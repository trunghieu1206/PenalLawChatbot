#!/bin/bash

# Database Backup Script (Non-Docker PostgreSQL)
# Works whether run from /root or /root/PenalLawChatbot/scripts/

set -e

# Always point to the project directory — adjust if yours differs
PROJECT_DIR="${PENALLAW_DIR:-/root/PenalLawChatbot}"
BACKUP_DIR="$PROJECT_DIR/database/backups"
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/penallaw_backup_$TIMESTAMP.sql"

# Create backup directory and ensure postgres can see it
mkdir -p "$BACKUP_DIR"
chmod 777 "$BACKUP_DIR"

echo "🔄 Starting database backup..."
echo "   Database: $DB_NAME"
echo "   User:     $DB_USER"
echo "   Output:   $BACKUP_FILE"
echo ""

# Check if PostgreSQL is running
if ! pg_isready -q 2>/dev/null; then
    echo "❌ Error: PostgreSQL is not running"
    echo "   Start with: pg_ctlcluster <version> main start"
    exit 1
fi

BACKUP_SUCCESS=0

# Method 1: su - postgres, dump to /tmp (postgres can always write there),
#            then move the file into place
TEMP_FILE=$(mktemp /tmp/pg_backup_XXXXXX.sql)
chmod 666 "$TEMP_FILE"   # let postgres write to it
if su - postgres -c "pg_dump $DB_NAME > '$TEMP_FILE'" 2>/tmp/pg_backup.err; then
    mv "$TEMP_FILE" "$BACKUP_FILE"
    BACKUP_SUCCESS=1
else
    rm -f "$TEMP_FILE"
fi

# Method 2: PGPASSWORD via TCP (no peer auth needed)
if [ $BACKUP_SUCCESS -eq 0 ] && [ -n "${POSTGRES_PASSWORD:-}" ]; then
    if PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$DB_USER" -h 127.0.0.1 "$DB_NAME" > "$BACKUP_FILE" 2>/tmp/pg_backup.err; then
        BACKUP_SUCCESS=1
    fi
fi

if [ $BACKUP_SUCCESS -eq 1 ]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    LINES=$(wc -l < "$BACKUP_FILE")
    echo "✅ Backup successful!"
    echo "   File size: $SIZE"
    echo "   SQL lines: $LINES"
    echo "   Location:  $BACKUP_FILE"
    echo ""
    echo "📦 Download to local machine:"
    echo "   scp -P 2219 'root@n3.ckey.vn:$BACKUP_FILE' ~/Desktop/Projects/PenalLawChatbot/database/backups/"
else
    echo "❌ Backup failed - check permissions"
    if [ -f /tmp/pg_backup.err ]; then
        echo ""
        echo "Error details:"
        cat /tmp/pg_backup.err
        rm -f /tmp/pg_backup.err
    fi
    echo ""
    echo "💡 Try manually:"
    echo "   PGPASSWORD=yourpass pg_dump -U postgres -h 127.0.0.1 $DB_NAME > $BACKUP_FILE"
    exit 1
fi
