#!/usr/bin/env bash
# =============================================================
# deploy_nodocker.sh
# Deploys ALL services DIRECTLY (no Docker) on a containerized
# GPU server (Ubuntu 22.04, root, no systemd, no iptables).
#
# OPTIMIZED:
#   - Skips npm install / mvn build if nothing changed
#   - Maven offline flag after first run
#   - PostgreSQL skipped if already running
#   - DB restore only if DB is empty
#   - Validates AI imports before launching
#
# Services:
#   - PostgreSQL         (port 5432, via pg_ctlcluster)
#   - AI Service         (port 8000, uvicorn)
#   - Spring Boot        (port 8080, java -jar)
#   - Frontend           (port 80,   nginx static)
#
# Usage:
#   bash deploy_nodocker.sh
# =============================================================
set -euo pipefail

# Bypass PEP 668 on Ubuntu 24.04 for global pip installs
export PIP_BREAK_SYSTEM_PACKAGES=1

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
skip()  { echo -e "${YELLOW}[SKIP]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

REPO_URL="https://github.com/trunghieu1206/PenalLawChatbot"
PROJECT_DIR="/root/PenalLawChatbot"
LOG_DIR="/var/log/penallaw"
BRANCH="dev"
mkdir -p "$LOG_DIR"
chmod 777 "$LOG_DIR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀  PenalLawChatbot — Bare-Metal Deploy (no Docker)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 0. Self-install build tools (in case setup_server.sh was skipped) ──
if ! command -v java &>/dev/null || ! java -version 2>&1 | grep -q '21\.'; then
    info "Java 21 not found — installing..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openjdk-21-jdk
    _JAVA_BIN=$(readlink -f "$(which java)")
    export JAVA_HOME
    JAVA_HOME=$(dirname "$(dirname "$_JAVA_BIN")")
    grep -qxF "export JAVA_HOME=$JAVA_HOME" /root/.bashrc 2>/dev/null || \
        echo "export JAVA_HOME=$JAVA_HOME" >> /root/.bashrc
    info "Java: $(java --version 2>&1 | head -1)"
else
    _JAVA_BIN=$(readlink -f "$(which java)")
    export JAVA_HOME
    JAVA_HOME=$(dirname "$(dirname "$_JAVA_BIN")")
    skip "Java: $(java --version 2>&1 | head -1)"
fi

if ! command -v mvn &>/dev/null; then
    info "Maven not found — installing..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends maven
    info "Maven: $(mvn --version | head -1)"
else
    skip "Maven: $(mvn --version | head -1)"
fi

if ! command -v node &>/dev/null || ! node --version | grep -qP 'v2[0-9]'; then
    info "Node.js 20 not found — installing..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs
    info "Node: $(node --version)"
else
    skip "Node: $(node --version)"
fi

# ── 1. Clone / pull repo ──────────────────────────────────────
# ── 1. Clone / pull repo ──────────────────────────────────────
# Strategy:
#   - .git exists            → pull latest
#   - ai-service/ exists     → full project present (uploaded via scp) → use as-is
#   - dir exists but empty   → created only for database/backups/ → clone fresh
#   - dir doesn't exist      → clone fresh
#
# In all clone cases: preserve database/backups/ and .env before wiping.

_clone_fresh() {
    local target="$1"
    # Preserve precious files before removing partial directory
    local _bk_db="" _bk_env=""
    if [ -d "$target/database/backups" ]; then
        _bk_db=$(mktemp -d)
        cp -r "$target/database/backups/." "$_bk_db/"
        info "  Preserved database/backups/ (will restore after clone)"
    fi
    if [ -f "$target/.env" ]; then
        _bk_env=$(mktemp)
        cp "$target/.env" "$_bk_env"
        info "  Preserved .env (will restore after clone)"
    fi

    rm -rf "$target"
    info "Cloning $REPO_URL ($BRANCH) → $target ..."
    git clone --branch "$BRANCH" "$REPO_URL" "$target"

    # Restore preserved files
    if [ -n "$_bk_db" ] && [ -d "$_bk_db" ]; then
        mkdir -p "$target/database/backups"
        cp -r "$_bk_db/." "$target/database/backups/"
        rm -rf "$_bk_db"
        info "  Restored database/backups/"
    fi
    if [ -n "$_bk_env" ] && [ -f "$_bk_env" ]; then
        cp "$_bk_env" "$target/.env"
        rm -f "$_bk_env"
        info "  Restored .env"
    fi
}

if [ -d "$PROJECT_DIR/.git" ]; then
    info "Git repo found — pulling latest ($BRANCH)..."
    git -C "$PROJECT_DIR" checkout "$BRANCH"
    git -C "$PROJECT_DIR" pull origin "$BRANCH"
elif [ -d "$PROJECT_DIR/ai-service" ]; then
    # Full project present (uploaded via scp) — use as-is, no clone needed
    warn "Project directory found (non-git, uploaded via scp). Using as-is."
else
    # Directory missing OR exists but is incomplete (e.g. only database/backups/ was created)
    warn "Project incomplete or missing — cloning fresh (preserving any backups and .env)..."
    _clone_fresh "$PROJECT_DIR"
fi

cd "$PROJECT_DIR"
GIT_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "non-git")
GIT_COMMIT=$(git -C "$PROJECT_DIR" log -1 --oneline 2>/dev/null || echo "local")
info "Branch: $GIT_BRANCH  |  Commit: $GIT_COMMIT"

# ── 2. Setup .env ─────────────────────────────────────────────
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        info "Copied .env.example → .env"
    else
        # .env.example not on server — generate with all defaults pre-filled.
        # JWT_SECRET is pre-filled (same value as .env.example in the repo).
        # User only needs to fill in OPENROUTER_API_KEY and HF_TOKEN.
        # AI service vars (model names, TOP_K, etc.) are NOT here — they live in main.py.
        cat > .env <<'ENVEOF'
# Fill in ONLY these two values:
OPENROUTER_API_KEY=your_openrouter_api_key_here
HF_TOKEN=your_huggingface_token_here

# Pre-filled — do not change:
JWT_SECRET=j1WQjbYqkjImzp0etlJQRgI4alxtRxGTgAJalevJKKDAuuHFm2gbPNXcRxMzYNQ1nUJd6hYNVPkScjrEZr0aGA==
POSTGRES_DB=penallaw
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
ENVEOF
    fi
    warn ">>> Edit .env — fill in OPENROUTER_API_KEY and HF_TOKEN <<<"
    warn "    sudo nano $PROJECT_DIR/.env"
    echo ""
    read -rp "Press ENTER after editing .env..." _
fi

set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport

[ -z "${OPENROUTER_API_KEY:-}" ] && error "OPENROUTER_API_KEY not set in .env"
[ -z "${JWT_SECRET:-}" ]         && error "JWT_SECRET not set in .env"
info ".env loaded."

# ── 3. PostgreSQL ─────────────────────────────────────────────
if ! command -v psql &>/dev/null; then
    info "Installing PostgreSQL..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        postgresql postgresql-contrib libpq-dev
    info "PostgreSQL installed."
else
    skip "PostgreSQL: $(psql --version)"
fi

PG_VERSION=$(ls /etc/postgresql/ 2>/dev/null | sort -V | tail -1 || echo "")
[ -z "$PG_VERSION" ] && error "PostgreSQL config not found in /etc/postgresql/"

PG_LOG="$LOG_DIR/postgres.log"
touch "$PG_LOG" && chmod 666 "$PG_LOG" 2>/dev/null || true

if ! pg_isready -q 2>/dev/null; then
    info "Starting PostgreSQL $PG_VERSION..."
    pg_dropcluster --stop "$PG_VERSION" main 2>/dev/null || true
    pg_createcluster "$PG_VERSION" main 2>/dev/null || true
    pg_ctlcluster "$PG_VERSION" main start -- -l "$PG_LOG"
    sleep 3
else
    skip "PostgreSQL already running"
fi
pg_isready || error "PostgreSQL failed to start. Check: $PG_LOG"

# Create DB/user
DB_NAME="${POSTGRES_DB:-penallaw}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASS="${POSTGRES_PASSWORD:-postgres}"

su -c "cd /tmp && psql -c \"ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';\"" postgres 2>/dev/null || true
su -c "cd /tmp && createdb $DB_NAME" postgres 2>/dev/null || true   # silently fails if exists
info "Database '$DB_NAME' ready."

# Auto-restore backup if DB is empty OR if laws table has no data.
# BUG-FIX: The old guard (TABLE_COUNT > 0 → skip) was too naive.
# Hibernate ddl-auto=update creates empty tables on first boot BEFORE this
# restore check runs. This caused the restore to be skipped even though laws
# had 0 rows. We now check the laws row count directly.
BACKUP_DIR="$PROJECT_DIR/database/backups"
TABLE_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public';\"" postgres 2>/dev/null || echo "0")
LAWS_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM laws;\"" postgres 2>/dev/null || echo "0")

if [ "${LAWS_COUNT:-0}" -gt 0 ]; then
    skip "DB already populated — laws: ${LAWS_COUNT:-0} articles in $TABLE_COUNT tables — skipping restore."
elif [ -d "$BACKUP_DIR" ]; then
    # Laws is empty (0 rows) — restore regardless of whether tables exist.
    if [ "${TABLE_COUNT:-0}" -gt 0 ]; then
        info "DB has $TABLE_COUNT tables but laws is empty — forcing restore (Hibernate may have created empty tables)."
    else
        info "DB is empty — restoring from backup..."
    fi

    if [ -f "$BACKUP_DIR/penallaw_combined_backup.sql" ]; then
        LATEST_BACKUP="$BACKUP_DIR/penallaw_combined_backup.sql"
    else
        LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/penallaw_backup_*.sql 2>/dev/null | head -1 || echo "")
    fi

    if [ -n "$LATEST_BACKUP" ] && [ -f "$LATEST_BACKUP" ]; then
        info "Restoring from: $(basename "$LATEST_BACKUP")..."
        su -c "cd /tmp && dropdb --if-exists $DB_NAME" postgres 2>/dev/null || true
        su -c "cd /tmp && createdb $DB_NAME" postgres 2>/dev/null || true
        TEMP_BACKUP="/tmp/penallaw_restore_$$.sql"
        cp "$LATEST_BACKUP" "$TEMP_BACKUP"
        chmod 644 "$TEMP_BACKUP"
        if su - postgres -c "psql $DB_NAME < \"$TEMP_BACKUP\" 2>&1" >> "$PG_LOG" 2>&1; then
            LAWS_COUNT=$(su -c "cd /tmp && psql -d $DB_NAME -tAc \"SELECT count(*) FROM laws;\"" postgres 2>/dev/null || echo "0")
            info "✅ Database restored — laws: ${LAWS_COUNT:-0} articles"
        else
            warn "⚠️  Restore had warnings — check $PG_LOG"
        fi
        rm -f "$TEMP_BACKUP"
    else
        info "No backup found — starting with empty database (Spring Boot will auto-create tables)."
    fi
fi

# Idempotent schema migration for sentencing_data column
su - postgres -c "psql $DB_NAME -c \"ALTER TABLE IF EXISTS chat_messages ADD COLUMN IF NOT EXISTS sentencing_data TEXT;\"" 2>/dev/null || true

# ── 4. Check Milvus DB (VN_law_lora.db) ──────────────────────
MILVUS_DB="$PROJECT_DIR/ai-service/VN_law_lora.db"
if [ -f "$MILVUS_DB" ]; then
    MILVUS_SIZE=$(du -h "$MILVUS_DB" | cut -f1)
    info "Milvus DB found: $MILVUS_DB ($MILVUS_SIZE)"
else
    warn "⚠️  Milvus DB not found at $MILVUS_DB"
    warn "    Upload it with: scp VN_law_lora.db root@SERVER:$PROJECT_DIR/ai-service/"
    warn "    AI service will start but retrieval will return no results."
fi

# ── 5. AI Service (FastAPI / uvicorn) ────────────────────────
info "Preparing AI service..."
cd "$PROJECT_DIR/ai-service"

# Find Python with torch (prefer conda env which has GPU torch pre-installed)
AI_PYTHON=""
for py in /opt/conda/bin/python3 /usr/bin/python3.11 /usr/bin/python3.10 /usr/bin/python3; do
    if [ -x "$py" ] && "$py" -c "import torch" 2>/dev/null; then
        AI_PYTHON="$py"
        TORCH_VER=$("$py" -c "import torch; print(torch.__version__)" 2>/dev/null)
        CUDA_AVAIL=$("$py" -c "import torch; print(torch.cuda.is_available())" 2>/dev/null)
        info "Python: $("$py" --version)  |  torch: $TORCH_VER  |  CUDA: $CUDA_AVAIL"
        break
    fi
done
[ -z "$AI_PYTHON" ] && error "No Python with torch found. Ensure torch is installed (setup_server.sh)."

# Ensure torch cu-tag matches the installed NVIDIA driver version.
# cu124 requires driver ≥R550; cu121 requires R530; cu118 works from R520.
# Using a cu tag NEWER than what the driver supports → CUDA unknown error (999).
if command -v nvidia-smi &>/dev/null; then
    _DRIVER_CUDA=$(nvidia-smi 2>/dev/null | grep -oP "CUDA Version:\s*\K[0-9]+" | head -1 || echo "0")
    _TORCH_CU=$("$AI_PYTHON" -c "import torch; v=torch.__version__; print(v.split('+')[1] if '+' in v else 'cpu')" 2>/dev/null || echo "cpu")
    info "  Driver CUDA cap: ${_DRIVER_CUDA}.x  |  torch cu-tag: $_TORCH_CU"

    # Map driver version to the HIGHEST compatible PyTorch cu-tag.
    # PyTorch cu-tag compatibility:
    #   cu124 → CUDA 12.4 → requires driver >= R550 (550.x)
    #   cu121 → CUDA 12.1 → requires driver >= R525 (525.x)  ← RTX 2080 R525 goes here
    #   cu118 → CUDA 11.8 → requires driver >= R520 (520.x)
    # Rule: pick the HIGHEST cu-tag that the driver can support.
    # torch 2.5.x does NOT have a cu118 wheel — min is cu121 for torch>=2.4.
    _DRV_MAJOR=$(nvidia-smi 2>/dev/null | grep -oP 'Driver Version: \K[0-9]+' | head -1 || echo "0")
    if   [ "$_DRV_MAJOR" -ge 550 ]; then _NEED_CU="cu124"   # CUDA 12.4+ (RTX 40xx, A100 new)
    elif [ "$_DRV_MAJOR" -ge 525 ]; then _NEED_CU="cu121"   # CUDA 12.1 — RTX 2080/3080 with R525+
    elif [ "$_DRV_MAJOR" -ge 520 ]; then _NEED_CU="cu118"   # CUDA 11.8 — older R520 drivers
    else                                  _NEED_CU="cu118"; fi # last resort
    info "  Driver major: R${_DRV_MAJOR} → selecting $_NEED_CU"

    if [ "$_TORCH_CU" != "$_NEED_CU" ] && [ "$_TORCH_CU" != "cpu" ]; then
        info "  torch $_TORCH_CU is incompatible with R${_DRV_MAJOR} driver — reinstalling with $_NEED_CU..."
        _TORCH_BASE=$("$AI_PYTHON" -c "import torch; print(torch.__version__.split('+')[0])" 2>/dev/null || echo "2.5.1")

        # torchvision version formula: torch 2.5.x → 0.20.x, torch 2.4.x → 0.19.x
        _calc_tv() {
            python3 -c "p='$1'.split('.')[:2]; print(f'0.{int(p[0])*10+int(p[1])-5}.0')" 2>/dev/null || echo "0.20.0"
        }

        # Try the exact torch version first.
        # Fallback order: same base with target cu-tag, then 2.5.1, then 2.4.1.
        # Note: torch 2.5.x has cu121 and cu124 wheels but NOT cu118.
        #       torch 2.4.x has cu118, cu121, cu124 wheels.
        _REINSTALL_OK=false
        _fallback_bases=("$_TORCH_BASE")
        [ "$_TORCH_BASE" != "2.5.1" ] && _fallback_bases+=("2.5.1")
        [ "$_NEED_CU" = "cu118" ]     && _fallback_bases+=("2.4.1")  # 2.5.x has no cu118

        for _TBASE in "${_fallback_bases[@]}"; do
            _TVBASE=$(_calc_tv "$_TBASE")
            info "  Trying torch==${_TBASE}+${_NEED_CU} torchvision==${_TVBASE}+${_NEED_CU}..."
            if "$AI_PYTHON" -m pip install \
                    "torch==${_TBASE}+${_NEED_CU}" \
                    "torchvision==${_TVBASE}+${_NEED_CU}" \
                    --index-url "https://download.pytorch.org/whl/${_NEED_CU}" \
                    --quiet 2>&1 | tail -3; then
                _REINSTALL_OK=true
                info "  ✅ Reinstalled torch ${_TBASE}+${_NEED_CU}"
                break
            fi
            warn "  torch ${_TBASE}+${_NEED_CU} unavailable — trying fallback..."
        done
        if [ "$_REINSTALL_OK" = false ]; then
            warn "  torch cu-tag reinstall failed — will try to fix device nodes and test CUDA."
        fi
        TORCH_VER=$("$AI_PYTHON" -c "import torch; print(torch.__version__)" 2>/dev/null)
        info "  torch after fix: $TORCH_VER"
    fi
fi

# NEVER pip-upgrade torch here. Upgrading torch breaks torchvision (version coupling).
if command -v nvidia-smi &>/dev/null; then
    # Auto-fix: ensure /dev/nvidia-uvm exists.
    # Without it, torch.cuda.is_available() = False (CUDA error 999) even when
    # nvidia-smi and /dev/nvidia0 are present.
    if [ ! -e /dev/nvidia-uvm ]; then
        info "  /dev/nvidia-uvm missing — attempting fix..."

        # Strategy 1: nvidia-modprobe (the official tool — creates /dev/nvidia* nodes).
        # Install it first if missing (it's in the nvidia driver package).
        if ! command -v nvidia-modprobe &>/dev/null; then
            info "  Installing nvidia-modprobe..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
                nvidia-modprobe 2>/dev/null || true
        fi
        if command -v nvidia-modprobe &>/dev/null && nvidia-modprobe -u -c 0 2>/dev/null; then
            info "  ✅ /dev/nvidia-uvm created via nvidia-modprobe."

        # Strategy 2: modprobe (bare metal; fails inside containers)
        elif modprobe nvidia-uvm 2>/dev/null; then
            info "  ✅ nvidia-uvm loaded via modprobe."
            grep -qxF "nvidia-uvm" /etc/modules 2>/dev/null || echo "nvidia-uvm" >> /etc/modules

        # Strategy 3: mknod (container/LXC — host kernel has module, but modprobe is blocked;
        # works if nvidia-uvm *major number* is listed in /proc/devices)
        elif _UVM_MAJOR=$(awk '/nvidia-uvm/{print $1}' /proc/devices 2>/dev/null) && [ -n "$_UVM_MAJOR" ]; then
            info "  modprobe blocked (container) — creating device nodes via mknod (major=$_UVM_MAJOR)..."
            mknod /dev/nvidia-uvm       c "$_UVM_MAJOR" 0 2>/dev/null && \
            mknod /dev/nvidia-uvm-tools c "$_UVM_MAJOR" 1 2>/dev/null || true
            chmod 666 /dev/nvidia-uvm /dev/nvidia-uvm-tools 2>/dev/null || true
            [ -e /dev/nvidia-uvm ] && info "  ✅ /dev/nvidia-uvm created via mknod." || \
                warn "  mknod failed — /dev/nvidia-uvm still absent."

        else
            warn "  All strategies failed — nvidia-uvm not in /proc/devices."
            warn "  GPU is visible but kernel UVM module is not loaded."
        fi
    fi

    _TORCH_CUDA=$("$AI_PYTHON" -c "import torch; print(torch.cuda.is_available())" 2>/dev/null || echo "False")
    if [ "$_TORCH_CUDA" = "True" ]; then
        _GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")
        _GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "?")
        info "✅ GPU ready: $_GPU_NAME ($_GPU_MEM) | torch CUDA: True"
    else
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo -e "${RED}[ERR]${NC}   GPU detected (nvidia-smi OK) but CUDA is NOT available."
        echo -e "${RED}[ERR]${NC}   torch.cuda.is_available() = False"
        echo ""
        echo -e "${YELLOW}[HELP]${NC}  This is a driver/kernel issue — NOT a PyTorch issue."
        echo -e "${YELLOW}[HELP]${NC}  Likely cause: the GPU container is missing CUDA device files."
        echo ""
        echo -e "${YELLOW}[HELP]${NC}  Try these steps on the HOST (not inside the container):"
        echo -e "${YELLOW}[HELP]${NC}    1. nvidia-modprobe -u    # load nvidia-uvm kernel module"
        echo -e "${YELLOW}[HELP]${NC}    2. ls /dev/nvidia*       # should show nvidia0, nvidia-uvm, etc."
        echo -e "${YELLOW}[HELP]${NC}    3. Re-run this script."
        echo ""
        echo -e "${YELLOW}[HELP]${NC}  If on a rental server: contact your provider to enable"
        echo -e "${YELLOW}[HELP]${NC}  'CUDA compute access' or to run nvidia-modprobe on the host."
        echo -e "${YELLOW}[HELP]${NC}  Some providers expose this as a 'GPU passthrough' toggle."
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        read -rp "You have a GPU but CUDA is unavailable. Continue on CPU anyway? [y/N] " _CONTINUE_CPU
        if [[ ! "$_CONTINUE_CPU" =~ ^[Yy]$ ]]; then
            error "Aborted. Fix CUDA access and re-run."
        fi
        warn "Continuing on CPU as requested. Inference will be significantly slower."
    fi
else
    warn "nvidia-smi not found — deploying in CPU-only mode."
fi

# Install AI service deps (excluding torch to avoid downgrading conda's GPU torch)
info "Installing AI service requirements..."

# Record CUDA state BEFORE pip install — needed to detect if pip downgraded torch.
_TORCH_CUDA_BEFORE=$("$AI_PYTHON" -c "import torch; print(torch.cuda.is_available())" 2>/dev/null || echo "False")

if "$AI_PYTHON" -c "import fastapi, transformers, langchain, pymilvus" 2>/dev/null; then
    skip "AI service requirements already installed."
else
    "$AI_PYTHON" -m pip install "setuptools>=70.0" --quiet 2>&1 | tail -2 || true
    # Exclude torch AND torchvision — we will reinstall the exact matching torchvision below
    grep -v "^torch" "$PROJECT_DIR/ai-service/requirements.txt" \
        | grep -v "^torchvision" \
        > /tmp/requirements_notorch.txt
    "$AI_PYTHON" -m pip install --ignore-installed typing_extensions --quiet -r /tmp/requirements_notorch.txt 2>&1 | tail -5 || \
        error "Failed to install AI dependencies. Check pip output above."

    # Fix Numpy 2.0 ABI incompatibilities with scipy
    "$AI_PYTHON" -m pip install "numpy>=2.0.0" "scipy>=1.13.0" --upgrade --quiet 2>&1 | tail -2 || true

    # Install FlagEmbedding (required by bge-reranker-v2-m3)
    "$AI_PYTHON" -m pip install "FlagEmbedding>=1.2.0" --quiet 2>&1 | tail -2 || true
fi

# Uninstall torchvision entirely — our AI service does NOT use it.
# (Jina embeddings + CrossEncoder + Milvus + LangChain have no image processing needs.)
# transformers imports torchvision only for image features and gracefully skips it when absent.
# Keeping a mismatched torchvision (e.g. CPU build vs CUDA torch, or wrong cu-tag) causes:
#   RuntimeError: operator torchvision::nms does not exist
# The safest fix for any server is to simply remove it.
info "Removing torchvision (not needed by this AI service, prevents nms crash)..."
"$AI_PYTHON" -m pip uninstall torchvision -y --quiet 2>&1 | tail -1 || true

# Always enforce pymilvus 2.4.x — pymilvus 3.0 breaks MilvusLite by treating .db file as a directory.
# --force-reinstall: ensure the downgrade happens even if pip thinks it's already installed.
# --break-system-packages: required on Ubuntu 24.04 (PEP 668) to overwrite system-managed packages.
info "Enforcing pymilvus<2.5 (required for legacy SQLite VN_law_lora.db)..."
"$AI_PYTHON" -m pip install \
    "milvus-lite>=2.4.9,<2.5.0" "pymilvus>=2.4.0,<2.5.0" \
    --force-reinstall --break-system-packages \
    --quiet 2>&1 | tail -3 || \
"$AI_PYTHON" -m pip install \
    "milvus-lite>=2.4.9,<2.5.0" "pymilvus>=2.4.0,<2.5.0" \
    --force-reinstall \
    --quiet 2>&1 | tail -3 || true

# Verify the version is actually 2.4.x (fail loudly if not, so we catch silent failures)
_milvus_ver=$("$AI_PYTHON" -c "import pymilvus; print(pymilvus.__version__)" 2>/dev/null || echo "unknown")
info "  pymilvus installed version: $_milvus_ver"
case "$_milvus_ver" in
    2.4.*) info "  ✅ pymilvus $_milvus_ver — correct (2.4.x)" ;;
    *)     warn "  ⚠️  pymilvus $_milvus_ver is NOT 2.4.x — MilvusLite may fail. Run manually: pip install 'pymilvus>=2.4.0,<2.5.0' --force-reinstall --break-system-packages" ;;
esac

# Verify torch CUDA state AFTER all installs.
# Only fail if CUDA was working BEFORE pip but is now broken (pip downgraded torch).
# If CUDA was already False before pip (e.g. driver/container issue), that is NOT pip's fault.
_TORCH_CUDA_AFTER=$("$AI_PYTHON" -c "import torch; print(torch.cuda.is_available())" 2>/dev/null || echo "False")
if command -v nvidia-smi &>/dev/null; then
    if [ "$_TORCH_CUDA_BEFORE" = "True" ] && [ "$_TORCH_CUDA_AFTER" = "False" ]; then
        error "pip install downgraded CUDA torch! A package (e.g. sentence-transformers) pulled in a CPU torch. Fix: reinstall torch with the correct cu-tag after all other deps."
    elif [ "$_TORCH_CUDA_AFTER" = "False" ]; then
        warn "CUDA still unavailable after pip install (was also unavailable before — driver/container issue, not caused by pip). Continuing with CPU."
    fi
fi

info "AI service requirements installed."

# Verify critical imports before launching (fail fast, not at first request)
info "Verifying critical imports..."
"$AI_PYTHON" -c "
import torch
from transformers import AutoModel, AutoTokenizer
from sentence_transformers import CrossEncoder
import peft, transformers, uvicorn, fastapi, langchain_openai, langgraph
from peft import PeftModel
print(f'✅ All imports OK | torch={torch.__version__} | peft={peft.__version__} | transformers={transformers.__version__}')
" || error "Import verification FAILED — check pip installation above."

# Write .env for ai-service (credentials + infrastructure only)
# AI service config (model names, TOP_K, FORCE_CPU, etc.) are set as
# defaults in ai-service/app/main.py — no need to pass them via .env.
cat > .env <<EOF
OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
HF_TOKEN=${HF_TOKEN:-}
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=${DB_NAME}
POSTGRES_USER=${DB_USER}
POSTGRES_PASSWORD=${DB_PASS}
MILVUS_DB_PATH=${PROJECT_DIR}/ai-service/VN_law_lora.db
EOF

# Kill any existing AI service process
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
unset MILVUS_URI 2>/dev/null || true

nohup "$AI_PYTHON" -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --timeout-keep-alive 660 \
    > "$LOG_DIR/ai-service.log" 2>&1 &
AI_PID=$!
info "AI service started (PID $AI_PID). Log: $LOG_DIR/ai-service.log"

# ── 6. Backend (Spring Boot) ──────────────────────────────────
info "Building Spring Boot backend..."
cd "$PROJECT_DIR/backend"

# Only rebuild if source/pom changed since last JAR
LATEST_JAR=$(ls -t target/*.jar 2>/dev/null | head -1 || echo "")
NEED_BUILD=true
if [ -n "$LATEST_JAR" ]; then
    if ! find src -newer "$LATEST_JAR" -name "*.java" 2>/dev/null | grep -q . && \
       ! [ pom.xml -nt "$LATEST_JAR" ]; then
        NEED_BUILD=false
    fi
fi

if [ "$NEED_BUILD" = true ]; then
    info "Running Maven build (-DskipTests)..."
    MVN_LOG="$LOG_DIR/maven_build.log"

    # Pipe to tee so output appears live AND gets saved.
    # Use PIPESTATUS[0] to capture mvn's exit code (not tee's).
    mvn package -DskipTests 2>&1 | tee "$MVN_LOG"
    MVN_EXIT=${PIPESTATUS[0]}

    if [ "$MVN_EXIT" -ne 0 ]; then
        echo ""
        echo "━━━ Maven build FAILED — last 30 lines of $MVN_LOG ━━━"
        tail -30 "$MVN_LOG"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        error "Maven build failed. Full log: $MVN_LOG"
    fi

    LATEST_JAR=$(ls -t target/*.jar 2>/dev/null | head -1 || echo "")
    if [ -z "$LATEST_JAR" ]; then
        tail -20 "$MVN_LOG"
        error "Maven succeeded but no JAR found in target/. See log above."
    fi
    info "✅ Build complete: $(basename "$LATEST_JAR")"
else
    skip "JAR up-to-date ($(basename "$LATEST_JAR")) — skipping Maven build"
fi

export SPRING_DATASOURCE_URL="jdbc:postgresql://127.0.0.1:5432/${DB_NAME}"
export SPRING_DATASOURCE_USERNAME="${DB_USER}"
export SPRING_DATASOURCE_PASSWORD="${DB_PASS}"
export JWT_SECRET="${JWT_SECRET}"
export AI_SERVICE_URL="http://localhost:8000"

pkill -f "java.*penallaw" 2>/dev/null || pkill -f "java.*backend" 2>/dev/null || true
sleep 1

nohup java \
    -Xmx512m \
    -XX:ActiveProcessorCount=$(nproc) \
    -XX:+UseParallelGC \
    -jar "$LATEST_JAR" \
    --server.port=8080 \
    --spring.datasource.url="$SPRING_DATASOURCE_URL" \
    --spring.datasource.username="$SPRING_DATASOURCE_USERNAME" \
    --spring.datasource.password="$SPRING_DATASOURCE_PASSWORD" \
    --jwt.secret="$JWT_SECRET" \
    --ai-service.base-url="$AI_SERVICE_URL" \
    --ai-service.timeout-seconds=660 \
    > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
info "Backend started (PID $BACKEND_PID). Log: $LOG_DIR/backend.log"

# ── 7. Frontend (Nginx static) ────────────────────────────────
info "Building frontend..."
cd "$PROJECT_DIR/frontend"

# npm install only if package.json changed or node_modules missing
if [ ! -d node_modules ] || [ package.json -nt node_modules/.package-lock.json ] 2>/dev/null; then
    info "Running npm install..."
    npm install --prefer-offline --silent
else
    skip "node_modules up-to-date"
fi

# Rebuild only if src changed since last dist build
if [ ! -d dist ] || find src -newer dist/index.html 2>/dev/null | grep -q .; then
    info "Building frontend bundle..."
    npm run build --silent
    info "Frontend built."
else
    skip "frontend dist is up-to-date"
fi

# Ensure nginx is installed
if ! command -v nginx &>/dev/null; then
    info "Installing nginx..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nginx >/dev/null 2>&1 || true
fi
mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled

# nginx config
cat > /etc/nginx/sites-available/penallaw <<'NGINX'
server {
    listen 80;
    server_name _;
    root /root/PenalLawChatbot/frontend/dist;
    index index.html;

    # SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy /ai-api → FastAPI (AI service, port 8000)
    # CPU inference can take up to 10 min — timeouts must be > 660s client timeout
    location /ai-api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_read_timeout 720s;
        proxy_send_timeout 720s;
        proxy_connect_timeout 10s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Proxy /api → Spring Boot backend (port 8080)
    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_read_timeout 720s;
        proxy_send_timeout 720s;
        proxy_connect_timeout 10s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINX

# nginx needs read access to /root
chmod 755 /root
chmod -R 755 "$PROJECT_DIR/frontend/dist/"

ln -sf /etc/nginx/sites-available/penallaw /etc/nginx/sites-enabled/penallaw
rm -f /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tail -3

pkill nginx 2>/dev/null || true
sleep 1
nginx
info "nginx started."

# ── 8. Health checks ──────────────────────────────────────────
echo ""
info "Waiting for AI service to be ready (up to 300s)..."
info "  (First run: model loading takes 60-120s on GPU, 180-300s on CPU)"
_ai_ready=false
_ai_elapsed=0
_ai_last_log=0
while [ $_ai_elapsed -lt 300 ]; do
    if curl -sf --max-time 2 "http://localhost:8000/health" > /dev/null 2>&1; then
        info "  ✅ AI Service is up! (after ${_ai_elapsed}s)"
        _ai_ready=true
        break
    fi
    # Print last 2 lines of AI log every 10 seconds so user can see progress
    if [ $((_ai_elapsed % 10)) -eq 0 ] && [ $_ai_elapsed -gt 0 ]; then
        _last_lines=$(tail -2 "$LOG_DIR/ai-service.log" 2>/dev/null | tr '\n' ' ')
        printf "\r  ⏳ [%3ds] %s\n" "$_ai_elapsed" "$_last_lines"
    else
        printf "\r  ⏳ Elapsed: %3ds / 300s — waiting..." "$_ai_elapsed"
    fi
    sleep 1
    _ai_elapsed=$((_ai_elapsed + 1))
done
echo ""
[ "$_ai_ready" = false ] && warn "  ⚠️  AI Service not ready after 300s — check $LOG_DIR/ai-service.log"

info "Waiting for Backend to be ready (up to 60s)..."
_be_ready=false
for _i in $(seq 1 12); do   # 12 × 5s = 60s
    if curl -sf --max-time 5 "http://localhost:8080/actuator/health" > /dev/null 2>&1; then
        info "  ✅ Backend is up (after $((_i * 5))s)"
        _be_ready=true
        break
    fi
    sleep 5
done
[ "$_be_ready" = false ] && warn "  ⚠️  Backend not ready after 60s — check $LOG_DIR/backend.log"

if curl -sf --max-time 5 "http://localhost:80" > /dev/null 2>&1; then
    info "  ✅ Frontend (nginx) is up"
else
    warn "  ⚠️  Frontend not responding — check nginx logs"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "✅  Deploy complete (bare-metal, no Docker)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
info "Logs:"
info "  AI service : tail -f $LOG_DIR/ai-service.log"
info "  Backend    : tail -f $LOG_DIR/backend.log"
info "  PostgreSQL : tail -f $LOG_DIR/postgres.log"
info "  nginx      : tail -f /var/log/nginx/error.log"
echo ""
info "Restart tips:"
info "  AI service : pkill -f uvicorn; cd $PROJECT_DIR/ai-service && nohup $AI_PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> $LOG_DIR/ai-service.log 2>&1 &"
info "  Backend    : pkill -f java; nohup java -jar $PROJECT_DIR/backend/target/*.jar --server.port=8080 >> $LOG_DIR/backend.log 2>&1 &"
info "  nginx      : pkill nginx; nginx"
echo ""
