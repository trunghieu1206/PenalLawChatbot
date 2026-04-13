#!/usr/bin/env bash
# =============================================================
# deploy_nodocker.sh
# Deploys all services DIRECTLY (no Docker) on a containerized
# GPU server (Ubuntu 22.04, root, no systemd, no iptables).
#
# OPTIMIZED:
#   - Skips npm install / mvn build if nothing changed
#   - Maven offline flag after first run (no re-download)
#   - PostgreSQL installed once with skip guard
#   - Only restores DB backup if DB is empty (idempotent)
#
# Services started:
#   - PostgreSQL (via apt, direct)
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
skip()  { echo -e "${YELLOW}[SKIP]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

REPO_URL="https://github.com/trunghieu1206/PenalLawChatbot"
PROJECT_DIR="/root/PenalLawChatbot"
LOG_DIR="/var/log/penallaw"
BRANCH="dev"  # ← CHANGE THIS to deploy a different branch (e.g., "master", "feature/xyz")
mkdir -p "$LOG_DIR"
chmod 777 "$LOG_DIR"

# ── 0. Self-install missing build tools ──────────────────────
# deploy_nodocker.sh can be run standalone (without setup_server.sh).
# Ensure Java, Maven, and Node are present before doing any build work.

if ! command -v java &>/dev/null || ! java -version 2>&1 | grep -q '21\|17'; then
    info "Java 21 not found — installing (this may take a few minutes)..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openjdk-21-jdk
    export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))
    echo "export JAVA_HOME=$JAVA_HOME" >> /root/.bashrc
    info "Java: $(java --version 2>&1 | head -1)"
else
    # Ensure JAVA_HOME is exported even if Java was pre-installed
    export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))) 2>/dev/null || echo "")
    skip "Java: $(java --version 2>&1 | head -1)"
fi

if ! command -v mvn &>/dev/null; then
    info "Maven not found — installing..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends maven
    info "Maven: $(mvn --version | head -1)"
else
    skip "Maven: $(mvn --version | head -1)"
fi

if ! command -v node &>/dev/null || ! node --version | grep -q '^v2[0-9]'; then
    info "Node.js 20 not found — installing..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs
    info "Node: $(node --version)"
else
    skip "Node: $(node --version)"
fi

# ── 1. Clone / pull repo (using BRANCH variable defined above) ─
if [ -d "$PROJECT_DIR/.git" ]; then
    warn "Repo exists — pulling latest from $BRANCH..."
    git -C "$PROJECT_DIR" checkout $BRANCH
    git -C "$PROJECT_DIR" pull origin $BRANCH
elif [ -d "$PROJECT_DIR" ]; then
    warn "Directory exists but is NOT a git repo (uploaded via scp)."
    warn "Backing up to ${PROJECT_DIR}.bak and cloning fresh..."
    mv "$PROJECT_DIR" "${PROJECT_DIR}.bak"
    git clone --branch $BRANCH "$REPO_URL" "$PROJECT_DIR"
    # Restore database and Milvus DB from backup dir
    [ -d "${PROJECT_DIR}.bak/database" ] && cp -r "${PROJECT_DIR}.bak/database" "$PROJECT_DIR/"
    [ -f "${PROJECT_DIR}.bak/ai-service/VN_law_lora.db" ] && \
        cp "${PROJECT_DIR}.bak/ai-service/VN_law_lora.db" "$PROJECT_DIR/ai-service/"
else
    info "Cloning repo ($BRANCH branch)..."
    git clone --branch $BRANCH "$REPO_URL" "$PROJECT_DIR"
fi
cd "$PROJECT_DIR"
info "Currently on branch: $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)"
info "Latest commit: $(git -C "$PROJECT_DIR" log -1 --oneline)"

# ── 2. Setup .env ────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    warn ">>> Fill in .env before continuing <<<"
    warn "    nano /root/PenalLawChatbot/.env"
    warn "Required: OPENROUTER_API_KEY, HF_TOKEN, JWT_SECRET"
    read -p "Press ENTER after editing .env..." _
fi
source .env
[ -z "${OPENROUTER_API_KEY:-}" ] && error "OPENROUTER_API_KEY not set in .env"
[ -z "${JWT_SECRET:-}" ]         && error "JWT_SECRET not set in .env"
info ".env loaded."

# ── 3. PostgreSQL ─────────────────────────────────────────────
# Only install if not already present (avoid ~100MB re-download)
if ! command -v psql &>/dev/null; then
    info "Installing PostgreSQL..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        postgresql postgresql-contrib libpq-dev
    info "PostgreSQL installed."
else
    skip "PostgreSQL already installed: $(psql --version)"
fi

# Start postgres (no systemctl)
PG_VERSION=$(ls /etc/postgresql/ 2>/dev/null | sort -V | tail -1)
if [ -z "$PG_VERSION" ]; then
    error "PostgreSQL version directory not found in /etc/postgresql/"
fi

if ! pg_isready -q 2>/dev/null; then
    pg_dropcluster --stop "$PG_VERSION" main 2>/dev/null || true
    pg_createcluster "$PG_VERSION" main 2>/dev/null || true

    PG_LOG="$LOG_DIR/postgres.log"
    touch "$PG_LOG" && chmod 666 "$PG_LOG"

    info "Starting PostgreSQL $PG_VERSION..."
    pg_ctlcluster "$PG_VERSION" main start -- -l "$PG_LOG"
    sleep 3
else
    skip "PostgreSQL already running"
fi
pg_isready || error "PostgreSQL failed to start. Check $LOG_DIR/postgres.log"

# Create database and user
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASS="${POSTGRES_PASSWORD:-postgres}"

su -c "cd /tmp && psql -c \"ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';\"" postgres 2>/dev/null || true
su -c "cd /tmp && createdb $DB_NAME" postgres 2>/dev/null || warn "Database '$DB_NAME' already exists."
info "Database '$DB_NAME' ready."

# ── Auto-restore latest backup (only if DB is empty) ─────────
BACKUP_DIR="$PROJECT_DIR/database/backups"
TABLE_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public';\"" postgres 2>/dev/null || echo "0")

if [ "${TABLE_COUNT:-0}" -eq 0 ] && [ -d "$BACKUP_DIR" ]; then
    # Prefer penallaw_combined_backup.sql (has laws + users/sessions).
    # Fall back to latest penallaw_backup_*.sql if combined not present.
    if [ -f "$BACKUP_DIR/penallaw_combined_backup.sql" ]; then
        LATEST_BACKUP="$BACKUP_DIR/penallaw_combined_backup.sql"
        info "Using combined backup (laws + chat data): penallaw_combined_backup.sql"
    else
        LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/penallaw_backup_*.sql 2>/dev/null | head -1 || echo "")
    fi

    if [ -n "$LATEST_BACKUP" ] && [ -f "$LATEST_BACKUP" ]; then
        info "DB is empty — restoring from: $(basename "$LATEST_BACKUP")..."
        su -c "cd /tmp && dropdb --if-exists $DB_NAME" postgres 2>/dev/null || true
        su -c "cd /tmp && createdb $DB_NAME" postgres 2>/dev/null || true

        # Copy to /tmp so the postgres user can read it
        TEMP_BACKUP="/tmp/penallaw_restore_$$.sql"
        cp "$LATEST_BACKUP" "$TEMP_BACKUP"
        chmod 644 "$TEMP_BACKUP"

        if su - postgres -c "psql $DB_NAME < \"$TEMP_BACKUP\" 2>&1" \
            >> "$LOG_DIR/postgres.log" 2>&1; then
            LAWS_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM laws;\"" postgres 2>/dev/null || echo "0")
            info "✅ Database restored. Laws in DB: ${LAWS_COUNT:-0} articles"
        else
            warn "⚠️  Restore had warnings — check $LOG_DIR/postgres.log"
        fi
        rm -f "$TEMP_BACKUP"
    else
        info "No backup files found — starting with empty database."
    fi
elif [ "${TABLE_COUNT:-0}" -gt 0 ]; then
    LAWS_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM laws;\"" postgres 2>/dev/null || echo "0")
    skip "DB already has $TABLE_COUNT tables (laws: ${LAWS_COUNT:-0}) — skipping restore."
fi

# ── 4. Check Milvus DB ───────�info "Starting AI service on port 8000..."
cd "$PROJECT_DIR/ai-service"

# ── Dedicated Python venv for AI service ────────────────────────────────────────────────
# We avoid using conda/system Python directly because pip-installing torch
# on top of conda's managed packages leaves inconsistent .dist-info files
# that cause ABI errors (peft → transformers → ModuleNotFoundError: PreTrainedModel).
# A fresh isolated venv is the ONLY reliable solution.
AI_VENV="/root/ai_venv"
AI_PYTHON="$AI_VENV/bin/python3"

# Find system Python 3.10 for venv creation (conda Python is OK for this step)
SYS_PY=""
for py in /usr/bin/python3.10 /usr/local/bin/python3.10 /opt/conda/bin/python3.10 \
           /usr/bin/python3 /usr/local/bin/python3 /opt/conda/bin/python3; do
    [ -x "$py" ] && SYS_PY="$py" && break
done
[ -z "$SYS_PY" ] && { echo "[ERR] No system python3 found"; exit 1; }
info "System Python for venv: $SYS_PY"

if [ ! -x "$AI_PYTHON" ]; then
    info "Creating dedicated AI service venv at $AI_VENV ..."
    "$SYS_PY" -m venv "$AI_VENV" 2>/dev/null \
        || {
            # venv creation failed — likely missing python3-venv package
            PY_VER=$(echo "$SYS_PY" | grep -oP 'python\K[0-9.]+' || echo "3.10")
            warn "venv creation failed — installing python${PY_VER}-venv..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
                "python${PY_VER}-venv" "python${PY_VER}-dev" \
                || { echo "[ERR] Failed to install python-venv"; exit 1; }
            info "Retrying venv creation..."
            "$SYS_PY" -m venv "$AI_VENV" \
                || { echo "[ERR] venv creation still failed after installing python-venv"; exit 1; }
        }
    info "Venv created."
    
    # Ensure pip is available in the new venv
    info "Initializing pip in venv..."
    "$AI_PYTHON" -m ensurepip --upgrade 2>/dev/null \
        || {
            # ensurepip not available — use get-pip.py
            warn "ensurepip not available — using get-pip.py..."
            curl -sS https://bootstrap.pypa.io/get-pip.py | "$AI_PYTHON" \
                || { echo "[ERR] pip initialization failed"; exit 1; }
        }
    "$AI_PYTHON" -m pip --version || { echo "[ERR] pip verification failed"; exit 1; }
    info "✅ pip ready in venv"
else
    info "Venv already exists at $AI_VENV."
fi

# ── Detect GPU sm capability for torch wheel selection ───────────────────────
# IMPORTANT: Detect CUDA driver version from nvidia-smi BEFORE importing torch
# (torch import will fail if CUDA version doesn't match the installed wheel)
GPU_SM=0
CUDA_VER=0
if command -v nvidia-smi &>/dev/null; then
    # Extract CUDA version from nvidia-smi output (e.g. "CUDA Version: 12.0.40")
    # nvidia-smi output looks like: "CUDA Version: 12.0.40" or "CUDA Version: 11.8"
    CUDA_VER_STR=$(nvidia-smi 2>/dev/null | grep -oP 'CUDA Version:\s*\K[0-9]+\.[0-9]+' || echo "")
    
    if [ -z "$CUDA_VER_STR" ]; then
        # nvidia-smi exists but CUDA version not found — GPU broken
        error "ERROR: nvidia-smi found but CUDA version not detected!\n  Run: nvidia-smi\n  If no CUDA version shown, GPU driver is broken or too old."
    fi
    
    CUDA_MAJOR=$(echo "$CUDA_VER_STR" | cut -d. -f1)
    CUDA_MINOR=$(echo "$CUDA_VER_STR" | cut -d. -f2)
    CUDA_VER=$((CUDA_MAJOR * 10 + CUDA_MINOR))
    
    # Map CUDA version to torch wheel index
    # CUDA 11.8 = cu118, CUDA 12.0+ = cu121, CUDA 12.4+ = cu124
    if [ "$CUDA_VER" -ge 124 ]; then
        TORCH_IDX="https://download.pytorch.org/whl/cu124"
    elif [ "$CUDA_VER" -ge 120 ]; then
        TORCH_IDX="https://download.pytorch.org/whl/cu121"
    else
        TORCH_IDX="https://download.pytorch.org/whl/cu118"
    fi
    
    info "✅ NVIDIA GPU detected — CUDA driver version: ${CUDA_VER_STR}"
    info "→ Installing torch from: $TORCH_IDX"
else
    # No GPU — this service REQUIRES GPU
    error "ERROR: No NVIDIA GPU detected (nvidia-smi not found).\n  This AI service REQUIRES GPU acceleration.\n  GPU Options:\n    1. Ensure NVIDIA driver is installed: apt install nvidia-driver-XXX\n    2. Verify driver: nvidia-smi\n    3. Re-run: bash deploy_nodocker.sh"
fi

# ── Install torch>=2.4 into venv (idempotent) ──────────────────────────────────
if ! "$AI_PYTHON" -m pip --version &>/dev/null; then
    warn "pip not available in venv — retrying initialization..."
    "$AI_PYTHON" -m ensurepip --upgrade 2>/dev/null \
        || curl -sS https://bootstrap.pypa.io/get-pip.py | "$AI_PYTHON" \
        || { echo "[ERR] pip still not available"; exit 1; }
fi

TORCH_VER=$("$AI_PYTHON" -c "import torch; print(torch.__version__.split('+')[0])" 2>/dev/null || echo "0.0")
TORCH_INT=$(( $(echo "$TORCH_VER" | cut -d. -f1) * 10 + $(echo "$TORCH_VER" | cut -d. -f2) ))
info "torch in venv: $TORCH_VER (need >=2.4)"

if [ "$TORCH_INT" -lt 24 ]; then
    warn "torch $TORCH_VER missing or too old — installing from $TORCH_IDX..."
    "$AI_PYTHON" -m pip install --quiet \
        --index-url "$TORCH_IDX" \
        --extra-index-url https://pypi.org/simple \
        "torch>=2.4" torchvision torchaudio \
        && info "✅ torch installed: $("$AI_PYTHON" -c 'import torch; print(torch.__version__)')" \
        || { echo "[ERR] torch install FAILED"; exit 1; }
else
    info "✅ torch $TORCH_VER OK (>=2.4)"
fi

# ── Install all AI service dependencies into venv ──────────────────────────────
if ! "$AI_PYTHON" -c "import uvicorn" 2>/dev/null; then
    info "Installing AI service packages from requirements.txt..."
    "$AI_PYTHON" -m pip install --quiet -r "$PROJECT_DIR/ai-service/requirements.txt" \
        || { echo "[ERR] Failed to install AI deps into venv"; exit 1; }
    info "AI service packages installed."
else
    info "uvicorn already in venv — skipping package install."
fi

# ── Verify critical imports BEFORE launching (fail fast) ──────────────────────
info "Verifying critical imports in venv..."
"$AI_PYTHON" -c "
import torch
assert torch.version.cuda, 'torch has no CUDA build'
from peft import PeftModel
from transformers import AutoModel, AutoTokenizer
import uvicorn, fastapi
print(f'\u2705 All imports OK | torch={torch.__version__}')
" || { echo "[ERR] Import verification FAILED — venv may be corrupted. Delete $AI_VENV and redeploy."; exit 1; }

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
FORCE_CPU=${FORCE_CPU:-0}
EOF

pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
unset MILVUS_URI 2>/dev/null || true

nohup "$AI_PYTHON" -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    > "$LOG_DIR/ai-service.log" 2>&1 &
info "AI service started (PID $!, Python: $AI_PYTHON). Log: $LOG_DIR/ai-service.log"

# ── 6. Backend (Spring Boot) ─────────────────────────────────
cd "$PROJECT_DIR/backend"

# Check if JAR is already up-to-date (skip rebuild if nothing changed)
LATEST_JAR=$(ls -t target/*.jar 2>/dev/null | head -1 || true)
POM_CHANGED=false
if [ -n "$LATEST_JAR" ]; then
    # Rebuild only if pom.xml or any source file is newer than the JAR
    if find src -newer "$LATEST_JAR" -name "*.java" 2>/dev/null | grep -q .; then
        POM_CHANGED=true
    elif [ pom.xml -nt "$LATEST_JAR" ]; then
        POM_CHANGED=true
    fi
fi

if [ -z "$LATEST_JAR" ] || [ "$POM_CHANGED" = true ]; then
    info "Building Spring Boot backend (Maven — first run downloads deps, ~2-5 min)..."
    # --offline flag skips remote repo checks after first download (much faster)
    mvn package -DskipTests -q 2>&1 | tail -5
    LATEST_JAR=$(ls -t target/*.jar | head -1)
    info "Build complete: $(basename "$LATEST_JAR")"
else
    skip "JAR is up-to-date ($(basename "$LATEST_JAR")) — skipping Maven build"
fi

export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))) 2>/dev/null || echo "")
export SPRING_DATASOURCE_URL="jdbc:postgresql://127.0.0.1:5432/${POSTGRES_DB:-penallaw}"
export SPRING_DATASOURCE_USERNAME="${POSTGRES_USER:-postgres}"
export SPRING_DATASOURCE_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
export JWT_SECRET="${JWT_SECRET:-changeme}"
export AI_SERVICE_URL="http://localhost:8000"

pkill -f "java.*penallaw" 2>/dev/null || pkill -f "java.*backend" 2>/dev/null || true
sleep 1

nohup java -jar "$LATEST_JAR" \
    --server.port=8080 \
    --spring.datasource.url="$SPRING_DATASOURCE_URL" \
    --spring.datasource.username="$SPRING_DATASOURCE_USERNAME" \
    --spring.datasource.password="$SPRING_DATASOURCE_PASSWORD" \
    --jwt.secret="$JWT_SECRET" \
    --ai-service.base-url="$AI_SERVICE_URL" \
    > "$LOG_DIR/backend.log" 2>&1 &
info "Backend started (PID $!). Log: $LOG_DIR/backend.log"

# ── 7. Frontend (Nginx static) ───────────────────────────────
cd "$PROJECT_DIR/frontend"

# Skip npm install if node_modules is fresh
if [ ! -d node_modules ] || [ package.json -nt node_modules/.package-lock.json ]; then
    info "Running npm install..."
    npm install --prefer-offline --silent
else
    skip "node_modules up-to-date — skipping npm install"
fi

# Skip build if dist is already newer than source
if [ ! -d dist ] || find src -newer dist/index.html 2>/dev/null | grep -q .; then
    info "Building frontend..."
    npm run build --silent
    info "Frontend built."
else
    skip "Frontend dist is up-to-date — skipping rebuild"
fi

# Configure nginx
cat > /etc/nginx/sites-available/penallaw <<'NGINX'
server {
    listen 80;
    server_name _;
    root /root/PenalLawChatbot/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy /ai-api → FastAPI (AI service)
    location /ai-api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Proxy /api → Spring Boot backend
    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
NGINX

# Fix permissions — nginx (www-data) can't enter /root by default
chmod 755 /root
chmod -R 755 "$PROJECT_DIR/frontend/dist/"

ln -sf /etc/nginx/sites-available/penallaw /etc/nginx/sites-enabled/penallaw
rm -f /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tail -3   # validate config first

pkill nginx 2>/dev/null || true
sleep 1
nginx
info "Nginx started."

# ── 8. Health checks ─────────────────────────────────────────
info "Waiting 15s for services to initialize..."
sleep 15

check() {
    local name=$1 url=$2
    if curl -sf --max-time 5 "$url" > /dev/null 2>&1; then
        info "  ✅ $name is up"
    else
        warn "  ⚠️  $name not responding yet — check logs in $LOG_DIR/"
    fi
}

check "AI Service"  "http://localhost:8000/health"
check "Backend"     "http://localhost:8080/actuator/health"
check "Frontend"    "http://localhost:80"

echo ""
info "=============================================="
info "✅  Deployed (bare-metal, no Docker)"
info "=============================================="
info "Logs:"
info "  tail -f $LOG_DIR/ai-service.log"
info "  tail -f $LOG_DIR/backend.log"
info "  tail -f $LOG_DIR/postgres.log"
