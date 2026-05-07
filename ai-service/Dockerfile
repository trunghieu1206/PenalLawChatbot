FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# curl is required for the docker-compose healthcheck (GET /health)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# BUG-FIX: --timeout-keep-alive=0 prevents uvicorn from closing idle connections
# prematurely during long CPU inference (5-10 min). log-level=info is suitable for prod.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "0", "--log-level", "info"]
