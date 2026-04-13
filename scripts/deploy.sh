#!/usr/bin/env bash
# =============================================================
# deploy.sh
# Clone project, configure env, and start all services.
# Works on containerized GPU servers (no systemd).
#
# Usage (already root, after setup_server.sh):
#   bash deploy.sh
# =============================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

# ── CONFIG ───────────────────────────────────────────────────
REPO_URL="https://github.com/trunghieu1206/PenalLawChatbot"
PROJECT_DIR="/root/PenalLawChatbot"

# ── 0. Ensure dockerd is running (no systemd in container) ───
if ! docker ps &>/dev/null; then
    info "Starting Docker daemon (dockerd)..."
    dockerd > /root/dockerd.log 2>&1 &
    info "Waiting for Docker to be ready..."
    for i in $(seq 1 30); do
        docker ps &>/dev/null && break
        sleep 1
    done
    docker ps &>/dev/null || error "Docker failed to start. Check: cat /root/dockerd.log"
    info "Docker is ready."
else
    info "Docker is already running."
fi

# ── 1. Clone / pull repo (PRODUCTION: master branch only) ─
if [ -d "$PROJECT_DIR" ]; then
    warn "Project directory exists. Pulling latest from master..."
    git -C "$PROJECT_DIR" checkout master
    git -C "$PROJECT_DIR" pull origin master
else
    info "Cloning repository (master branch)..."
    git clone --branch master "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"
info "Currently on branch: $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)"
info "Latest commit: $(git -C "$PROJECT_DIR" log -1 --oneline)"

# ── 2. Create .env ───────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn ">>> IMPORTANT: Fill in your API keys in .env <<<"
    warn "    nano .env"
    warn ""
    warn "Required:"
    warn "  OPENROUTER_API_KEY=..."
    warn "  HF_TOKEN=..."
    warn "  JWT_SECRET=$(openssl rand -base64 32)"
    echo ""
    read -p "Press ENTER after editing .env..." _
fi

# Validate
source .env
[ -z "${OPENROUTER_API_KEY:-}" ] && error "OPENROUTER_API_KEY not set in .env"
[ -z "${HF_TOKEN:-}"           ] && error "HF_TOKEN not set in .env"
[ -z "${JWT_SECRET:-}"         ] && error "JWT_SECRET not set in .env"
info ".env validated."

# ── 3. Check Milvus DB file ──────────────────────────────────
DB_PATH="$PROJECT_DIR/ai-service/VN_law_lora.db"
if [ ! -f "$DB_PATH" ]; then
    warn "VN_law_lora.db not found — AI RAG won't work until you upload it:"
    warn "  scp -P 1894 VN_law_lora.db root@n1.ckey.vn:$DB_PATH"
fi

# ── 4. Build & start ────────────────────────────────────────
info "Building and starting all services (this may take a while)..."
docker compose up --build -d

info "Waiting 15s for services to initialize..."
sleep 15

# ── 5. Health checks ─────────────────────────────────────────
info "Running health checks..."
check_service() {
    local name=$1 url=$2
    if curl -sf "$url" > /dev/null 2>&1; then
        info "  ✅ $name is up"
    else
        warn "  ⚠️  $name not ready yet — run: docker compose logs"
    fi
}

check_service "AI Service"  "http://localhost:8000/health"
check_service "Backend API" "http://localhost:8080/actuator/health"
check_service "Frontend"    "http://localhost:80"

# ── 6. Done ──────────────────────────────────────────────────
echo ""
info "=============================================="
info "✅  Deployment complete!"
info "=============================================="
info "Access via mapped ports:"
info "  Frontend  → http://n1.ckey.vn:1895"
info "  AI API    → http://n1.ckey.vn:1897/docs"
info "  Backend   → http://n1.ckey.vn:1898"
info ""
info "Useful commands:"
info "  docker compose logs -f ai-service    # AI service logs"
info "  docker compose logs -f backend       # backend logs"
info "  docker compose ps                    # service status"
info "  docker compose down                  # stop everything"
