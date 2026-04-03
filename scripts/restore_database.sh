#!/bin/bash

# Database Restore Script (Auto-Latest Backup)
# Restores PostgreSQL database from the latest .sql backup file.
# Can be run BEFORE deploy_nodocker.sh for clean fresh deployments,
# or AFTER for manual database resets.
# Works without Docker on containerized/bare-metal deployments.

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
skip()  { echo -e "${YELLOW}[SKIP]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

# Configuration
PROJECT_DIR="${PENALLAW_DIR:-/root/PenalLawChatbot}"
BACKUP_DIR="$PROJECT_DIR/database/backups"
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASS="${POSTGRES_PASSWORD:-postgres}"
LOG_DIR="/var/log/penallaw"
mkdir -p "$LOG_DIR"
chmod 777 "$LOG_DIR" 2>/dev/null || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔄  Database Restore (Latest Backup)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Find the latest backup file
if [ ! -d "$BACKUP_DIR" ]; then
    error "Backup directory not found: $BACKUP_DIR"
fi

info "Looking for backups in: $BACKUP_DIR"
BACKUP_FILE=$(ls -t "$BACKUP_DIR"/penallaw_backup_*.sql 2>/dev/null | head -n 1 || echo "")

if [ -z "$BACKUP_FILE" ]; then
    error "No backup files found in $BACKUP_DIR"
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
BACKUP_TIME=$(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$BACKUP_FILE" 2>/dev/null || stat -c '%y' "$BACKUP_FILE" 2>/dev/null | cut -d. -f1 || echo "")

info "Latest backup: $(basename "$BACKUP_FILE")"
echo "   Size: $BACKUP_SIZE | Modified: $BACKUP_TIME"
echo ""

# ── Ensure PostgreSQL is installed and started ────────────────
info "Ensuring PostgreSQL is installed..."
if ! command -v psql &>/dev/null || ! command -v pg_isready &>/dev/null; then
    error "PostgreSQL is not installed. Run deploy_nodocker.sh first or:"
    echo "   apt-get install -y postgresql postgresql-contrib libpq-dev"
    exit 1
fi
skip "PostgreSQL tools available"

info "Starting PostgreSQL (if needed)..."
PG_VERSION=$(ls /etc/postgresql/ 2>/dev/null | sort -V | tail -1 || echo "")
if [ -z "$PG_VERSION" ]; then
    error "PostgreSQL version directory not found in /etc/postgresql/"
fi

if ! pg_isready -q 2>/dev/null; then
    warn "PostgreSQL not running — starting..."
    pg_dropcluster --stop "$PG_VERSION" main 2>/dev/null || true
    pg_createcluster "$PG_VERSION" main 2>/dev/null || true
    
    PG_LOG="$LOG_DIR/postgres_restore.log"
    touch "$PG_LOG" 2>/dev/null || PG_LOG="/tmp/postgres_restore.log"
    
    pg_ctlcluster "$PG_VERSION" main start -- -l "$PG_LOG" 2>&1 || true
    sleep 3
    
    if ! pg_isready -q 2>/dev/null; then
        warn "PostgreSQL startup returned, but pg_isready not responding yet. Waiting a bit more..."
        sleep 3
    fi
fi

if pg_isready -q 2>/dev/null; then
    info "PostgreSQL is running"
else
    error "PostgreSQL still not responding after startup. Manual check needed."
fi
echo ""

# ── Check database status ────────────────────────────────────
info "Checking if database '$DB_NAME' exists..."
DB_EXISTS=$(su -c "cd /tmp && psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$DB_NAME';\"" postgres 2>/dev/null || echo "")

if [ -n "$DB_EXISTS" ]; then
    # Database exists — check if it has tables
    TABLE_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public';\"" postgres 2>/dev/null || echo "0")
    
    if [ "${TABLE_COUNT:-0}" -gt 0 ]; then
        warn "Database '$DB_NAME' already exists with $TABLE_COUNT tables"
        echo ""
        echo "⚠️   WARNING: Restoring will DROP and recreate the database!"
        echo "     All current data in '$DB_NAME' will be LOST."
        echo ""
        echo "     Press Ctrl+C to cancel, or enter 'yes' to continue:"
        read -r -t 30 confirm || confirm="no"
        
        if [ "$confirm" != "yes" ]; then
            warn "Restore cancelled by user"
            exit 0
        fi
        
        info "Dropping existing database..."
        su -c "cd /tmp && dropdb --if-exists $DB_NAME" postgres 2>/dev/null || true
    else
        warn "Database exists but is empty"
    fi
else
    info "Database does not exist yet"
fi

echo ""

# Create fresh database
info "Creating database '$DB_NAME'..."
su -c "cd /tmp && createdb $DB_NAME" postgres 2>/dev/null || true

# Set password
info "Setting password for user '$DB_USER'..."
su -c "cd /tmp && psql -c \"ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';\"" postgres 2>/dev/null || true

echo ""
info "Importing backup file (this may take a few moments)..."
START_TIME=$(date +%s)

# Perform the restore
if su -c "cd /tmp && psql $DB_NAME < \"$BACKUP_FILE\"" postgres \
    >> "$LOG_DIR/restore.log" 2>&1; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    info "✅ Restore completed successfully"
    echo ""
    echo "  Database:  $DB_NAME"
    echo "  Source:    $(basename "$BACKUP_FILE")"
    echo "  Size:      $BACKUP_SIZE"
    echo "  Time:      ${DURATION}s"
else
    warn "Restore had warnings — check log: $LOG_DIR/restore.log"
    # Don't fail completely — database might still be usable
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅  Database Ready!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next steps:"
echo "  1. Run: bash deploy_nodocker.sh"
echo ""
