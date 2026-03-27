# Database Persistence Guide (Non-Docker Deployment)

## Overview

Your application stores data directly in PostgreSQL on the system. Database files are stored in PostgreSQL's data directory so they persist across server restarts. You can backup the database to a portable SQL file for restoration on new servers.

## Default PostgreSQL Setup

When `deploy_nodocker.sh` runs:
- PostgreSQL 16 is installed and started directly on the system
- Database: `penallaw` (created automatically)
- User: `postgres`
- Port: `5432` (localhost only)

## Backup & Restore Workflow

### 1. Backing Up Your Database

**Before deploying to a new server, create a backup:**

```bash
cd ~/PenalLawChatbot
./scripts/backup_database.sh
```

This creates a timestamped backup SQL file:
```
./database/backups/penallaw_backup_20240315_143022.sql
```

**Output example:**
```
✅ Backup successful!
   File size: 2.5M
   SQL lines: 45230
   Location: ./database/backups/penallaw_backup_20240315_143022.sql
```

### 2. Download the Backup File

**From your local machine:**
```bash
scp -P 1894 root@n1.ckey.vn:~/PenalLawChatbot/database/backups/penallaw_backup_*.sql ./local/backup/
```

**Keep multiple backups safe!** Backups are your insurance policy.

### 3. Restore on New Server

**Step 1:** Ensure setup completed on new server:
```bash
bash setup_server.sh          # Install dependencies
bash deploy_nodocker.sh       # Start services
```

**Step 2:** Upload backup file to new server:
```bash
scp -P 1894 ./local/backup/penallaw_backup_*.sql root@newserver:~/PenalLawChatbot/database/backups/
```

**Step 3:** Restore database:
```bash
cd ~/PenalLawChatbot
./scripts/restore_database.sh ./database/backups/penallaw_backup_20240315_143022.sql
```

You'll be prompted:
```
⚠️  WARNING: This will DROP the existing database and restore from backup!
   All current data will be lost. Continue? (yes/no)
```

Type `yes` to confirm.

**Step 4:** Restart services:
```bash
pkill -f 'java -jar'
pkill -f 'uvicorn'
bash deploy_nodocker.sh
```

## PostgreSQL Management

### Check If PostgreSQL is Running

```bash
pg_isready
# Output: accepting connections
```

### View PostgreSQL Logs

```bash
sudo tail -f /var/log/postgresql/postgresql-*.log
```

### Start/Stop PostgreSQL

**Start:**
```bash
sudo systemctl start postgresql
# OR (if no systemd):
sudo pg_ctlcluster 16 main start
```

**Stop:**
```bash
sudo systemctl stop postgresql
# OR:
sudo pg_ctlcluster 16 main stop
```

### Access PostgreSQL Directly

```bash
sudo -u postgres psql penallaw
# In psql:
#   \dt             — list tables
#   \d tablename    — describe table
#   SELECT COUNT(*) FROM users;    — check data
#   \q              — exit
```

### Database Location on Filesystem

PostgreSQL stores data in:
```
/var/lib/postgresql/16/main/
```

⚠️ **Do not manually modify these files!** Use `pg_dump` and `psql` instead.

## Important Notes

### What Gets Backed Up

✅ All PostgreSQL data:
- Users & authentication
- Chat sessions & messages
- Any application data

❌ NOT backed up (separate):
- Milvus vector embeddings (`VN_law_lora.db`)
- Source code
- Configuration files

### Backup Best Practices

1. **Regular backups** - Every week minimum
2. **Multiple copies** - Keep 3-4 recent backups
3. **Off-server storage** - Don't store backups only on the server
4. **Version control** - Document which backup is for which state

### Restore Notes

- **Destructive operation** - Restore drops existing database
- **Services need restart** - After restore, restart backend/AI service
- **Data loss confirmation** - Script requires explicit "yes" confirmation

## Troubleshooting

### PostgreSQL won't start
```bash
# Check status
sudo systemctl status postgresql

# View logs
sudo tail -f /var/log/postgresql/postgresql-*.log

# Force start if cluster corrupted
sudo pg_ctlcluster 16 main stop
sudo pg_dropcluster 16 main
sudo pg_createcluster 16 main
sudo pg_ctlcluster 16 main start
```

### Backup command fails
```bash
# Check if PostgreSQL is running
pg_isready

# Check permissions
ls -la ./database/backups/

# Try manual backup
sudo -u postgres pg_dump penallaw > ./database/backups/manual_backup.sql
```

### Restore command fails
```bash
# Verify backup file is valid SQL
file ./database/backups/penallaw_backup_*.sql
head -20 ./database/backups/penallaw_backup_*.sql

# Check PostgreSQL is running
pg_isready

# Try manual restore
sudo -u postgres psql penallaw < ./database/backups/penallaw_backup_*.sql
```

## Automated Daily Backups (Optional)

**Add to crontab** for automatic daily backups:
```bash
sudo crontab -e
```

Add this line (backs up daily at 2 AM):
```cron
0 2 * * * cd /root/PenalLawChatbot && ./scripts/backup_database.sh
```

View cron logs:
```bash
journalctl -u cron | tail -20
```

## Quick Reference

```bash
# Status check
pg_isready

# Backup database
./scripts/backup_database.sh

# List backups
ls -lh ./database/backups/

# Restore from backup
./scripts/restore_database.sh ./database/backups/penallaw_backup_20240315_143022.sql

# Manual backup (alternative)
sudo -u postgres pg_dump penallaw > manual_backup.sql

# Manual restore (alternative)
sudo -u postgres psql penallaw < manual_backup.sql

# Check database size
sudo -u postgres psql penallaw -c "SELECT pg_size_pretty(pg_database_size('penallaw'));"
```

## Migration Checklist

When moving to a new server:

- [ ] Create backup on old server: `./scripts/backup_database.sh`
- [ ] Download backup file to safe location
- [ ] Run `setup_server.sh` on new server
- [ ] Run `deploy_nodocker.sh` on new server
- [ ] Upload backup to new server
- [ ] Run restore script: `./scripts/restore_database.sh <backup_file>`
- [ ] Restart services: `pkill -f 'java -jar' && pkill -f 'uvicorn' && bash deploy_nodocker.sh`
- [ ] Test application: Visit `http://newserver`
- [ ] Verify data is present

## Contact & Support

For issues with deployment, check deployment logs:
```bash
tail -f /var/log/penallaw/postgres.log
tail -f /var/log/penallaw/ai-service.log

# Or if using journalctl
journalctl -u penallaw-backend -f
```
