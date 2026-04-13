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
    "$PYTHON_BIN" -m pip install --upgrade pip
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
    "$PYTHON_BIN" -m pip install --upgrade pip
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

# ── 8. Detect GPU + select correct PyTorch wheel ─────────────
info "Detecting GPU and CUDA driver version..."

CUDA_DRIVER_VER=""
GPU_NAME=""
TORCH_INDEX_URL=""
TORCH_EXTRA_ARGS=""

if command -v nvidia-smi &>/dev/null; then
    # nvidia-smi --query-gpu=name prints the GPU model
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "")
    # CUDA version from driver (e.g. "CUDA Version: 12.4")
    CUDA_DRIVER_VER=$(nvidia-smi 2>/dev/null \
        | grep -oP "CUDA Version:\s*\K[0-9]+\.[0-9]+" | head -1 || echo "")
    info "GPU detected  : ${GPU_NAME:-unknown}"
    info "CUDA driver   : ${CUDA_DRIVER_VER:-unknown}"
else
    warn "nvidia-smi not found — no GPU detected on this machine."
fi

# Map CUDA driver version → PyTorch wheel.
# Rule: choose the highest cu-version that is ≤ CUDA driver version.
# PyTorch official wheels: cu118, cu121, cu124, cu126
if [ -n "$CUDA_DRIVER_VER" ]; then
    CUDA_MAJOR=$(echo "$CUDA_DRIVER_VER" | cut -d. -f1)
    CUDA_MINOR=$(echo "$CUDA_DRIVER_VER" | cut -d. -f2)
    CUDA_INT=$(( CUDA_MAJOR * 100 + CUDA_MINOR ))   # e.g. 12.4 → 1204

    if   [ "$CUDA_INT" -ge 1206 ]; then
        TORCH_CU="cu126"
    elif [ "$CUDA_INT" -ge 1204 ]; then
        TORCH_CU="cu124"
    elif [ "$CUDA_INT" -ge 1200 ]; then
        TORCH_CU="cu121"
    elif [ "$CUDA_INT" -ge 1108 ]; then
        TORCH_CU="cu118"
    elif [ "$CUDA_INT" -ge 1100 ]; then
        # Driver 11.0–11.7: use cu118 (backward compatible at runtime)
        TORCH_CU="cu118"
        warn "CUDA driver ${CUDA_DRIVER_VER} is old — using PyTorch cu118 (may have reduced sm support)"
    else
        TORCH_CU=""
        warn "CUDA driver version ${CUDA_DRIVER_VER} is too old for any supported PyTorch GPU wheel."
        warn "Minimum supported: CUDA 11.0. GPU will NOT be used."
    fi

    if [ -n "$TORCH_CU" ]; then
        TORCH_INDEX_URL="https://download.pytorch.org/whl/${TORCH_CU}"
        info "Selected PyTorch wheel : ${TORCH_CU}  (index: ${TORCH_INDEX_URL})"
    fi
else
    # nvidia-smi not found → no GPU
    if [ "${FORCE_CPU_SETUP:-0}" = "1" ]; then
        warn "FORCE_CPU_SETUP=1 — no GPU, continuing with CPU-only install."
        warn "The AI service embedding will be SLOW. This is NOT recommended for production."
    else
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        err  "[GPU ERROR] nvidia-smi not found — no GPU detected."
        err  "  This server requires a CUDA-capable GPU to run the embedding model."
        err  "  Verify the GPU driver is installed and visible:"
        err  "    nvidia-smi"
        err  "    lspci | grep -i nvidia"
        err  ""
        err  "  If you intentionally want CPU-only (NOT recommended):"
        err  "    FORCE_CPU_SETUP=1 bash setup_server.sh"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        exit 1
    fi
fi

# ── 9. Install / fix PyTorch ──────────────────────────────────
if command -v uv &>/dev/null; then
    skip "uv already installed"
else
    info "Installing uv (fast pip replacement)..."
    "$PYTHON_BIN" -m pip install uv
fi

info "Installing Python AI service packages..."
# --python ensures uv uses conda's Python, not the stub /usr/bin/python3

if [ -n "$TORCH_INDEX_URL" ]; then
    # GPU server: install torch from the GPU-specific wheel index first,
    # then install the rest (which don't need the GPU index).
    info "Installing GPU-compatible torch from ${TORCH_INDEX_URL}..."
    uv pip install --system --python "$PYTHON_BIN" \
        --index-url "$TORCH_INDEX_URL" \
        --extra-index-url https://pypi.org/simple \
        torch torchvision torchaudio
else
    # Only reachable when FORCE_CPU_SETUP=1 was explicitly set above.
    info "Installing CPU-only torch (FORCE_CPU_SETUP=1 mode)..."
    uv pip install --system --python "$PYTHON_BIN" \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        torch torchvision torchaudio
fi

# Install the rest of the AI service dependencies
uv pip install --system --python "$PYTHON_BIN" \
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

# ── 10. CUDA probe — verify GPU is actually usable ────────────
if [ -n "$TORCH_INDEX_URL" ]; then
    info "Running CUDA probe to verify GPU kernels work for ${GPU_NAME}..."
    PROBE_RESULT=$("$PYTHON_BIN" - <<'PYEOF' 2>&1
import sys
try:
    import torch
    if not torch.cuda.is_available():
        print("FAIL: torch.cuda.is_available() returned False")
        sys.exit(1)
    name = torch.cuda.get_device_name(0)
    cap  = torch.cuda.get_device_capability(0)
    # Run a real kernel dispatch — this is what cudaErrorNoKernelImageForDevice triggers
    t = torch.zeros(256, device="cuda")
    t = t * 2 + 1
    result = t.sum().item()
    print(f"OK: GPU={name} sm={cap[0]}{cap[1]} probe_sum={result}")
    sys.exit(0)
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    sys.exit(1)
PYEOF
    )
    PROBE_EXIT=$?

    if [ $PROBE_EXIT -eq 0 ]; then
        info "✅ CUDA probe PASSED: $PROBE_RESULT"
        info "   GPU is fully usable. No action needed."
    else
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        err  "⚠️  CUDA PROBE FAILED — GPU WILL NOT BE USED!"
        err  "   Probe output : $PROBE_RESULT"
        err  "   GPU          : ${GPU_NAME}"
        err  "   CUDA driver  : ${CUDA_DRIVER_VER}"
        err  "   PyTorch wheel: ${TORCH_CU}"
        echo ""
        err  "   Possible causes:"
        err  "   1. Driver version mismatch — run 'nvidia-smi' and verify CUDA version"
        err  "   2. GPU compute capability not supported by this wheel"
        err  "      P104-100 (sm_61) needs cu118+ with Pascal kernels"
        err  "   3. Try: pip install torch --index-url https://download.pytorch.org/whl/cu118"
        err  "      then re-run this script to re-probe"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        err  "SETUP INCOMPLETE. Fix the GPU issue above before deploying."
        exit 1
    fi
fi
# If we reach here, GPU is confirmed working (or FORCE_CPU_SETUP was set).

# ── Done ─────────────────────────────────────────────────────
info "=============================================="
info "✅  Setup complete!"
info "=============================================="
info "  Python  : $($PYTHON_BIN --version)"
info "  Node    : $(node --version)"
info "  Java    : $(java --version 2>&1 | head -1)"
info "  GPU     : ${GPU_NAME:-None (CPU only)}"
info "  CUDA    : ${CUDA_DRIVER_VER:-N/A}"
info "  PyTorch : $($PYTHON_BIN -c 'import torch; print(torch.__version__)' 2>/dev/null)"
info ""
info "Log saved to: $LOG"
info "Next step: bash deploy_nodocker.sh"
