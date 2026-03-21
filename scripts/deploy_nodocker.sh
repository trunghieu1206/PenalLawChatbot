#!/usr/bin/env bash
# =============================================================
# deploy_nodocker.sh
# Deploys all services DIRECTLY (no Docker) on a containerized
# GPU server (Ubuntu 22.04, root, no systemd, no iptables).
#
# Services started:
#   - PostgreSQL (via apt, run directly)
#   - AI Service (uvicorn, port 8000)
#   - Spring Boot backend (java -jar, port 8080)
#   - Frontend (nginx static, port 80)
#
# Usage:
#   bash deploy_nodocker.sh
# =============================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

REPO_URL="https://github.com/trunghieu1206/PenalLawChatbot"
PROJECT_DIR="/root/PenalLawChatbot"
LOG_DIR="/var/log/penallaw"
mkdir -p "$LOG_DIR"
chmod 777 "$LOG_DIR"  # postgres user needs write access

# ── 1. Clone / pull repo ─────────────────────────────────────
if [ -d "$PROJECT_DIR" ]; then
    warn "Repo exists — pulling latest..."
    git -C "$PROJECT_DIR" pull
else
    info "Cloning repo..."
    git clone "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"

# ── 2. Setup .env ────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn ">>> Fill in .env before continuing <<<"
    warn "    nano /root/PenalLawChatbot/.env"
    warn "Required: OPENROUTER_API_KEY, HF_TOKEN, JWT_SECRET"
    read -p "Press ENTER after editing .env..." _
fi
source .env
[ -z "${OPENROUTER_API_KEY:-}" ] && error "OPENROUTER_API_KEY not set"
[ -z "${OPENROUTER_API_KEY:-}" ] && error "HF_TOKEN not set"
info ".env loaded."

# ── 3. PostgreSQL ─────────────────────────────────────────────
info "Setting up PostgreSQL..."
DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql postgresql-contrib

# Start postgres manually (no systemctl)
PG_VERSION=$(ls /etc/postgresql/ | sort -V | tail -1)

# Use Ubuntu's cluster tools (handles conf files correctly)
if ! pg_isready -q 2>/dev/null; then
    # Drop and recreate cluster cleanly if it exists but isn't initialized
    pg_dropcluster --stop "$PG_VERSION" main 2>/dev/null || true
    pg_createcluster "$PG_VERSION" main 2>/dev/null || true

    PG_LOG="$LOG_DIR/postgres.log"
    touch "$PG_LOG" && chmod 666 "$PG_LOG"

    info "Starting PostgreSQL $PG_VERSION..."
    pg_ctlcluster "$PG_VERSION" main start -- -l "$PG_LOG"
    sleep 3
fi
pg_isready || error "PostgreSQL failed to start. Check $LOG_DIR/postgres.log"
info "PostgreSQL is running."

# Create database and user
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASS="${POSTGRES_PASSWORD:-postgres}"

su -c "cd /tmp && psql -c \"ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';\"" postgres 2>/dev/null || true
su -c "cd /tmp && createdb $DB_NAME" postgres 2>/dev/null || warn "Database '$DB_NAME' already exists."
info "Database '$DB_NAME' ready."

# ── 4. Check Milvus DB ────────────────────────────────────────
DB_PATH="$PROJECT_DIR/ai-service/VN_law_lora.db"
if [ ! -f "$DB_PATH" ]; then
    warn "VN_law_lora.db not found — upload it:"
    warn "  scp -P 1894 VN_law_lora.db root@n1.ckey.vn:$DB_PATH"
fi

# ── 5. AI Service (FastAPI / uvicorn) ────────────────────────
info "Starting AI service on port 8000..."
cd "$PROJECT_DIR/ai-service"

# Write .env for ai-service
cat > .env <<EOF
OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
HF_TOKEN=${HF_TOKEN:-}
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=${POSTGRES_DB:-penallaw}
POSTGRES_USER=${POSTGRES_USER:-postgres}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-postgres}
MILVUS_DB_PATH=${PROJECT_DIR}/ai-service/VN_law_lora.db
COLLECTION_NAME=legal_rag_lora
LLM_MODEL=${LLM_MODEL:-google/gemini-2.5-flash}
TOP_K=15
EMBEDDING_ADAPTER=trunghieu1206/lawchatbot-40k
EOF

# Kill existing
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1

# Ensure MILVUS_URI is NOT in the shell environment when uvicorn starts
# (pymilvus reads it at import time and crashes if it contains a file path)
unset MILVUS_URI

nohup uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    > "$LOG_DIR/ai-service.log" 2>&1 &
info "AI service started (PID $!). Log: $LOG_DIR/ai-service.log"

# ── 6. Backend (Spring Boot) ─────────────────────────────────
info "Building Spring Boot backend..."
cd "$PROJECT_DIR/backend"

# Inject env into application.yml override
export SPRING_DATASOURCE_URL="jdbc:postgresql://127.0.0.1:5432/${POSTGRES_DB:-penallaw}"
export SPRING_DATASOURCE_USERNAME="${POSTGRES_USER:-postgres}"
export SPRING_DATASOURCE_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
export JWT_SECRET="${JWT_SECRET:-changeme}"
export AI_SERVICE_URL="http://localhost:8000"

mvn package -DskipTests -q
JAR=$(ls target/*.jar | head -1)

pkill -f "$JAR" 2>/dev/null || true
sleep 1

nohup java -jar "$JAR" \
    --server.port=8080 \
    --spring.main.allow-circular-references=true \
    --spring.datasource.url="$SPRING_DATASOURCE_URL" \
    --spring.datasource.username="$SPRING_DATASOURCE_USERNAME" \
    --spring.datasource.password="$SPRING_DATASOURCE_PASSWORD" \
    --app.jwt.secret="$JWT_SECRET" \
    --ai.service.url="$AI_SERVICE_URL" \
    > "$LOG_DIR/backend.log" 2>&1 &
info "Backend started (PID $!). Log: $LOG_DIR/backend.log"

# ── 7. Frontend (Nginx static) ───────────────────────────────
info "Building frontend..."
cd "$PROJECT_DIR/frontend"
npm install --silent
npm run build

# Configure nginx to serve the built files
cat > /etc/nginx/sites-available/penallaw <<'NGINX'
server {
    listen 80;
    server_name _;
    root /root/PenalLawChatbot/frontend/dist;
    index index.html;

    # Serve React SPA
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy /ai-api → FastAPI
    location /ai-api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_read_timeout 130s;
        proxy_send_timeout 130s;
    }

    # Proxy /api → Spring Boot
    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_read_timeout 130s;
        proxy_send_timeout 130s;
    }
}
NGINX

# Fix permissions — nginx runs as www-data which can't enter /root by default
chmod 755 /root
chmod -R 755 "$PROJECT_DIR/frontend/dist/"

# Enable site
ln -sf /etc/nginx/sites-available/penallaw /etc/nginx/sites-enabled/penallaw
rm -f /etc/nginx/sites-enabled/default

# Start nginx (no systemctl)
pkill nginx 2>/dev/null || true
sleep 1
nginx
info "Nginx started."

# ── 8. Health checks ─────────────────────────────────────────
info "Waiting 20s for services to start..."
sleep 20

check() {
    local name=$1 url=$2
    if curl -sf "$url" > /dev/null 2>&1; then
        info "  ✅ $name is up"
    else
        warn "  ⚠️  $name not responding yet — check $LOG_DIR/"
    fi
}

check "AI Service"  "http://localhost:8000/health"
check "Backend"     "http://localhost:8080/actuator/health"
check "Frontend"    "http://localhost:80"

echo ""
info "=============================================="
info "✅  Deployed (bare-metal, no Docker)"
info "=============================================="
info "  Frontend  → http://n1.ckey.vn:1895"
info "  AI API    → http://n1.ckey.vn:1897/docs"
info "  Backend   → http://n1.ckey.vn:1898"
info ""
info "Logs:"
info "  tail -f $LOG_DIR/ai-service.log"
info "  tail -f $LOG_DIR/backend.log"
info "  tail -f $LOG_DIR/postgres.log"
