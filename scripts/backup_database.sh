#!/bin/bash

# Database Backup Script (Non-Docker PostgreSQL)
# Creates a PostgreSQL backup file that can be transferred between servers

set -e

# Configuration
BACKUP_DIR="./database/backups"
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/penallaw_backup_$TIMESTAMP.sql"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "🔄 Starting database backup..."
echo "   Database: $DB_NAME"
echo "   User: $DB_USER"
echo "   Backup file: $BACKUP_FILE"
echo ""

# Check if PostgreSQL is running
if ! pg_isready -q 2>/dev/null; then
    echo "❌ Error: PostgreSQL is not running"
    echo "   Start PostgreSQL with: sudo systemctl start postgresql"
    echo "   Or: sudo pg_ctlcluster <version> main start"
    exit 1
fi

# Perform backup using pg_dump
if su -c "pg_dump -U $DB_USER $DB_NAME > '$BACKUP_FILE'" postgres 2>/dev/null; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    LINES=$(wc -l < "$BACKUP_FILE")
    echo "✅ Backup successful!"
    echo "   File size: $SIZE"
    echo "   SQL lines: $LINES"
    echo "   Location: $BACKUP_FILE"
    echo ""
    echo "📦 To download this backup:"
    echo "   - Download: $BACKUP_FILE"
    echo "   - Keep it safe for restoring on another server"
    echo ""
    echo "💡 Example SCP download:"
    echo "   scp -P <port> user@server:~/PenalLawChatbot/$BACKUP_FILE /local/path/"
else
    echo "❌ Backup failed - check PostgreSQL permissions"
    exit 1
fi
