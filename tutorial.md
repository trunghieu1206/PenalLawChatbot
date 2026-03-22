# open following ports on server
22,80,8000,8080,7681

# upload the setup scripts to server
scp -P 1894 scripts/setup_server.sh scripts/deploy.sh scripts/deploy_nodocker.sh root@n1.ckey.vn:/root/

# run the installer 
chmod +x setup_server.sh deploy.sh
bash setup_server.sh

# note: if the server itself is a Docker container run this script instead of setup_server.sh
bash deploy_nodocker.sh

# then deploy the server
bash deploy.sh