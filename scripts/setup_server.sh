#!/usr/bin/env bash
# =============================================================
# setup_server.sh
# Dependency installer for PenalLawChatbot on a containerized
# GPU server (Ubuntu 22.04, root user, NO systemd).
#
# Usage (already root — no sudo needed):
#   chmod +x setup_server.sh
#   bash setup_server.sh
# =============================================================
set -euo pipefail

# Log to file + stdout (use /root/ since /var/log may be restricted)
LOG="/root/penallaw_setup.log"
exec > >(tee -a "$LOG") 2>&1

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERR]${NC}   $*"; }

# ── 0. Detect OS ────────────────────────────────────────────
. /etc/os-release
info "Detected OS: $NAME $VERSION_ID (running as $(whoami))"

# ── 0a. Wait for dpkg lock ──────────────────────────────────
# Fresh VPS boot: unattended-upgrades or cloud-init holds the lock.
# Wait up to 5 minutes before giving up.
info "Checking for dpkg lock..."
LOCK_WAIT=0
while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
      fuser /var/lib/dpkg/lock          >/dev/null 2>&1; do
    if [ $LOCK_WAIT -ge 300 ]; then
        err "dpkg lock held for >5 min. Kill the process manually then re-run."
        exit 1
    fi
    warn "dpkg lock held by $(fuser /var/lib/dpkg/lock-frontend 2>&1 || true) — waiting 10s... (${LOCK_WAIT}s elapsed)"
    sleep 10
    LOCK_WAIT=$((LOCK_WAIT + 10))
done
info "dpkg lock is free."

# ── 0b. Ensure DNS works ────────────────────────────────────
info "Checking DNS resolution..."
if ! getent hosts google.com >/dev/null 2>&1 && \
   ! nslookup google.com >/dev/null 2>&1; then
    warn "DNS resolution failed. Writing fallback nameservers to /etc/resolv.conf..."
    # Preserve existing content as backup
    cp /etc/resolv.conf /etc/resolv.conf.bak 2>/dev/null || true
    { echo "nameserver 8.8.8.8"; echo "nameserver 1.1.1.1"; } > /etc/resolv.conf
    # Give the network stack a moment
    sleep 2
    if ! getent hosts google.com >/dev/null 2>&1; then
        err "Still no DNS after setting 8.8.8.8/1.1.1.1. Check your network config."
        err "Run: ip route | grep default  (verify gateway exists)"
        exit 1
    fi
    info "DNS now working via 8.8.8.8."
else
    info "DNS resolution OK."
fi

# ── 1. System packages ──────────────────────────────────────
info "Updating apt and installing system packages..."
apt-get update -qq
# NOTE: nginx pulls in fontconfig-config whose postinst runs fc-cache.
# In headless/minimal containers this can exit non-zero and kill the script
# (due to set -e). DEBIAN_FRONTEND=noninteractive + the dpkg --configure -a
# fallback ensures the install completes even when the postinst trips.
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git curl wget unzip zip nano \
    build-essential ca-certificates gnupg lsb-release \
    software-properties-common apt-transport-https \
    net-tools htop \
    nginx || { dpkg --configure -a; apt-get install -y --fix-broken; }
info "Base system packages installed."

# ── 1b. PostgreSQL 16 (official PGDG repo) ──────────────────
info "Adding PostgreSQL 16 repository..."
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] \
    https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16 postgresql-client-16 libpq-dev
info "PostgreSQL 16 installed."


info "Git: $(git --version)"

# ── 2. Python 3.11 ──────────────────────────────────────────
info "Installing Python 3.11..."
# Try deadsnakes PPA, fall back silently if it fails in container
add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
apt-get update -qq 2>/dev/null || true
DEBIAN_FRONTEND=noninteractive apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip 2>/dev/null \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip  # fallback

update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 2>/dev/null || true
update-alternatives --install /usr/bin/python  python  /usr/bin/python3.11 1 2>/dev/null || true
python3 -m pip install --upgrade pip setuptools wheel
info "Python: $(python3 --version)"

# ── 3. Node.js 20 LTS ───────────────────────────────────────
info "Installing Node.js 20 LTS..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
info "Node: $(node --version)  npm: $(npm --version)"

# ── 4. Java 21 ──────────────────────────────────────────────
info "Installing Java 21 + Maven..."
DEBIAN_FRONTEND=noninteractive apt-get install -y openjdk-21-jdk maven
export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))
echo "export JAVA_HOME=$JAVA_HOME" >> /root/.bashrc
info "Java: $(java --version 2>&1 | head -1)"
info "Maven: $(mvn --version | head -1)"

# ── 5. Docker (install only — no systemctl) ─────────────────
info "Installing Docker..."
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) \
        signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu \
        $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    info "Docker installed: $(docker --version)"
else
    warn "Docker already installed: $(docker --version)"
fi

# ── 6. Start dockerd (no systemd in container environment) ──
info "Starting Docker daemon (dockerd)..."
if ! pgrep -x dockerd > /dev/null; then
    # Create required dir for docker socket
    mkdir -p /var/run
    # Start dockerd in background, redirect logs
    dockerd > /root/dockerd.log 2>&1 &
    DOCKERD_PID=$!
    info "dockerd started (PID $DOCKERD_PID). Waiting for socket..."
    # Wait up to 30s for docker to become ready
    for i in $(seq 1 30); do
        if docker ps &>/dev/null; then
            info "Docker is ready!"
            break
        fi
        sleep 1
    done
    docker ps &>/dev/null || warn "Docker socket not ready yet — run 'dockerd &' manually before deploying."
else
    info "dockerd is already running."
fi

# ── 7. NVIDIA Container Toolkit ─────────────────────────────
if command -v nvidia-smi &>/dev/null; then
    info "GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
    info "Installing NVIDIA Container Toolkit..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit
    # Configure for dockerd (no systemctl restart — just reconfigure)
    nvidia-ctk runtime configure --runtime=docker
    # Restart dockerd to pick up NVIDIA runtime
    pkill dockerd 2>/dev/null || true
    sleep 2
    dockerd > /root/dockerd.log 2>&1 &
    sleep 5
    info "NVIDIA Container Toolkit ready."
else
    warn "nvidia-smi not found — skipping GPU toolkit."
fi

# ── 8. Python AI service dependencies ───────────────────────
info "Installing uv (fast pip replacement that handles complex deps)..."
pip install uv

info "Installing Python packages via uv..."
uv pip install --system \
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


# ── Done ────────────────────────────────────────────────────
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
info "Next step: bash deploy.sh"
