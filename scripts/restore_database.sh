#!/bin/bash

# Database Restore Script (Non-Docker PostgreSQL)
# Restores a PostgreSQL database from a backup file

set -e

# Configuration
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"

# Check if backup file is provided
if [ -z "$1" ]; then
    echo "❌ Error: Backup file not provided"
    echo ""
    echo "Usage: ./restore_database.sh <backup_file>"
    echo ""
    echo "Example:"
    echo "   ./restore_database.sh ./database/backups/penallaw_backup_20240101_120000.sql"
    echo ""
    echo "💡 List available backups:"
    echo "   ls -lh ./database/backups/"
    exit 1
fi

BACKUP_FILE="$1"

# Check if backup file exists
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Error: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "🔄 Starting database restore..."
echo "   Backup file: $BACKUP_FILE"
echo "   Database: $DB_NAME"
echo "   User: $DB_USER"
echo ""
echo "⚠️  WARNING: This will DROP the existing database and restore from backup!"
echo "   All current data will be lost. Continue? (yes/no)"
read -r confirm

if [ "$confirm" != "yes" ]; then
    echo "❌ Restore cancelled"
    exit 0
fi

# Check if PostgreSQL is running
if ! pg_isready -q 2>/dev/null; then
    echo "❌ Error: PostgreSQL is not running"
    echo "   Start PostgreSQL with: sudo systemctl start postgresql"
    echo "   Or: sudo pg_ctlcluster <version> main start"
    exit 1
fi

echo "🗑️  Dropping existing database..."
if su -c "dropdb --if-exists $DB_NAME" postgres 2>/dev/null; then
    echo "   ✓ Database dropped (or didn't exist)"
else
    echo "   ⚠️  Could not drop database (may already be gone)"
fi

echo "📥 Creating new database..."
if su -c "createdb $DB_NAME" postgres 2>/dev/null; then
    echo "   ✓ Database created"
else
    echo "   ❌ Failed to create database"
    exit 1
fi

echo "📝 Restoring from backup..."
if su -c "psql $DB_NAME < '$BACKUP_FILE'" postgres 2>/dev/null; then
    echo "   ✓ Restore completed"
else
    echo "   ❌ Restore failed - check SQL file"
    exit 1
fi

echo ""
echo "✅ Restore successful!"
echo "   Database: $DB_NAME"
echo "   Source: $BACKUP_FILE"
echo ""
echo "💡 You may need to restart services:"
echo "   pkill -f 'java -jar' && pkill -f uvicorn"
echo "   Then redeploy with: bash deploy_nodocker.sh"
