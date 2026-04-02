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
info "Checking DNS resolution..."
if ! getent hosts archive.ubuntu.com >/dev/null 2>&1; then
    warn "DNS resolution failed — writing fallback nameservers..."
    cp /etc/resolv.conf /etc/resolv.conf.bak 2>/dev/null || true
    { echo "nameserver 8.8.8.8"; echo "nameserver 1.1.1.1"; } > /etc/resolv.conf
    sleep 2
    if ! getent hosts archive.ubuntu.com >/dev/null 2>&1; then
        err "Still no DNS. Check: ip route | grep default"
        exit 1
    fi
    info "DNS working via 8.8.8.8."
else
    info "DNS OK."
fi

# Replace mirror.ubuntu.com with archive.ubuntu.com (CDN often unreachable in containers)
if grep -q 'mirror.ubuntu.com' /etc/apt/sources.list 2>/dev/null; then
    warn "Replacing mirror.ubuntu.com → archive.ubuntu.com in sources.list..."
    cp /etc/apt/sources.list /etc/apt/sources.list.bak
    sed -i 's|http://mirror.ubuntu.com/ubuntu|http://archive.ubuntu.com/ubuntu|g' /etc/apt/sources.list
fi
# Deduplicate sources.list to avoid "configured multiple times" warnings
# (happens when archive.ubuntu.com was already present alongside mirror.ubuntu.com)
if [ -f /etc/apt/sources.list ]; then
    awk '!seen[$0]++' /etc/apt/sources.list > /tmp/sources.dedup && mv /tmp/sources.dedup /etc/apt/sources.list
fi

# ── 0c. Fix any interrupted dpkg state ──────────────────────
info "Running dpkg --configure -a (safe, idempotent)..."
DEBIAN_FRONTEND=noninteractive dpkg --configure -a || true

# ── 1. Base system packages ─────────────────────────────────
info "Updating apt cache..."
apt-get update -qq

info "Installing base system packages..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git curl wget unzip nano \
    build-essential ca-certificates gnupg lsb-release \
    software-properties-common apt-transport-https \
    net-tools \
    nginx || { dpkg --configure -a; apt-get install -y --fix-broken; }
info "Base packages done."

# ── 2. Python + pip ─────────────────────────────────────────
# PyTorch Docker images use Miniconda: Python lives at /opt/conda/bin/python3
# The system /usr/bin/python3 is a stub with NO pip — so we must use conda's
# pip if available, and only fall back to apt/get-pip.py otherwise.

# Prefer conda Python if available (it already has pip + all torch deps)
if command -v conda &>/dev/null; then
    PYTHON_BIN="$(conda run which python3 2>/dev/null || echo /opt/conda/bin/python3)"
else
    PYTHON_BIN="$(command -v python3.11 || command -v python3.10 || command -v python3)"
fi
info "Using Python at: $PYTHON_BIN"

PY_VERSION=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

# Ensure pip is available under whichever Python we detected
if ! "$PYTHON_BIN" -m pip --version &>/dev/null; then
    warn "pip not found for $PYTHON_BIN — installing via apt..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-pip 2>/dev/null || true
    # Last resort: get-pip.py
    if ! "$PYTHON_BIN" -m pip --version &>/dev/null; then
        warn "apt pip failed — using get-pip.py..."
        curl -sS https://bootstrap.pypa.io/get-pip.py | "$PYTHON_BIN"
    fi
fi

if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
    skip "Python $PY_VERSION already installed — skipping deadsnakes PPA (saves ~15 min)"
    "$PYTHON_BIN" -m pip install --upgrade pip --quiet
else
    info "Python < 3.10 detected ($PY_VERSION) — installing Python 3.11 from deadsnakes PPA..."
    add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
    apt-get update -qq 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev python3-pip 2>/dev/null \
        || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-pip
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 2>/dev/null || true
    update-alternatives --install /usr/bin/python  python  /usr/bin/python3.11 1 2>/dev/null || true
    PYTHON_BIN=/usr/bin/python3.11
    "$PYTHON_BIN" -m pip install --upgrade pip --quiet
fi
info "Python: $("$PYTHON_BIN" --version)"

# Export for all subsequent steps (uv, etc.)
export PYTHON_BIN

# ── 3. Node.js 20 LTS ───────────────────────────────────────
if command -v node &>/dev/null && node --version | grep -q '^v20'; then
    skip "Node $(node --version)"
else
    info "Installing Node.js 20 LTS..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs
    info "Node: $(node --version)  npm: $(npm --version)"
fi

# ── 4. Java 21 + Maven ──────────────────────────────────────
if command -v java &>/dev/null && java -version 2>&1 | grep -q '21'; then
    skip "Java $(java -version 2>&1 | head -1)"
else
    info "Installing Java 21 + Maven (largest download ~300MB, please wait)..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        openjdk-21-jdk maven
    export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))
    echo "export JAVA_HOME=$JAVA_HOME" >> /root/.bashrc
    info "Java: $(java --version 2>&1 | head -1)"
    info "Maven: $(mvn --version | head -1)"
fi

# Pre-warm the Maven local repository so deploy_nodocker.sh doesn't download from scratch
# This downloads common Spring Boot parents/plugins and caches them at ~/.m2
if [ -d "/root/PenalLawChatbot/backend" ]; then
    info "Pre-warming Maven cache (downloads Spring Boot parent POMs)..."
    mvn -f /root/PenalLawChatbot/backend/pom.xml dependency:go-offline -q 2>/dev/null || true
    info "Maven cache warm."
fi

# ── 5. Docker ────────────────────────────────────────────
# Skip Docker install entirely when running inside a Docker container.
# Installing dockerd inside a container requires --privileged and is
# almost never what you want on a GPU rental server.
if [ -f /.dockerenv ] || grep -q 'docker\|lxc\|container' /proc/1/cgroup 2>/dev/null; then
    warn "Running inside a container — skipping Docker install (saves ~400MB + 5 min)."
    warn "Use the host's Docker daemon if you need containers."
elif command -v docker &>/dev/null; then
    skip "Docker $(docker --version)"
else
    info "Installing Docker..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    info "Docker installed: $(docker --version)"
fi

# ── 6. Start dockerd (no systemd) ───────────────────────────
if ! pgrep -x dockerd > /dev/null; then
    info "Starting dockerd..."
    mkdir -p /var/run
    dockerd > /root/dockerd.log 2>&1 &
    DOCKERD_PID=$!
    for i in $(seq 1 30); do
        docker ps &>/dev/null && { info "Docker is ready!"; break; }
        sleep 1
    done
    docker ps &>/dev/null || warn "Docker socket not ready yet — run 'dockerd &' manually."
else
    skip "dockerd already running"
fi

# ── 7. NVIDIA Container Toolkit (only if GPU present) ───────
if command -v nvidia-smi &>/dev/null; then
    if dpkg -l | grep -q nvidia-container-toolkit 2>/dev/null; then
        skip "NVIDIA Container Toolkit"
    else
        info "GPU detected — installing NVIDIA Container Toolkit..."
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
            | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
            | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
            > /etc/apt/sources.list.d/nvidia-container-toolkit.list
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit
        nvidia-ctk runtime configure --runtime=docker
        pkill dockerd 2>/dev/null || true; sleep 2
        dockerd > /root/dockerd.log 2>&1 &
        sleep 5
        info "NVIDIA Container Toolkit ready."
    fi
else
    warn "nvidia-smi not found — skipping GPU toolkit."
fi

# ── 8. Python AI service packages ───────────────────────────
if command -v uv &>/dev/null; then
    skip "uv already installed"
else
    info "Installing uv (fast pip replacement)..."
    "$PYTHON_BIN" -m pip install uv --quiet
fi

info "Installing Python packages via uv..."
info "(torch is pre-installed in PyTorch Docker image — only langchain/fastapi/etc. will download)"
# --python ensures uv uses conda's Python, not the stub /usr/bin/python3
# --no-build-isolation speeds up packages that don't need isolated builds
uv pip install --system --python "$PYTHON_BIN" --quiet \
    fastapi "uvicorn[standard]" \
    pydantic python-dotenv \
    "langchain==0.3.21" "langchain-core==0.3.51" \
    langchain-community langchain-openai langchain-milvus \
    "langgraph==0.3.21" \
    sentence-transformers \
    "pymilvus==2.4.4" "milvus-lite==2.4.8" "marshmallow<4.0.0" \
    peft \
    psycopg2-binary \
    openai \
    huggingface_hub \
    python-docx \
    pandas
info "Python packages installed."

# ── Done ─────────────────────────────────────────────────────
info "=============================================="
info "✅  Setup complete!"
info "=============================================="
info "  Python  : $(python3 --version)"
info "  Node    : $(node --version)"
info "  Java    : $(java --version 2>&1 | head -1)"
info "  Docker  : $(docker --version)"
info "  Git     : $(git --version)"
info ""
info "Log saved to: $LOG"
info "Next step: bash deploy_nodocker.sh"
