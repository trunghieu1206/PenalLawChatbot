#!/usr/bin/env bash
# =============================================================
# setup_server.sh
# Dependency installer for PenalLawChatbot on a containerized
# GPU server (Ubuntu 22.04, root user, NO systemd).
#
# OPTIMIZED: every tool has a "skip if already installed" guard
# so re-runs are fast.
#
# Usage (already root — no sudo needed):
#   chmod +x setup_server.sh
#   bash setup_server.sh
# =============================================================
set -euo pipefail

LOG="/root/penallaw_setup.log"
exec > >(tee -a "$LOG") 2>&1

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERR]${NC}   $*"; }
skip()  { echo -e "${YELLOW}[SKIP]${NC}  $* (already installed)"; }

# ── 0. Detect OS ────────────────────────────────────────────
. /etc/os-release
info "Detected OS: $NAME $VERSION_ID (running as $(whoami))"

# ── 0a. Stop auto-update services that hold dpkg lock on fresh Ubuntu VPS ──
# On Ubuntu 22.04, apt-daily.timer and apt-daily-upgrade.timer fire within
# seconds of first boot and run unattended-upgrades, holding the dpkg lock
# for 5-15 minutes while they install pending security updates.
# We stop them NOW before touching apt; the existing lock-wait loop below
# acts as a fallback safety net.
info "Stopping unattended-upgrades / apt-daily (may hold dpkg lock on fresh VPS)..."
if command -v systemctl &>/dev/null && systemctl is-system-running &>/dev/null 2>&1; then
    # Systemd available — stop and disable the timers gracefully
    systemctl stop  unattended-upgrades apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
    systemctl disable apt-daily.timer apt-daily-upgrade.timer                         2>/dev/null || true
    systemctl mask  apt-daily.service apt-daily-upgrade.service                        2>/dev/null || true
    info "  systemd apt timers stopped and masked."
else
    # Container / no-systemd — kill unattended-upgrades directly (SIGTERM so it can
    # exit cleanly rather than leaving dpkg in a partial state)
    pkill -TERM -x "unattended-upgr" 2>/dev/null || true
    pkill -TERM -f  "apt-get.*upgrade" 2>/dev/null || true
    sleep 3
    info "  Sent SIGTERM to unattended-upgrades (container mode)."
fi

# Some VPS providers (e.g. DigitalOcean, Vultr, rental servers) run apt via
# cloud-init on first boot. Wait for it to finish before grabbing the lock.
if command -v cloud-init &>/dev/null; then
    info "  Waiting for cloud-init to complete (may take ~30s on first boot)..."
    cloud-init status --wait 2>/dev/null || true
fi

# ── 0b. Wait for dpkg lock (fallback for anything we couldn't stop) ─────────
info "Checking for dpkg lock..."
LOCK_WAIT=0
while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
      fuser /var/lib/dpkg/lock          >/dev/null 2>&1; do
    if [ $LOCK_WAIT -ge 300 ]; then
        err "dpkg lock held for >5 min even after stopping unattended-upgrades."
        err "Find the holder with:  fuser -v /var/lib/dpkg/lock-frontend"
        err "Then kill it and re-run this script."
        exit 1
    fi
    warn "dpkg lock held — waiting 10s... (${LOCK_WAIT}s elapsed)"
    sleep 10
    LOCK_WAIT=$((LOCK_WAIT + 10))
done
info "dpkg lock is free."

# ── 0b. Fix any interrupted dpkg state ──────────────────────
info "Running dpkg --configure -a (safe, idempotent)..."
DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true

# ── 0c. Proactively pick a reachable apt mirror ──────────────
info "Probing apt mirror reachability..."
MIRROR_OK=0
if curl -s --connect-timeout 5 --max-time 8 \
       -o /dev/null http://archive.ubuntu.com/ubuntu/dists/jammy/Release 2>/dev/null; then
    MIRROR_OK=1
    info "archive.ubuntu.com is reachable — keeping default mirror."
fi

if [ "$MIRROR_OK" = "0" ]; then
    warn "archive.ubuntu.com unreachable. Switching to Aliyun mirror..."
    cat > /etc/apt/sources.list <<'EOF'
deb http://mirrors.aliyun.com/ubuntu/ jammy main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ jammy-updates main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ jammy-backports main restricted
deb http://mirrors.aliyun.com/ubuntu/ jammy-security main restricted universe multiverse
EOF
    if ! curl -s --connect-timeout 5 --max-time 8 \
           -o /dev/null http://mirrors.aliyun.com/ubuntu/dists/jammy/Release 2>/dev/null; then
        warn "Aliyun also unreachable — trying USTC mirror..."
        cat > /etc/apt/sources.list <<'EOF'
deb http://mirrors.ustc.edu.cn/ubuntu/ jammy main restricted universe multiverse
deb http://mirrors.ustc.edu.cn/ubuntu/ jammy-updates main restricted universe multiverse
deb http://mirrors.ustc.edu.cn/ubuntu/ jammy-backports main restricted
deb http://mirrors.ustc.edu.cn/ubuntu/ jammy-security main restricted universe multiverse
EOF
    fi
fi

apt-get update -qq 2>&1 | grep -v '^W:' || true

# ── 1. Base system packages ─────────────────────────────────
info "Installing core packages..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git curl wget unzip nano \
    build-essential ca-certificates gnupg lsb-release \
    software-properties-common apt-transport-https \
    net-tools nginx \
    python3 python3-venv python3-dev python3-pip \
    python3.10 python3.10-venv python3.10-dev \
    2>&1 | tail -10 || true

if ! command -v python3 &>/dev/null; then
    err "Python installation failed. Attempting fallback..."
    apt-get install -y --fix-broken || true
    apt-get install -y python3 python3-venv python3-dev || err "Cannot install Python"
fi
info "Base system + Python ready."

# ── 2. PostgreSQL (needed by restore_database.sh) ───────────
if ! command -v psql &>/dev/null; then
    info "Installing PostgreSQL..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        postgresql postgresql-contrib libpq-dev
    info "PostgreSQL installed: $(psql --version)"
else
    skip "PostgreSQL: $(psql --version)"
fi

# ── 3. Python version detection ──────────────────────────────
if command -v conda &>/dev/null; then
    PYTHON_BIN="$(conda run which python3 2>/dev/null || echo /opt/conda/bin/python3)"
elif command -v python3.11 &>/dev/null; then
    PYTHON_BIN=/usr/bin/python3.11
elif command -v python3.10 &>/dev/null; then
    PYTHON_BIN=/usr/bin/python3.10
else
    PYTHON_BIN=$(command -v python3)
fi
info "Using Python: $PYTHON_BIN"

PY_VERSION=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if ! "$PYTHON_BIN" -m pip --version &>/dev/null; then
    warn "pip not found for $PYTHON_BIN — installing..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-pip 2>/dev/null || \
        curl -sS https://bootstrap.pypa.io/get-pip.py | "$PYTHON_BIN"
fi

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    info "Python < 3.10 detected — installing Python 3.11 via deadsnakes PPA..."
    add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3.11 2>/dev/null
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 2>/dev/null || true
    PYTHON_BIN=/usr/bin/python3.11
else
    skip "Python $PY_VERSION — skipping PPA install"
fi
"$PYTHON_BIN" -m pip install -q --upgrade pip
info "Python: $("$PYTHON_BIN" --version)"
export PYTHON_BIN

# ── 4. Node.js 20 LTS + Java 21 + Maven ─────────────────────
info "Checking Node.js, Java, Maven..."
NODE_OK=0; JAVA_OK=0
node --version 2>/dev/null | grep -qP 'v2[0-9]' && NODE_OK=1
java -version 2>&1 | grep -q '21\.' && JAVA_OK=1

if [ "$NODE_OK" = "1" ] && [ "$JAVA_OK" = "1" ]; then
    skip "Node v20 + Java 21"
else
    info "Installing Node.js 20 + Java 21 + Maven..."
    if [ "$NODE_OK" = "0" ]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        nodejs openjdk-21-jdk maven 2>&1 | tail -2
fi

# Always export JAVA_HOME regardless of whether we just installed or it was present
_JAVA_BIN=$(readlink -f "$(which java)" 2>/dev/null || true)
if [ -n "$_JAVA_BIN" ]; then
    export JAVA_HOME
    JAVA_HOME=$(dirname "$(dirname "$_JAVA_BIN")")
    grep -qxF "export JAVA_HOME=$JAVA_HOME" /root/.bashrc 2>/dev/null || \
        echo "export JAVA_HOME=$JAVA_HOME" >> /root/.bashrc
    info "JAVA_HOME=$JAVA_HOME"
fi
info "Node: $(node --version)  |  Java: $(java --version 2>&1 | head -1)  |  Maven: $(mvn --version | head -1)"

# ── 4b. Clone project repo (needed for Maven pre-warm + model pre-download) ──
REPO_URL="https://github.com/trunghieu1206/PenalLawChatbot"
PROJECT_DIR="/root/PenalLawChatbot"
BRANCH="dev"   # keep in sync with deploy_nodocker.sh

if [ -d "$PROJECT_DIR/.git" ]; then
    info "Git repo found — pulling latest ($BRANCH)..."
    git -C "$PROJECT_DIR" checkout "$BRANCH" 2>/dev/null || true
    git -C "$PROJECT_DIR" pull origin "$BRANCH" 2>/dev/null || true
elif [ -d "$PROJECT_DIR/ai-service" ]; then
    info "Project directory found (non-git, uploaded via scp) — skipping clone."
else
    warn "Project not found or incomplete — cloning fresh (preserving any backups)..."
    # Preserve database/backups/ if it exists
    _bk=""
    if [ -d "$PROJECT_DIR/database/backups" ]; then
        _bk=$(mktemp -d)
        cp -r "$PROJECT_DIR/database/backups/." "$_bk/"
    fi
    rm -rf "$PROJECT_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$PROJECT_DIR"
    if [ -n "$_bk" ] && [ -d "$_bk" ]; then
        mkdir -p "$PROJECT_DIR/database/backups"
        cp -r "$_bk/." "$PROJECT_DIR/database/backups/"
        rm -rf "$_bk"
        info "  Restored database/backups/"
    fi
fi

# Pre-warm Maven cache on first run
if [ ! -d /root/.m2/repository ] && [ -d "$PROJECT_DIR/backend" ]; then
    info "First-run: pre-warming Maven cache (background)..."
    mvn -f "$PROJECT_DIR/backend/pom.xml" dependency:go-offline -q &
    MVN_WARM_JOB=$!
fi

# ── 5. GPU check ─────────────────────────────────────────────
info "Verifying GPU availability..."
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")
    CUDA_DRIVER_VER=$(nvidia-smi 2>/dev/null | grep -oP "CUDA Version:\s*\K[0-9]+\.[0-9]+" | head -1 || echo "unknown")
    info "✅ GPU: $GPU_NAME  |  CUDA driver: $CUDA_DRIVER_VER"
else
    warn "nvidia-smi not found — GPU check skipped (set FORCE_CPU=1 in .env for CPU-only mode)."
fi

# ── 5b. Install torch + AI service deps ──────────────────────
# deploy_nodocker.sh requires at least one Python interpreter that can
# `import torch`. We install it here so the deploy never fails with
# "No Python with torch found".
#
# Strategy:
#   GPU server  → pick cu118/cu121/cu124 based on driver; fall back to 2.4.1+cu118
#                 (last cu118 build) if the same torch version has no cu118 wheel.
#   CPU server  → install lightweight CPU-only wheel (~200 MB).
info "Installing torch for AI service..."

_TORCH_ALREADY=false
"$PYTHON_BIN" -c "import torch" 2>/dev/null && _TORCH_ALREADY=true

if [ "$_TORCH_ALREADY" = "false" ]; then
    if command -v nvidia-smi &>/dev/null; then
        # Detect driver and select the highest compatible cu-tag
        _DRV_MAJOR=$(nvidia-smi 2>/dev/null | grep -oP 'Driver Version: \K[0-9]+' | head -1 || echo "0")
        if   [ "$_DRV_MAJOR" -ge 550 ]; then _CU_TAG="cu124"; _TORCH_VER="2.5.1"
        elif [ "$_DRV_MAJOR" -ge 525 ]; then _CU_TAG="cu121"; _TORCH_VER="2.5.1"
        elif [ "$_DRV_MAJOR" -ge 520 ]; then _CU_TAG="cu118"; _TORCH_VER="2.4.1"
        else                                  _CU_TAG="cu118"; _TORCH_VER="2.4.1"; fi
        info "  GPU R${_DRV_MAJOR} → installing torch==${_TORCH_VER}+${_CU_TAG}..."

        _GPU_TORCH_OK=false
        _fallback_bases=("$_TORCH_VER")
        [ "$_TORCH_VER" != "2.5.1" ] && _fallback_bases+=("2.5.1")
        [ "$_CU_TAG" = "cu118" ]     && _fallback_bases+=("2.4.1")

        for _TB in "${_fallback_bases[@]}"; do
            _TVB=$(python3 -c "p='$_TB'.split('.')[:2]; print(f'0.{int(p[0])*10+int(p[1])-5}.1')" 2>/dev/null || echo "0.19.1")
            if "$PYTHON_BIN" -m pip install \
                    "torch==${_TB}+${_CU_TAG}" "torchvision==${_TVB}+${_CU_TAG}" \
                    --index-url "https://download.pytorch.org/whl/${_CU_TAG}" \
                    --quiet 2>&1 | tail -3; then
                _GPU_TORCH_OK=true
                info "  ✅ Installed torch ${_TB}+${_CU_TAG}"
                break
            fi
            warn "  torch ${_TB}+${_CU_TAG} unavailable — trying fallback..."
        done
        [ "$_GPU_TORCH_OK" = "false" ] && warn "  GPU torch failed — falling back to CPU torch."
    fi

    # CPU fallback (no GPU, or GPU install failed)
    if ! "$PYTHON_BIN" -c "import torch" 2>/dev/null; then
        info "  Installing CPU torch (~200 MB)..."
        "$PYTHON_BIN" -m pip install torch torchvision \
            --index-url https://download.pytorch.org/whl/cpu \
            --quiet 2>&1 | tail -3 || warn "  CPU torch install failed — check pip output."
    fi
else
    skip "torch already importable — skipping install"
fi

# Install remaining AI service deps from requirements.txt (excluding torch
# to avoid pip downgrading the GPU torch we just installed).
if [ -f "$PROJECT_DIR/ai-service/requirements.txt" ]; then
    info "  Installing remaining AI service deps (requirements.txt minus torch)..."
    grep -v "^torch" "$PROJECT_DIR/ai-service/requirements.txt" > /tmp/req_notorch.txt
    "$PYTHON_BIN" -m pip install -r /tmp/req_notorch.txt --quiet 2>&1 | tail -5 || \
        warn "  Some AI deps failed — deploy_nodocker.sh will retry them."
    "$PYTHON_BIN" -m pip install "milvus-lite>=2.4.9" "pymilvus>=2.4.0" \
        "FlagEmbedding>=1.2.0" --quiet 2>&1 | tail -3 || true
    rm -f /tmp/req_notorch.txt
    info "  AI service deps installed."
else
    warn "  $PROJECT_DIR/ai-service/requirements.txt not found — will install at deploy time."
fi

# ── 6. Pre-download models ───────────────────────────────────
# Downloading here avoids first-request timeout when the service starts.
RERANKER_MODEL="BAAI/bge-reranker-v2-m3"
EMBEDDING_MODEL="trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05"

HF_TOKEN_VAL="${HF_TOKEN:-}"
if [ -f /root/PenalLawChatbot/.env ]; then
    HF_TOKEN_VAL=$(grep -oP '(?<=^HF_TOKEN=)\S+' /root/PenalLawChatbot/.env 2>/dev/null || echo "")
fi

for _MODEL in "$RERANKER_MODEL" "$EMBEDDING_MODEL"; do
    if "$PYTHON_BIN" -c "from huggingface_hub import snapshot_download; snapshot_download('$_MODEL', local_files_only=True)" 2>/dev/null; then
        skip "Already cached: $_MODEL"
    else
        info "Pre-downloading: $_MODEL ..."
        HF_HUB_VERBOSITY=warning \
        HF_TOKEN="$HF_TOKEN_VAL" \
        "$PYTHON_BIN" -c "
from huggingface_hub import snapshot_download
try:
    snapshot_download('$_MODEL', token='$HF_TOKEN_VAL' or None)
    print('  ✅ Downloaded: $_MODEL')
except Exception as e:
    print(f'  ⚠️  Pre-download failed (will retry at service start): {e}')
" 2>/dev/null || warn "Pre-download skipped for $_MODEL — will download at first startup."
    fi
done

# ── 7. Wait for Maven pre-warm ───────────────────────────────
[ -n "${MVN_WARM_JOB:-}" ] && { wait $MVN_WARM_JOB 2>/dev/null; info "Maven cache warm."; } || true

# ── Done ─────────────────────────────────────────────────────
echo ""
echo "✅  System setup completed!"
echo ""
info "Installed:"
info "  Python: $("$PYTHON_BIN" --version)"
info "  Node:   $(node --version)  |  npm: $(npm --version 2>/dev/null || echo 'n/a')"
info "  Java:   $(java --version 2>&1 | head -1 | cut -d' ' -f1-2)"
info "  Maven:  $(mvn --version 2>/dev/null | head -1)"
info "  PgSQL:  $(psql --version 2>/dev/null || echo 'see above')"
echo ""
info "Next steps:"
info "  1. bash restore_database.sh   # restore from backup"
info "  2. bash deploy_nodocker.sh    # deploy all services"
info ""
info "Log: $LOG"
