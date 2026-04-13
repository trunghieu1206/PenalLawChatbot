#!/usr/bin/env bash
# =============================================================
# setup_server.sh
# Dependency installer for PenalLawChatbot on a containerized
# GPU server (Ubuntu 22.04, root user, NO systemd).
#
# OPTIMIZED: every tool has a "skip if already installed" guard
# so re-runs are fast. PostgreSQL is installed in deploy_nodocker.sh
# to avoid duplicate downloads.
#
# Usage (already root — no sudo needed):
#   chmod +x setup_server.sh
#   bash setup_server.sh
# =============================================================
set -euo pipefail

# Log to file + stdout
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

# ── 0a. Wait for dpkg lock ──────────────────────────────────
info "Checking for dpkg lock..."
LOCK_WAIT=0
while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
      fuser /var/lib/dpkg/lock          >/dev/null 2>&1; do
    if [ $LOCK_WAIT -ge 300 ]; then
        err "dpkg lock held for >5 min. Kill the process manually then re-run."
        exit 1
    fi
    warn "dpkg lock held — waiting 10s... (${LOCK_WAIT}s elapsed)"
    sleep 10
    LOCK_WAIT=$((LOCK_WAIT + 10))
done
info "dpkg lock is free."

# ── 0b. Ensure DNS + rewrite bad mirror ─────────────────────
if ! getent hosts archive.ubuntu.com >/dev/null 2>&1; then
    warn "DNS/mirror issue — fixing..."
    cp /etc/resolv.conf /etc/resolv.conf.bak 2>/dev/null || true
    { echo "nameserver 8.8.8.8"; echo "nameserver 1.1.1.1"; } > /etc/resolv.conf
    sleep 1
    if ! getent hosts archive.ubuntu.com >/dev/null 2>&1; then
        err "Still no DNS. Check: ip route | grep default"
        exit 1
    fi
fi

# Fix sources.list: replace mirror.ubuntu.com AND deduplicate in one pass
if [ -f /etc/apt/sources.list ]; then
    sed -i 's|http://mirror.ubuntu.com/ubuntu|http://archive.ubuntu.com/ubuntu|g' /etc/apt/sources.list
    sort -u /etc/apt/sources.list > /tmp/sources.dedup && mv /tmp/sources.dedup /etc/apt/sources.list
fi
info "DNS/mirror fixed."

# ── 0c. Fix any interrupted dpkg state ──────────────────────
info "Running dpkg --configure -a (safe, idempotent)..."
DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true

# ── 1. Base system packages ─────────────────────────────────
info "Updating apt cache and installing core packages..."
apt-get update -qq

# Batch: base + nginx + python-venv all in one install call (faster)
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git curl wget unzip nano \
    build-essential ca-certificates gnupg lsb-release \
    software-properties-common apt-transport-https \
    net-tools nginx \
    python3.10-venv python3.10-dev \
    python3.11-venv python3.11-dev \
    2>&1 | tail -5 || { dpkg --configure -a; apt-get install -y --fix-broken; }
info "Base system + Python venv ready."

# ── 2. Python + pip ─────────────────────────────────────────
# Cache python availability to avoid repeated command -v calls
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

# Ensure pip is available under whichever Python we detected
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
    skip "Python $PY_VERSION — skipping PPA install (saves time)"
fi
"$PYTHON_BIN" -m pip install -q --upgrade pip
info "Python: $("$PYTHON_BIN" --version)"

# Export for all subsequent steps (uv, etc.)
export PYTHON_BIN

# ── 3. Node.js 20 LTS + Java 21 + Maven (batch install) ─────
echo "Checking Node.js, Java, Maven..."
NODE_OK=0 JAVA_OK=0
[ "$(node --version 2>/dev/null | grep -oP 'v\K20')" = "20" ] && NODE_OK=1
[ "$(java -version 2>&1 | grep -oP '\K21')" ] && JAVA_OK=1

if [ "$NODE_OK" = "1" ] && [ "$JAVA_OK" = "1" ]; then
    skip "Node v20 + Java 21"
else
    info "Installing Node.js 20 + Java 21 + Maven (parallel batch)..."
    if [ "$NODE_OK" = "0" ]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null &
        NODE_JOB=$!
    fi
    [ -n "${NODE_JOB:-}" ] && wait $NODE_JOB 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        nodejs openjdk-21-jdk maven 2>&1 | tail -2
    export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))
    echo "export JAVA_HOME=$JAVA_HOME" >> /root/.bashrc
    info "Node: $(node --version)  |  Java: $(java --version 2>&1 | head -1)  |  Maven: $(mvn --version | head -1)"
fi

# Pre-warm Maven cache only on first run (check if ~/.m2 exists)
if [ ! -d /root/.m2/repository ] && [ -d /root/PenalLawChatbot/backend ]; then
    info "First-run: pre-warming Maven cache (background)..."
    mvn -f /root/PenalLawChatbot/backend/pom.xml dependency:go-offline -q &
    MVN_WARM_JOB=$!
fi

# ── 5. Skip Docker (bare-metal, no containers needed) ──────────────────
skip "Docker (not using containers on bare-metal)"

# ── 5. Verify GPU Available (torch pre-installed on this server) ─────────────
echo "Verifying GPU availability..."

if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")
    CUDA_DRIVER_VER=$(nvidia-smi 2>/dev/null | grep -oP "CUDA Version:\s*\K[0-9]+\.[0-9]+" | head -1 || echo "unknown")
    info "✅ GPU: $GPU_NAME  |  CUDA: $CUDA_DRIVER_VER"
else
    err "[GPU ERROR] nvidia-smi not found. GPU is REQUIRED."
    err "  Verify NVIDIA driver: nvidia-smi"
    exit 1
fi

# ── 6. Wait for background Maven pre-warm (if started) ─────────────
[ -n "${MVN_WARM_JOB:-}" ] && { wait $MVN_WARM_JOB 2>/dev/null; info "Maven cache warm."; } || true

# ── Done ─────────────────────────────────────────────────────
echo ""
echo "✅  System setup completed! (~3-7 min total)"
echo ""
info "Installed:"
info "  Python:    $($PYTHON_BIN --version)  |  Java: $(java --version 2>&1 | head -1 | cut -d' ' -f1-2)"
info "  Node:      $(node --version)  |  npm: $(npm --version 2>/dev/null || echo 'auto')"
info "  GPU:       ${GPU_NAME}  |  CUDA: ${CUDA_DRIVER_VER}"
info ""
info "Next step:"
info "  bash deploy_nodocker.sh"
info ""
info "Log: $LOG"
