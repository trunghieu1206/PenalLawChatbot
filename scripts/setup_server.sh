#!/usr/bin/env bash
# =============================================================
# setup_server.sh
# Full dependency installer for PenalLawChatbot on a fresh
# Ubuntu 20.04/22.04 GPU server (runs in one shot).
#
# Usage:
#   chmod +x setup_server.sh
#   sudo bash setup_server.sh
# =============================================================
set -euo pipefail
LOG="/var/log/penallaw_setup.log"
exec > >(tee -a "$LOG") 2>&1

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── 0. Detect OS ────────────────────────────────────────────
. /etc/os-release
info "Detected OS: $NAME $VERSION_ID"

# ── 1. System packages ──────────────────────────────────────
info "Installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    git curl wget unzip zip \
    build-essential ca-certificates gnupg lsb-release \
    software-properties-common apt-transport-https \
    libpq-dev postgresql-client \
    net-tools htop nvtop \
    nginx

# ── 2. Git (latest) ─────────────────────────────────────────
info "Git version: $(git --version)"

# ── 3. Python 3.11 ──────────────────────────────────────────
info "Installing Python 3.11..."
add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
apt-get update -qq
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip

update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
update-alternatives --install /usr/bin/python  python  /usr/bin/python3.11 1
python3 -m pip install --upgrade pip setuptools wheel
info "Python: $(python3 --version)"

# ── 4. Node.js 20 LTS ───────────────────────────────────────
info "Installing Node.js 20 LTS..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
info "Node: $(node --version)  npm: $(npm --version)"

# ── 5. Java 21 (for Spring Boot) ────────────────────────────
info "Installing Java 21..."
apt-get install -y openjdk-21-jdk maven
export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))
echo "JAVA_HOME=$JAVA_HOME" >> /etc/environment
info "Java: $(java --version 2>&1 | head -1)"
info "Maven: $(mvn --version | head -1)"

# ── 6. Docker & Docker Compose v2 ───────────────────────────
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
    apt-get install -y docker-ce docker-ce-cli containerd.io \
                       docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
    info "Docker: $(docker --version)"
    info "Docker Compose: $(docker compose version)"
else
    warn "Docker already installed, skipping."
fi

# ── 7. NVIDIA Container Toolkit (GPU Docker support) ────────
info "Installing NVIDIA Container Toolkit..."
if command -v nvidia-smi &>/dev/null; then
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -qq
    apt-get install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
    info "NVIDIA Container Toolkit installed."
else
    warn "nvidia-smi not found — skipping GPU Docker toolkit. Install GPU driver first if needed."
fi

# ── 8. Python dependencies for ai-service ───────────────────
info "Installing Python AI service dependencies..."
pip install --upgrade \
    fastapi uvicorn[standard] \
    pydantic python-dotenv \
    langchain langchain-core langchain-community langchain-openai langchain-milvus \
    langgraph \
    sentence-transformers \
    "pymilvus==2.4.4" "milvus-lite==2.4.8" "marshmallow<4.0.0" \
    peft \
    psycopg2-binary \
    openai \
    huggingface_hub \
    python-docx \
    pandas

info "Python packages installed."

info "=============================================="
info "✅  All dependencies installed successfully!"
info "=============================================="
info "Versions summary:"
info "  Python  : $(python3 --version)"
info "  Node    : $(node --version)"
info "  Java    : $(java --version 2>&1 | head -1)"
info "  Maven   : $(mvn --version | head -1)"
info "  Docker  : $(docker --version)"
info "  Git     : $(git --version)"
info ""
info "Log saved to: $LOG"
info ""
info "Next: run deploy.sh to clone & start the project."
