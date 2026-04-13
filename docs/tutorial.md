# open following ports on server
22,80,8000,8080,7681

# upload the setup scripts and DB backup to server
scp -P 1512 scripts/setup_server.sh scripts/deploy.sh scripts/deploy_nodocker.sh scripts/backup_database.sh scripts/restore_database.sh root@n3.ckey.vn:/root/

ssh -p 1512 root@n3.ckey.vn "mkdir -p ~/PenalLawChatbot/database/backups"
scp -P 1512 ~/Desktop/Projects/PenalLawChatbot/database/backups/penallaw_combined_backup.sql \
    root@n3.ckey.vn:~/PenalLawChatbot/database/backups/

# run the installer 
chmod +x setup_server.sh deploy.sh
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
scp -P 2422 'root@n1.ckey.vn:~/PenalLawChatbot/database/backups/penallaw_backup_*.sql' ~/Desktop/Projects/PenalLawChatbot/database/backups/

## verify 
ls -lh ~/PenalLawChatbot/database/backups/

# monitor GPU usage
watch -n 1 nvidia-smi