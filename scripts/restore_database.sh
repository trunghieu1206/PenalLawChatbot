#!/usr/bin/env bash
# =============================================================
# restore_database.sh
# Restores PostgreSQL database from the latest .sql backup.
#
# Can be run BEFORE or AFTER deploy_nodocker.sh.
# Works without Docker on containerized/bare-metal deployments.
#
# Usage:
#   bash restore_database.sh
# =============================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
skip()  { echo -e "${YELLOW}[SKIP]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

# ── Configuration ────────────────────────────────────────────
PROJECT_DIR="${PENALLAW_DIR:-/root/PenalLawChatbot}"
BACKUP_DIR="$PROJECT_DIR/database/backups"
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASS="${POSTGRES_PASSWORD:-postgres}"

# Load .env if available (overrides defaults above)
if [ -f "$PROJECT_DIR/.env" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$PROJECT_DIR/.env"
    set +o allexport
    info "Loaded .env from $PROJECT_DIR"
fi

LOG_DIR="/var/log/penallaw"
mkdir -p "$LOG_DIR"
chmod 777 "$LOG_DIR" 2>/dev/null || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔄  Database Restore"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Find backup file ─────────────────────────────────────────
if [ ! -d "$BACKUP_DIR" ]; then
    error "Backup directory not found: $BACKUP_DIR
    Upload your backup with:
      scp ./database/backups/penallaw_combined_backup.sql root@SERVER:$BACKUP_DIR/"
fi

info "Looking for backups in: $BACKUP_DIR"

# Prefer combined backup (laws + users/sessions), fall back to timestamped backup
if [ -f "$BACKUP_DIR/penallaw_combined_backup.sql" ]; then
    BACKUP_FILE="$BACKUP_DIR/penallaw_combined_backup.sql"
    info "Found combined backup (laws + chat data): penallaw_combined_backup.sql"
else
    BACKUP_FILE=$(ls -t "$BACKUP_DIR"/penallaw_backup_*.sql 2>/dev/null | head -n 1 || echo "")
fi

if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
    error "No backup files found in $BACKUP_DIR
    Expected: penallaw_combined_backup.sql  or  penallaw_backup_*.sql"
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
BACKUP_TIME=$(stat -c '%y' "$BACKUP_FILE" 2>/dev/null | cut -d. -f1 || echo "unknown")
info "Backup file: $(basename "$BACKUP_FILE")  ($BACKUP_SIZE | $BACKUP_TIME)"
echo ""

# ── Ensure PostgreSQL is installed and running ────────────────
info "Ensuring PostgreSQL is installed..."
if ! command -v psql &>/dev/null; then
    info "PostgreSQL not found — installing..."
    DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        postgresql postgresql-contrib libpq-dev >/dev/null 2>&1 || \
        error "Failed to install PostgreSQL"
    info "PostgreSQL installed: $(psql --version)"
else
    skip "PostgreSQL: $(psql --version)"
fi

info "Starting PostgreSQL if needed..."
PG_VERSION=$(ls /etc/postgresql/ 2>/dev/null | sort -V | tail -1 || echo "")
if [ -z "$PG_VERSION" ]; then
    error "PostgreSQL config directory not found. Installation may have failed."
fi

if ! pg_isready -q 2>/dev/null; then
    warn "PostgreSQL not running — starting..."
    pg_dropcluster --stop "$PG_VERSION" main 2>/dev/null || true
    pg_createcluster "$PG_VERSION" main 2>/dev/null || true
    pg_ctlcluster "$PG_VERSION" main start -- -l "/tmp/pg_restore_$$.log" 2>&1 || true
    sleep 5
fi

# Final readiness check
if ! pg_isready -q 2>/dev/null; then
    error "PostgreSQL still not responding after startup. Check: pg_ctlcluster $PG_VERSION main status"
fi
info "PostgreSQL is running."
echo ""

# Set password for postgres user
su - postgres -c "psql -c \"ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';\"" 2>/dev/null || true

# ── Check current database state ─────────────────────────────
info "Checking if database '$DB_NAME' exists..."
DB_EXISTS=$(su -c "cd /tmp && psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$DB_NAME';\"" postgres 2>/dev/null || echo "")
TABLE_COUNT=0

if [ -n "$DB_EXISTS" ]; then
    TABLE_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public';\"" postgres 2>/dev/null || echo "0")
    if [ "${TABLE_COUNT:-0}" -gt 0 ]; then
        LAWS_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM laws;\"" postgres 2>/dev/null || echo "0")
        warn "Database '$DB_NAME' already has $TABLE_COUNT tables (laws: ${LAWS_COUNT:-0})."
        echo ""
        echo "⚠️  Restoring will DROP and recreate the entire database!"
        echo "   All current data in '$DB_NAME' will be LOST."
        echo ""
        echo "   Type 'yes' to continue, or Ctrl+C to cancel:"
        read -r -t 30 confirm || confirm="no"
        if [ "$confirm" != "yes" ]; then
            warn "Restore cancelled."
            exit 0
        fi
    fi
fi

# ── Drop & recreate database ──────────────────────────────────
info "Dropping and recreating database '$DB_NAME'..."
su - postgres -c "psql -c \"DROP DATABASE IF EXISTS $DB_NAME;\"" 2>/dev/null || true
su - postgres -c "psql -c \"CREATE DATABASE $DB_NAME;\"" 2>/dev/null || true
info "Database '$DB_NAME' created."
echo ""

# ── Restore from backup ───────────────────────────────────────
info "Importing $(basename "$BACKUP_FILE") ($BACKUP_SIZE)..."
START_TIME=$(date +%s)

TEMP_BACKUP="/tmp/penallaw_restore_$$.sql"
cp "$BACKUP_FILE" "$TEMP_BACKUP"
chmod 644 "$TEMP_BACKUP"

RESTORE_LOG="/tmp/penallaw_restore_log_$$.txt"
if su - postgres -c "psql $DB_NAME < \"$TEMP_BACKUP\" 2>&1" > "$RESTORE_LOG" 2>&1; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    RESTORED_TABLES=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public';\"" postgres 2>/dev/null || echo "?")
    LAWS_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM laws;\"" postgres 2>/dev/null || echo "0")

    info "✅ Restore completed in ${DURATION}s"
    echo ""
    echo "  Database : $DB_NAME"
    echo "  Source   : $(basename "$BACKUP_FILE")"
    echo "  Size     : $BACKUP_SIZE"
    echo "  Tables   : ${RESTORED_TABLES:-?}"
    echo "  Laws     : ${LAWS_COUNT:-?} articles"
else
    RESTORE_ERR=$(grep -i "error" "$RESTORE_LOG" 2>/dev/null | head -3 || echo "See $RESTORE_LOG")
    rm -f "$TEMP_BACKUP" "$RESTORE_LOG"
    error "Restore failed: $RESTORE_ERR"
fi

rm -f "$TEMP_BACKUP" "$RESTORE_LOG"

# ── Apply missing columns from recent migrations ──────────────
# sentencing_data column added in Tri-Path RAG refactor.
# Hibernate ddl-auto=update will handle this automatically on backend start,
# but we run it here too so manual psql checks don't fail.
info "Ensuring schema is up to date (idempotent ALTER TABLE)..."
su - postgres -c "psql $DB_NAME -c \"ALTER TABLE IF EXISTS chat_messages ADD COLUMN IF NOT EXISTS sentencing_data TEXT;\"" 2>/dev/null || true
info "Schema check done."

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅  Database Ready!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next step:  bash deploy_nodocker.sh"
echo ""
