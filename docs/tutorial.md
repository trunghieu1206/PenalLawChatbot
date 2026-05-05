# open following ports on server
22,80,8000,8080,7681

# ── Step 1: upload scripts + .env template + DB backup ───────────────────────
# Run these from your LOCAL machine (inside PenalLawChatbot/ directory):
scp -P 10000 scripts/setup_server.sh scripts/deploy.sh scripts/deploy_nodocker.sh scripts/backup_database.sh scripts/restore_database.sh root@74.81.39.6:/root/
scp -P 10000 .env.example root@74.81.39.6:/root/.env.example

# Create backup dir and upload DB backup (mkdir needed before clone runs)
ssh -p 10000 root@74.81.39.6 "mkdir -p ~/PenalLawChatbot/database/backups"
scp -P 10000 ~/Desktop/Projects/PenalLawChatbot/database/backups/penallaw_backup_20260505_150435.sql \
    root@74.81.39.6:~/PenalLawChatbot/database/backups/

# run the installer 
chmod +x setup_server.sh deploy_nodocker.sh
bash setup_server.sh

# restore database
bash restore_database.sh

# note: if the server itself is a Docker container run this script instead of setup_server.sh 
bash deploy_nodocker.sh

# then deploy the server
bash deploy.sh

# how to create backup db file and download to local
## create backup on server
cd ~/PenalLawChatbot
mkdir -p ./database/backups
cd ~
chmod 777 ./PenalLawChatbot/database/backups
./backup_database.sh

## download backup to local
scp -P 10000 'root@74.81.39.6:~/PenalLawChatbot/database/backups/penallaw_backup_*.sql' ~/Desktop/Projects/PenalLawChatbot/database/backups/

## verify 
ls -lh ~/PenalLawChatbot/database/backups/

# monitor GPU usage
watch -n 1 nvidia-smi