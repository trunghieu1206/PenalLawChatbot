# upload the setup scripts to server
scp scripts/setup_server.sh scripts/deploy.sh root@YOUR_SERVER_IP:/root/

# run the installer 
chmod +x setup_server.sh deploy.sh
sudo bash setup_server.sh

# then deploy the server
bash deploy.sh