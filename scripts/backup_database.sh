#!/usr/bin/env bash
# =============================================================
# backup_database.sh
# Creates a timestamped PostgreSQL backup of the penallaw DB.
#
# Run on the server (requires root / postgres access):
#   sudo bash backup_database.sh
#
# Then download with:
#   scp -i "chatbot-key.pem" ubuntu@<EC2_HOST>:~/PenalLawChatbot/database/backups/penallaw_backup_*.sql ./database/backups/
# =============================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

# ── Configuration ─────────────────────────────────────────────
PROJECT_DIR="${PENALLAW_DIR:-/root/PenalLawChatbot}"
BACKUP_DIR="$PROJECT_DIR/database/backups"
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"

# Load .env if available (overrides defaults above)
if [ -f "$PROJECT_DIR/.env" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$PROJECT_DIR/.env"
    set +o allexport
    info "Loaded .env from $PROJECT_DIR"
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/penallaw_backup_${TIMESTAMP}.sql"

# ── Pre-flight checks ─────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "💾  Database Backup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Database : $DB_NAME"
info "User     : $DB_USER"
info "Output   : $BACKUP_FILE"
echo ""

if ! pg_isready -q 2>/dev/null; then
    error "PostgreSQL is not running. Start it with: sudo systemctl start postgresql"
fi

# Create backup directory (safe permissions — postgres user must be able to write)
mkdir -p "$BACKUP_DIR"
chmod 755 "$BACKUP_DIR"

# ── Perform backup ────────────────────────────────────────────
info "Running pg_dump..."
if sudo -u postgres pg_dump -U "$DB_USER" "$DB_NAME" > "$BACKUP_FILE"; then
    chmod 644 "$BACKUP_FILE"

    # Verify the file is non-empty
    SIZE_BYTES=$(stat -c%s "$BACKUP_FILE" 2>/dev/null || wc -c < "$BACKUP_FILE")
    if [ "${SIZE_BYTES:-0}" -lt 1024 ]; then
        rm -f "$BACKUP_FILE"
        error "Backup file is suspiciously small (${SIZE_BYTES} bytes) — aborting."
    fi

    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    LINES=$(wc -l < "$BACKUP_FILE")

    # Quick row-count sanity check for key tables
    VISITOR_ROWS=$(sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT count(*) FROM visitor_logs;" 2>/dev/null || echo "?")
    LAWS_ROWS=$(sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT count(*) FROM laws;" 2>/dev/null || echo "?")
    SESSION_ROWS=$(sudo -u postgres psql -d "$DB_NAME" -tAc "SELECT count(*) FROM chat_sessions;" 2>/dev/null || echo "?")

    echo ""
    info "✅ Backup successful!"
    echo ""
    echo "  File     : $BACKUP_FILE"
    echo "  Size     : $SIZE  ($LINES lines)"
    echo "  visitors : $VISITOR_ROWS rows"
    echo "  laws     : $LAWS_ROWS rows"
    echo "  sessions : $SESSION_ROWS rows"
    echo ""
    echo "📦 To download to your local machine:"
    echo "   scp -i \"chatbot-key.pem\" ubuntu@<EC2_HOST>:$BACKUP_FILE ./database/backups/"
    echo ""
else
    rm -f "$BACKUP_FILE"
    error "pg_dump failed — check PostgreSQL permissions."
fi
