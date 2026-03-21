#!/usr/bin/env bash
# =============================================================
# deploy.sh
# Clone project, transfer data files, configure env, and start
# all services with Docker Compose.
#
# Usage (on the GPU server, after running setup_server.sh):
#   bash deploy.sh
# =============================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

# ── CONFIG — edit these before running ──────────────────────
REPO_URL="https://github.com/YOUR_USERNAME/PenalLawChatbot.git"
PROJECT_DIR="/root/PenalLawChatbot"
# ────────────────────────────────────────────────────────────

# ── 1. Clone repo ───────────────────────────────────────────
if [ -d "$PROJECT_DIR" ]; then
    warn "Project directory exists. Pulling latest changes..."
    git -C "$PROJECT_DIR" pull
else
    info "Cloning repository..."
    git clone "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"

# ── 2. Create .env from example ─────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn "Created .env from .env.example."
    warn ">>> IMPORTANT: Edit .env now and fill in your API keys! <<<"
    warn "    nano .env"
    warn ""
    warn "Required values:"
    warn "  OPENROUTER_API_KEY=..."
    warn "  HF_TOKEN=..."
    warn "  JWT_SECRET=...  (use: openssl rand -base64 64)"
    echo ""
    read -p "Press ENTER after you have edited .env to continue..." _
fi

# Validate required env vars
source .env
[ -z "${OPENROUTER_API_KEY:-}" ] && error "OPENROUTER_API_KEY is not set in .env"
[ -z "${HF_TOKEN:-}"           ] && error "HF_TOKEN is not set in .env"
[ -z "${JWT_SECRET:-}"         ] && error "JWT_SECRET is not set in .env"
info ".env validated."

# ── 3. Check data files ─────────────────────────────────────
DB_PATH="$PROJECT_DIR/ai-service/VN_law_lora.db"
if [ ! -f "$DB_PATH" ]; then
    warn "VN_law_lora.db not found at $DB_PATH"
    warn "Transfer it from your Mac or Google Drive:"
    warn "  scp user@your-mac:/path/to/VN_law_lora.db $DB_PATH"
    warn "Continuing without it — AI service will start but RAG won't work until DB is present."
fi

# ── 4. Build & start with Docker Compose ────────────────────
info "Building and starting all services..."
docker compose up --build -d

info "Waiting 10s for services to initialize..."
sleep 10

# ── 5. Health checks ────────────────────────────────────────
info "Running health checks..."

check_service() {
    local name=$1 url=$2
    if curl -sf "$url" > /dev/null 2>&1; then
        info "  ✅ $name is up ($url)"
    else
        warn "  ⚠️  $name may not be ready yet ($url) — check: docker compose logs $name"
    fi
}

check_service "AI Service"  "http://localhost:8000/health"
check_service "Backend API" "http://localhost:8080/actuator/health"
check_service "Frontend"    "http://localhost:80"

# ── 6. Done ─────────────────────────────────────────────────
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "YOUR_SERVER_IP")
echo ""
info "=============================================="
info "✅  Deployment complete!"
info "=============================================="
info "  Frontend  → http://$SERVER_IP"
info "  Backend   → http://$SERVER_IP:8080"
info "  AI API    → http://$SERVER_IP:8000/docs"
info ""
info "Useful commands:"
info "  docker compose logs -f ai-service   # watch AI logs"
info "  docker compose logs -f backend"
info "  docker compose ps                   # check status"
info "  docker compose down                 # stop all"
