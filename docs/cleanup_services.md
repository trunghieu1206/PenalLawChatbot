# clean up backend 
sudo pkill -f "backend-1.0.0.jar"
sudo pkill -f java

# clean up ai-service
sudo pkill -f uvicorn

# clean up frontend
sudo pkill -f "serve -s"


----------------------------
# clean up backend 
pkill -f "backend-1.0.0.jar"
pkill -f java

# clean up ai-service
pkill -f uvicorn

# clean up frontend
pkill -f "serve -s"