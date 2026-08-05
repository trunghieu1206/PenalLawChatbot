"""
core/config.py — VNPLaw AI Service
All module-level constants and device detection.

IMPORTANT: This file imports torch. In main.py, OMP_NUM_THREADS and related
env-vars MUST be set BEFORE 'from app.core.config import ...' is reached.
The new main.py enforces this ordering explicitly.
"""

import os
from datetime import date, timezone, timedelta

# NOTE: torch is imported here after env-vars are already set by main.py
import torch


# ─── Model / DB config ────────────────────────────────────────────
MILVUS_URI      = os.getenv("MILVUS_DB_PATH", "./VN_law_lora.db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "legal_rag_lora")
TOP_K           = int(os.getenv("TOP_K", "15"))
LLM_MODEL       = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")

# Fine-tuned Jina v5 Nano adapter — default is the production model.
# Override via EMBEDDING_ADAPTER env var only if you want a different model.
_DEFAULT_EMBEDDING = (
    "trunghieu1206/"
    "jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05"
)

# Output fields fetched from Milvus for every document
OUTPUT_FIELDS = [
    "content", "article_number", "title",
    "chapter", "source", "effective_start", "effective_end",
]


# ─── Device detection ─────────────────────────────────────────────────────
def _detect_device() -> str:
    """Auto-detect the best available compute device for this worker process.

    GPU distribution strategy (multi-GPU servers)
    -----------------------------------------------
    uvicorn --workers N spawns N independent processes. We use:
        gpu_index = os.getpid() % gpu_count
    so that workers round-robin across all available GPUs automatically.

    Decision tree
    -------------
    1. FORCE_CPU=1 env var         → always "cpu"  (explicit override)
    2. nvidia-smi / CUDA absent    → "cpu"  (CPU-only server)
    3. 0 CUDA devices visible      → "cpu"
    4. CUDA kernel probe fails     → "cpu"  (bad driver / container issue)
    5. 1 GPU                       → "cuda:0"
    6. N GPUs (N>=2)               → "cuda:{pid % N}"  (round-robin)

    Never raises — the service always starts.
    """
    if os.getenv("FORCE_CPU", "0") == "1":
        print("⚙️  FORCE_CPU=1 — Using CPU (explicit override).")
        return "cpu"

    if not torch.cuda.is_available():
        print(
            "⚠️  CUDA not available — falling back to CPU.\n"
            "   (Install NVIDIA drivers + matching PyTorch wheel to enable GPU.)"
        )
        return "cpu"

    gpu_count = torch.cuda.device_count()
    if gpu_count == 0:
        print("⚠️  CUDA is available but device_count()=0 — falling back to CPU.")
        return "cpu"

    gpu_idx = os.getpid() % gpu_count
    device   = f"cuda:{gpu_idx}"

    try:
        probe = torch.zeros(1, device=device)
        _ = probe + 1  # triggers actual kernel dispatch
        del probe
        gpu_name  = torch.cuda.get_device_name(gpu_idx)
        cap       = torch.cuda.get_device_capability(gpu_idx)
        total_mem = torch.cuda.get_device_properties(gpu_idx).total_memory // (1024 ** 2)
        print(
            f"⚙️  GPU {gpu_idx}/{gpu_count}: {gpu_name} "
            f"({total_mem} MB VRAM, sm_{cap[0]}{cap[1]}) — PID {os.getpid()}"
        )
        return device
    except Exception as e:
        print(
            f"⚠️  GPU {gpu_idx} probe failed ({type(e).__name__}: {e})\n"
            f"   Falling back to CPU. To fix GPU:\n"
            f"   - Check driver: nvidia-smi\n"
            f"   - Reinstall matching PyTorch CUDA wheel (cu118 / cu121 / cu124)\n"
            f"   - Re-run deploy_nodocker.sh"
        )
        return "cpu"


DEVICE = _detect_device()


# ─── Legal domain constants ────────────────────────────────────────────────────

# Fields required to proceed past clarification check.
# IMPORTANT: Only include fields that MUST be explicitly present in the case.
REQUIRED_FIELDS = {
    "hanh_vi":       "mô tả hành vi phạm tội (bị cáo đã làm gì?)",
    "ngay_pham_toi": "ngày xảy ra hành vi phạm tội (dd/mm/yyyy)",
}

_MIN_SUPPORTED_DATE = date(2000, 7, 1)

_VN_TZ = timezone(timedelta(hours=7))

_EDITION_RANGES = [
    ("BLHS 1999",                  date(2000, 7, 1),  date(2010, 1, 1)),
    ("BLHS 1999 (sửa đổi 2009)",  date(2010, 1, 1),  date(2018, 1, 1)),
    ("BLHS 2015 (sửa đổi 2017)",  date(2018, 1, 1),  date(2025, 7, 1)),
    ("BLHS 2015 (sửa đổi 2025)",  date(2025, 7, 1),  date(9999, 1, 1)),
]

_ALWAYS_KEEP_BY_EDITION = {
    "BLHS 1999":                  {"7", "11", "13", "14", "15", "16", "17", "18", "19", "20",
                                   "28", "41", "45", "46", "47", "48", "49", "50", "51", "52", "60"},
    "BLHS 1999 (sửa đổi 2009)": {"7", "11", "13", "14", "15", "16", "17", "18", "19", "20",
                                   "28", "41", "45", "46", "47", "48", "49", "50", "51", "52", "60"},
    "BLHS 2015 (sửa đổi 2017)": {"7", "14", "15", "16", "17", "20", "21", "22", "23",
                                   "32", "38", "47", "48", "50", "51", "52", "53", "54", "55", "56", "57", "59", "65"},
    "BLHS 2015 (sửa đổi 2025)": {"7", "14", "15", "16", "17", "20", "21", "22", "23",
                                   "32", "38", "47", "48", "50", "51", "52", "53", "54", "55", "56", "57", "59", "65"},
}

_PINNED_MAP = {
    ("mitigating",        "BLHS 1999"):                 "46",
    ("aggravating",       "BLHS 1999"):                 "48",
    ("recidivism",        "BLHS 1999"):                 "49",
    ("below_min",         "BLHS 1999"):                 "47",
    ("attempt",           "BLHS 1999"):                 "18",
    ("consolidate",       "BLHS 1999"):                 "50",
    ("suspended",         "BLHS 1999"):                 "60",
    ("civil_comp",        "BLHS 1999"):                 "42",
    ("penalty_types",     "BLHS 1999"):                 "28",
    ("sentencing_basis",  "BLHS 1999"):                 "45",
    ("retroactive",       "BLHS 1999"):                 "7",
    ("self_defense",      "BLHS 1999"):                 "15",
    ("necessity",         "BLHS 1999"):                 "16",
    ("co_participation",  "BLHS 1999"):                 "20",
    ("exemption",         "BLHS 1999"):                 "25",
    ("mitigating",        "BLHS 1999 (sửa đổi 2009)"): "46",
    ("aggravating",       "BLHS 1999 (sửa đổi 2009)"): "48",
    ("recidivism",        "BLHS 1999 (sửa đổi 2009)"): "49",
    ("below_min",         "BLHS 1999 (sửa đổi 2009)"): "47",
    ("attempt",           "BLHS 1999 (sửa đổi 2009)"): "18",
    ("consolidate",       "BLHS 1999 (sửa đổi 2009)"): "50",
    ("suspended",         "BLHS 1999 (sửa đổi 2009)"): "60",
    ("civil_comp",        "BLHS 1999 (sửa đổi 2009)"): "42",
    ("penalty_types",     "BLHS 1999 (sửa đổi 2009)"): "28",
    ("sentencing_basis",  "BLHS 1999 (sửa đổi 2009)"): "45",
    ("retroactive",       "BLHS 1999 (sửa đổi 2009)"): "7",
    ("self_defense",      "BLHS 1999 (sửa đổi 2009)"): "15",
    ("necessity",         "BLHS 1999 (sửa đổi 2009)"): "16",
    ("co_participation",  "BLHS 1999 (sửa đổi 2009)"): "20",
    ("exemption",         "BLHS 1999 (sửa đổi 2009)"): "25",
    ("mitigating",        "BLHS 2015 (sửa đổi 2017)"): "51",
    ("aggravating",       "BLHS 2015 (sửa đổi 2017)"): "52",
    ("recidivism",        "BLHS 2015 (sửa đổi 2017)"): "53",
    ("below_min",         "BLHS 2015 (sửa đổi 2017)"): "54",
    ("attempt",           "BLHS 2015 (sửa đổi 2017)"): "57",
    ("consolidate",       "BLHS 2015 (sửa đổi 2017)"): "55",
    ("suspended",         "BLHS 2015 (sửa đổi 2017)"): "65",
    ("civil_comp",        "BLHS 2015 (sửa đổi 2017)"): "48",
    ("penalty_types",     "BLHS 2015 (sửa đổi 2017)"): "32",
    ("sentencing_basis",  "BLHS 2015 (sửa đổi 2017)"): "50",
    ("retroactive",       "BLHS 2015 (sửa đổi 2017)"): "7",
    ("self_defense",      "BLHS 2015 (sửa đổi 2017)"): "22",
    ("necessity",         "BLHS 2015 (sửa đổi 2017)"): "23",
    ("co_participation",  "BLHS 2015 (sửa đổi 2017)"): "17",
    ("exemption",         "BLHS 2015 (sửa đổi 2017)"): "59",
    ("mitigating",        "BLHS 2015 (sửa đổi 2025)"): "51",
    ("aggravating",       "BLHS 2015 (sửa đổi 2025)"): "52",
    ("recidivism",        "BLHS 2015 (sửa đổi 2025)"): "53",
    ("below_min",         "BLHS 2015 (sửa đổi 2025)"): "54",
    ("attempt",           "BLHS 2015 (sửa đổi 2025)"): "57",
    ("consolidate",       "BLHS 2015 (sửa đổi 2025)"): "55",
    ("suspended",         "BLHS 2015 (sửa đổi 2025)"): "65",
    ("civil_comp",        "BLHS 2015 (sửa đổi 2025)"): "48",
    ("penalty_types",     "BLHS 2015 (sửa đổi 2025)"): "32",
    ("sentencing_basis",  "BLHS 2015 (sửa đổi 2025)"): "50",
    ("retroactive",       "BLHS 2015 (sửa đổi 2025)"): "7",
    ("self_defense",      "BLHS 2015 (sửa đổi 2025)"): "22",
    ("necessity",         "BLHS 2015 (sửa đổi 2025)"): "23",
    ("co_participation",  "BLHS 2015 (sửa đổi 2025)"): "17",
    ("exemption",         "BLHS 2015 (sửa đổi 2025)"): "59",
}

# Purposes always pinned for each role
_PINNED_PURPOSES = {
    "neutral": ["retroactive", "mitigating", "aggravating", "consolidate", "attempt", "co_participation", "sentencing_basis"],
    "defense": ["retroactive", "mitigating", "below_min", "attempt", "suspended", "exemption", "co_participation", "sentencing_basis"],
    "victim":  ["retroactive", "aggravating", "recidivism", "civil_comp", "penalty_types", "co_participation", "sentencing_basis"],
}

_ROLE_CIRCUMSTANCE_INSTRUCTION = {
    "neutral": (
        "Mô tả các tình tiết tăng nặng VÀ giảm nhẹ có trong vụ án một cách trung lập. "
        "Ví dụ: bị cáo có tiền án / thành khẩn khai báo / bồi thường thiệt hại / dùng hung khí."
    ),
    "defense": (
        "Mô tả CHỈ các tình tiết giảm nhẹ có trong vụ án. "
        "Ví dụ: bị cáo thành khẩn khai báo, ăn năn hối cải, phạm tội lần đầu, "
        "bồi thường thiệt hại, hoàn cảnh khó khăn, tuổi trẻ."
    ),
    "victim": (
        "Mô tả CHỈ các tình tiết tăng nặng và hậu quả nghiêm trọng có trong vụ án. "
        "Ví dụ: bị cáo có tiền án, dùng hung khí nguy hiểm, có tổ chức, "
        "nạn nhân bị thương nặng / tử vong, thiệt hại tài sản lớn."
    ),
}

_MAX_SEMANTIC_DOCS = 8

