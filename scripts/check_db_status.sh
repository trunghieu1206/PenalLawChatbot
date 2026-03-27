#!/bin/bash

# Database Status & Health Check Script

echo "📊 PenalLaw Database Status Check"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

check_status() {
    local name=$1
    local command=$2
    echo -n "$name: "
    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC}"
    else
        echo -e "${RED}✗${NC}"
    fi
}

# PostgreSQL Status
echo ""
echo "🐘 PostgreSQL"
check_status "  Running" "pg_isready -q"

if pg_isready -q 2>/dev/null; then
    DB_SIZE=$(sudo -u postgres psql penallaw -t -c "SELECT pg_size_pretty(pg_database_size('penallaw'));" 2>/dev/null | xargs)
    TABLES=$(sudo -u postgres psql penallaw -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | xargs)
    
    echo "  Database Size: $DB_SIZE"
    echo "  Tables: $TABLES"
    
    # Check table row counts
    USERS=$(sudo -u postgres psql penallaw -t -c "SELECT COUNT(*) FROM users;" 2>/dev/null | xargs)
    SESSIONS=$(sudo -u postgres psql penallaw -t -c "SELECT COUNT(*) FROM chat_sessions;" 2>/dev/null | xargs)
    MESSAGES=$(sudo -u postgres psql penallaw -t -c "SELECT COUNT(*) FROM chat_messages;" 2>/dev/null | xargs)
    
    echo "  Records:"
    echo "    - Users: $USERS"
    echo "    - Chat Sessions: $SESSIONS"
    echo "    - Chat Messages: $MESSAGES"
fi

# Services Status
echo ""
echo "🚀 Running Services"
check_status "  Spring Boot (8080)" "lsof -i :8080 2>/dev/null | grep -q LISTEN"
check_status "  AI Service (8000)" "lsof -i :8000 2>/dev/null | grep -q LISTEN"
check_status "  Frontend (80)" "lsof -i :80 2>/dev/null | grep -q LISTEN"
check_status "  PostgreSQL (5432)" "lsof -i :5432 2>/dev/null | grep -q LISTEN"

# Backup Status
echo ""
echo "💾 Backups"
BACKUP_COUNT=$(find ./database/backups -name "*.sql" 2>/dev/null | wc -l)
echo "  Backup files: $BACKUP_COUNT"

if [ $BACKUP_COUNT -gt 0 ]; then
    LATEST=$(ls -t ./database/backups/*.sql 2>/dev/null | head -1)
    LATEST_SIZE=$(du -h "$LATEST" 2>/dev/null | cut -f1)
    LATEST_AGE=$(python3 -c "import os, time; print(int((time.time() - os.path.getmtime('$LATEST')) / 3600)); " 2>/dev/null)
    
    if [ ! -z "$LATEST_AGE" ]; then
        echo "  Latest: $(basename $LATEST) ($LATEST_SIZE, $LATEST_AGE hours old)"
    fi
fi

# Log Status
echo ""
echo "📝 Log Files"
for log in /var/log/penallaw/*.log; do
    if [ -f "$log" ]; then
        SIZE=$(du -h "$log" | cut -f1)
        LINES=$(wc -l < "$log" 2>/dev/null)
        echo "  $(basename $log): $LINES lines, $SIZE"
    fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Status check complete"
