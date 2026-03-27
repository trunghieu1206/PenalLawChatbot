# clean up backend 
pkill -f "backend-1.0.0.jar"
pkill -f java

# clean up ai-service
pkill -f uvicorn

# clean up frontend
pkill -f "serve -s"