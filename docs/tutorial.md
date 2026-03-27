# open following ports on server
22,80,8000,8080,7681

# upload the setup scripts to server
scp -P 2219 scripts/setup_server.sh scripts/deploy.sh scripts/deploy_nodocker.sh root@n3.ckey.vn:/root/

# run the installer 
chmod +x setup_server.sh deploy.sh
bash setup_server.sh

# note: if the server itself is a Docker container run this script instead of setup_server.sh
bash deploy_nodocker.sh

# then deploy the server
bash deploy.sh

# how to create backup db file and download to local
## create backup on server
cd ~/PenalLawChatbot
./scripts/backup_database.sh

## download backup to local
scp -P 1894 root@n1.ckey.vn:~/PenalLawChatbot/database/backups/penallaw_backup_*.sql ~/Desktop/db-backups/

# how to upload backup db file to server
ssh -p 1894 root@newserver.com "mkdir -p ~/PenalLawChatbot/database/backups"

scp -P 1894 ~/Desktop/db-backups/penallaw_backup_20240315_143022.sql root@newserver.com:~/PenalLawChatbot/database/backups/

## verify 
ls -lh ~/PenalLawChatbot/database/backups/

# restore db on new server
./scripts/restore_database.sh ./database/backups/penallaw_backup_20240315_143022.sql