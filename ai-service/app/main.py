"""
VNPLaw — FastAPI AI Service
Enhanced LangGraph pipeline with:
  - Fact extraction node
  - Law mapping node
  - Deterministic sentencing calculation
  - Rebuttal mode
  - Structured legal argument generation

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MODEL CONFIGURATION  (single source of truth)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  EMBEDDING MODEL
    Model  : trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05
    Base   : jinaai/jina-embeddings-v5-text-nano (239M params, EuroBERT-210M)
    Type   : LoRA fine-tuned on Vietnamese legal case questions (~4k pairs)
    Dim    : 768  |  Context: 8192 tokens  |  task= not supported (LoRA adapter)
    Override env: EMBEDDING_ADAPTER  (or EMBEDDING_MODEL for backward compat)

  RERANKER
    Model  : BAAI/bge-reranker-v2-m3
    Type   : Multilingual cross-encoder  |  Context: 8192 tokens
    Reason : PhoRanker (itdainb/PhoRanker) was only 256 tokens — too small for
             Vietnamese law articles which reach 3,574 tokens (Điều 232 BLHS 2017)
    Override env: RERANKER_MODEL

  LLM  (remote API — no local GPU required)
    Model  : google/gemini-2.5-flash  (via OpenRouter)
    Temp   : 0  |  Context: 1M tokens
    Override env: LLM_MODEL

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import warnings

# --- WARNING & LOGGING SUPPRESSION ---
# 1. Suppress Python deprecation warnings (transformers, pkg_resources)
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
warnings.filterwarnings("ignore", category=UserWarning, module=".*pkg_resources.*")
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers.*")

# 2. Suppress noisy gRPC "too_many_pings" logs from local Milvus Lite
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GRPC_TRACE"] = ""
# -------------------------------------

# --- CPU PERFORMANCE OPTIMIZATION ---
# Force PyTorch and underlying C++ math libraries to use all available physical
# CPU cores optimally. Crucial for CPU inference performance on rented vCPUs.
# intra_op_threads: parallelism WITHIN a single op (e.g. matrix multiplication)
# inter_op_threads: parallelism BETWEEN independent ops (pipeline parallelism)
_cores = os.cpu_count() or 4
os.environ["OMP_NUM_THREADS"]     = str(_cores)
os.environ["MKL_NUM_THREADS"]     = str(_cores)
os.environ["OPENBLAS_NUM_THREADS"] = str(_cores)
import torch  # MUST import torch AFTER setting these env vars
torch.set_num_threads(_cores)           # intra-op parallelism
torch.set_num_interop_threads(_cores)   # inter-op parallelism
print(f"⚙️  CPU threads: intra={torch.get_num_threads()} / inter={torch.get_num_interop_threads()} / logical cores={_cores}")
# ------------------------------------

# ⚠️ MUST be before any pymilvus/langchain_milvus imports:
# pymilvus reads MILVUS_URI from os.environ at import time (Connections singleton).
# We NEVER set MILVUS_URI in the environment — instead we use MILVUS_DB_PATH
# so pymilvus never sees a file path and crashes with "Illegal uri".
os.environ.pop("MILVUS_URI", None)

# ── pkg_resources shim ────────────────────────────────────────────────────────
# milvus-lite <2.4.9 does `from pkg_resources import DistributionNotFound,
# get_distribution` at module load — only to read its own version string.
# In conda environments pip cannot reliably replace conda-managed packages, so
# the old milvus-lite file may never be updated regardless of `pip install`.
# Solution: inject a minimal stub into sys.modules BEFORE pymilvus is imported.
# This makes the service independent of the conda installation state.
# ─────────────────────────────────────────────────────────────────────────────
try:
    import pkg_resources  # noqa: F401 — just verify it is importable
except ModuleNotFoundError:
    import sys as _sys
    import types as _types

    class _PkgResources(_types.ModuleType):
        """Minimal pkg_resources stub — covers what milvus-lite actually uses."""

        class DistributionNotFound(Exception):
            pass

        @staticmethod
        def get_distribution(name: str):
            # Import locally so the function is self-contained and not affected
            # by the `del` cleanup that runs after the stub is registered.
            import importlib.metadata as _m
            try:
                dist = _m.distribution(name)
                class _Dist:  # noqa: E306
                    version = dist.metadata["Version"]
                return _Dist()
            except _m.PackageNotFoundError:
                raise _PkgResources.DistributionNotFound(name)

    _stub = _PkgResources("pkg_resources")
    _stub.DistributionNotFound = _PkgResources.DistributionNotFound
    _stub.get_distribution = _PkgResources.get_distribution
    _sys.modules["pkg_resources"] = _stub
    del _sys, _types, _stub, _PkgResources  # keep namespace clean
# ─────────────────────────────────────────────────────────────────────────────



import re
import inspect
import tempfile
import shutil
import json
import numpy as np
import torch
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone, timedelta
from typing import List, Literal, Annotated, Sequence, TypedDict, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from huggingface_hub import login
from transformers import AutoModel, AutoTokenizer
from peft import PeftModel

# LangChain & AI Imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pymilvus import MilvusClient
from langgraph.graph import END, StateGraph, START
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI

# Load Environment Variables
load_dotenv()

# --- CONFIGURATION ---
# Use MILVUS_DB_PATH (not MILVUS_URI) to avoid pymilvus reading it at import time.
# pymilvus specifically watches the MILVUS_URI env var — using a different name
# prevents the "Illegal uri: [/path/to/file]" ConnectionConfigException.
MILVUS_URI       = os.getenv("MILVUS_DB_PATH", "./VN_law_lora.db")
COLLECTION_NAME  = os.getenv("COLLECTION_NAME", "legal_rag_lora")
TOP_K            = int(os.getenv("TOP_K", "15"))
LLM_MODEL        = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")
# Fine-tuned Jina v5 Nano adapter — default is the production model.
# Override via EMBEDDING_ADAPTER env var only if you want a different model.
_DEFAULT_EMBEDDING = "trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05"


def _detect_device() -> str:
    """Auto-detect the best available device: GPU → CPU fallback.

    1. FORCE_CPU=1 env var → always CPU (explicit override).
    2. CUDA available + kernel probe succeeds → use GPU.
    3. CUDA not available OR kernel probe fails → fall back to CPU with a warning.

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

    try:
        probe = torch.zeros(1, device="cuda")
        _ = probe + 1  # triggers actual kernel dispatch
        del probe
        gpu_name = torch.cuda.get_device_name(0)
        cap = torch.cuda.get_device_capability(0)
        print(f"⚙️  GPU Ready — {gpu_name} (sm_{cap[0]}{cap[1]})")
        return "cuda"
    except Exception as e:
        print(
            f"⚠️  GPU probe failed ({type(e).__name__}: {e})\n"
            f"   Falling back to CPU. To fix GPU:\n"
            f"   - Check driver: nvidia-smi\n"
            f"   - Reinstall matching PyTorch CUDA wheel (cu118 / cu121 / cu124)\n"
            f"   - Re-run deploy_nodocker.sh"
        )
        return "cpu"



DEVICE = _detect_device()

# --- GLOBAL STATE ---
app_state: Dict[str, Any] = {}


# ===========================================================
# CUSTOM EMBEDDING CLASS — uses PEFT to load LoRA adapter
# ===========================================================
def _load_peft_with_compat(base_model, adapter_name: str):
    """
    Load a PEFT LoRA adapter, automatically patching the adapter_config.json
    when the installed PEFT version doesn't recognise one or more config keys
    (e.g. `corda_config` introduced in PEFT 0.15 / CorDA).

    Strategy
    --------
    1. Try normal PeftModel.from_pretrained().
    2. On a TypeError caused by unknown kwargs, download adapter_config.json
       from HuggingFace, strip all keys that LoraConfig.__init__ doesn't
       accept, write a patched copy to a temp directory alongside all other
       adapter files, and retry from that temp directory.
    3. Only fall back to the base model if the retry itself fails.
    """
    from peft import LoraConfig  # local import to avoid circular ref

    def _known_lora_keys() -> set:
        """Return the set of parameter names accepted by LoraConfig.__init__."""
        sig = inspect.signature(LoraConfig.__init__)
        return set(sig.parameters.keys()) - {"self"}

    # ── Pass 1: straightforward load ───────────────────────────────────────
    try:
        peft_model = PeftModel.from_pretrained(base_model, adapter_name)
        peft_model = peft_model.merge_and_unload()
        print("✅ LoRA adapter merged successfully.")
        return peft_model
    except TypeError as e:
        if "unexpected keyword argument" not in str(e):
            raise  # unrelated TypeError — propagate
        print(f"⚠️  PEFT config has unknown key(s): {e}")
        print("   Attempting self-healing: patching adapter_config.json …")

    # ── Pass 2: patch adapter_config.json and retry ─────────────────────────
    import json as _json
    from huggingface_hub import hf_hub_download, list_repo_files

    tmp_dir = tempfile.mkdtemp(prefix="peft_patched_")
    try:
        # Download every file in the adapter repo into tmp_dir
        try:
            repo_files = list(list_repo_files(adapter_name))
        except Exception:
            repo_files = ["adapter_config.json", "adapter_model.safetensors"]

        for fname in repo_files:
            try:
                src = hf_hub_download(repo_id=adapter_name, filename=fname)
                dst = os.path.join(tmp_dir, fname)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            except Exception:
                pass  # skip files that can't be fetched (e.g. .gitattributes)

        # Patch adapter_config.json — remove keys unknown to this PEFT version
        cfg_path = os.path.join(tmp_dir, "adapter_config.json")
        if not os.path.exists(cfg_path):
            raise FileNotFoundError("adapter_config.json not found in downloaded repo")

        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _json.load(f)

        known = _known_lora_keys()
        removed = {k: v for k, v in cfg.items() if k not in known and k != "peft_type"}
        if removed:
            print(f"   Stripping unknown config keys: {list(removed.keys())}")
            for k in removed:
                del cfg[k]
            with open(cfg_path, "w", encoding="utf-8") as f:
                _json.dump(cfg, f, indent=2, ensure_ascii=False)

        # Retry load from patched local directory
        peft_model = PeftModel.from_pretrained(base_model, tmp_dir)
        peft_model = peft_model.merge_and_unload()
        print("✅ LoRA adapter merged successfully (via patched config).")
        return peft_model

    except Exception as retry_err:
        print(f"⚠️  Patched load also failed: {type(retry_err).__name__}: {retry_err}")
        print("   Falling back to base model (embeddings will be less accurate).")
        return base_model
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class LoRABGEM3Embeddings(Embeddings):
    def __init__(self, base_model_name: str, adapter_name: str, device: str = "cuda"):
        print(f"🔄 Loading BGE-M3 base model on {device}...")
        self.device = device

        # Load tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(
            base_model_name, trust_remote_code=True
        )

        # Load base transformer model.
        # use_safetensors=True bypasses torch.load and the CVE-2025-32434 security
        # check added in transformers>=4.57 that blocks torch<2.6.
        # BAAI/bge-m3 ships model.safetensors so this is always safe.
        base_model = AutoModel.from_pretrained(
            base_model_name, trust_remote_code=True, use_safetensors=True
        )

        # Apply LoRA via PEFT — with automatic config-patching for version skew
        print(f"⬇️  Applying LoRA adapter via PEFT: {adapter_name}")
        self.model = _load_peft_with_compat(base_model, adapter_name)

        self.model = self.model.to(device)
        self.model.eval()

    def _mean_pooling(self, model_output, attention_mask):
        """Mean pool token embeddings, weighted by attention mask."""
        token_embeddings = model_output[0]  # (batch, seq, hidden)
        mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * mask_expanded, 1) / torch.clamp(
            mask_expanded.sum(1), min=1e-9
        )

    def _encode_batch(self, texts: List[str]) -> np.ndarray:
        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        encoded = {k: v.to(self.device) for k, v in encoded.items()}
        with torch.no_grad():
            output = self.model(**encoded)
        embeddings = self._mean_pooling(output, encoded["attention_mask"])
        embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
        return embeddings.cpu().numpy()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        all_embeddings: List[List[float]] = []
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            all_embeddings.extend(self._encode_batch(batch).tolist())
        return all_embeddings

    def embed_query(self, text: str) -> List[float]:
        return self._encode_batch([text])[0].tolist()


# ===========================================================
# JINA EMBEDDINGS CLASS  — uses SentenceTransformer
# ===========================================================
class JinaEmbeddings(Embeddings):
    """
    LangChain-compatible embeddings wrapper for Jina v5 Nano
    (or any SentenceTransformer model that accepts a 'task' kwarg).

    Both documents and queries use task='retrieval' as recommended
    by the Jina retrieval model documentation.
    """

    def __init__(
        self,
        model_name: str = "trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05",
        device: Optional[str] = None,
        batch_size: int = 32,
    ):
        from sentence_transformers import SentenceTransformer

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        print(f"🔄 Loading Jina model '{model_name}' on {device}...")
        self._model = SentenceTransformer(
            model_name,
            trust_remote_code=True,
            device=device,
        )
        self._batch_size = batch_size

        # Probe once whether this model supports task= (base Jina does, LoRA adapters may not)
        try:
            self._model.encode(["probe"], task="retrieval", show_progress_bar=False)
            self._supports_task = True
            print("✅ Jina embedding model loaded (task= supported).")
        except TypeError:
            self._supports_task = False
            print("✅ Jina embedding model loaded (task= not supported — fine-tuned adapter).")

    def _encode(self, texts: List[str]) -> List[List[float]]:
        kwargs = dict(
            normalize_embeddings=True,
            batch_size=self._batch_size,
            show_progress_bar=False,
        )
        if self._supports_task:
            kwargs["task"] = "retrieval"
        vecs = self._model.encode(texts, **kwargs)
        return vecs.tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._encode([text])[0]


# ===========================================================
# LANGGRAPH STATE
# ===========================================================
class AgentState(TypedDict):
    messages:            Annotated[Sequence[BaseMessage], add_messages]
    question:            str
    full_case_content:   str
    documents:           List[Document]
    retrieval_queries:   List[str]                   # 3 queries from multi_query_rewrite
    retry_count:         int
    user_role:           Literal["defense", "victim", "neutral"]
    extracted_facts:     Optional[Dict[str, Any]]
    mapped_laws:         Optional[List[Dict[str, Any]]]
    rebuttal_against:    Optional[str]
    sentencing_data:     Optional[Dict[str, Any]]
    chat_history:        Optional[List[Dict[str, str]]]
    is_relevant:         Optional[bool]
    _missing_fields:     Optional[List[str]]         # set by clarification_check_node
    per_defendant_dates: Optional[List[Dict[str, str]]]  # multi-defendant support


# ===========================================================
# REQUEST / RESPONSE MODELS
# ===========================================================
class RequestBody(BaseModel):
    case_content: str
    role: Literal["defense", "victim", "neutral"] = "neutral"
    rebuttal_against: Optional[str] = None
    conversation_history: Optional[List[Dict[str, str]]] = Field(default_factory=list)


class PredictResponse(BaseModel):
    result: str
    extracted_facts: Optional[Dict[str, Any]] = None
    mapped_laws: Optional[List[Dict[str, Any]]] = None
    sentencing_data: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    status: str
    device: str
    model_loaded: bool


class GradeDocuments(BaseModel):
    binary_score: str = Field(description="Relevance score 'yes' or 'no'")


class PracticeEvalRequest(BaseModel):
    case_description: str
    user_mode: Literal["defense", "victim", "neutral"] = "neutral"
    user_analysis: str


class PracticeEvalFeedback(BaseModel):
    strengths: List[str]
    improvements: List[str]
    missed_articles: List[str]
    suggestion: str


class PracticeEvalResponse(BaseModel):
    score: int
    feedback: PracticeEvalFeedback


# ===========================================================
# MODULE-LEVEL CONSTANTS — used by new RAG nodes
# ===========================================================

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
    "BLHS 1999":                  {"7", "46", "47", "48", "49", "50", "51", "52", "60"},
    "BLHS 1999 (sửa đổi 2009)": {"7", "46", "47", "48", "49", "50", "51", "52", "60"},
    "BLHS 2015 (sửa đổi 2017)": {"7", "51", "52", "53", "54", "55", "56", "57", "65"},
    "BLHS 2015 (sửa đổi 2025)": {"7", "51", "52", "53", "54", "55", "56", "57", "65"},
}


def _edition_for_date(date_str: str) -> Optional[str]:
    if not isinstance(date_str, str) or not date_str.strip():
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(date_str.strip(), fmt).date()
            for name, start, end in _EDITION_RANGES:
                if start <= d < end:
                    return name
        except (ValueError, AttributeError, TypeError):
            continue
    return None


_PINNED_MAP = {
    ("mitigating",    "BLHS 1999"):                 "46",
    ("aggravating",   "BLHS 1999"):                 "48",
    ("recidivism",    "BLHS 1999"):                 "49",
    ("below_min",     "BLHS 1999"):                 "47",
    ("attempt",       "BLHS 1999"):                 "52",
    ("consolidate",   "BLHS 1999"):                 "50",
    ("suspended",     "BLHS 1999"):                 "60",
    ("civil_comp",    "BLHS 1999"):                 "42",
    ("penalty_types", "BLHS 1999"):                 "28",
    ("retroactive",   "BLHS 1999"):                 "7",
    ("mitigating",    "BLHS 1999 (sửa đổi 2009)"): "46",
    ("aggravating",   "BLHS 1999 (sửa đổi 2009)"): "48",
    ("recidivism",    "BLHS 1999 (sửa đổi 2009)"): "49",
    ("below_min",     "BLHS 1999 (sửa đổi 2009)"): "47",
    ("attempt",       "BLHS 1999 (sửa đổi 2009)"): "52",
    ("consolidate",   "BLHS 1999 (sửa đổi 2009)"): "50",
    ("suspended",     "BLHS 1999 (sửa đổi 2009)"): "60",
    ("civil_comp",    "BLHS 1999 (sửa đổi 2009)"): "42",
    ("penalty_types", "BLHS 1999 (sửa đổi 2009)"): "28",
    ("retroactive",   "BLHS 1999 (sửa đổi 2009)"): "7",
    ("mitigating",    "BLHS 2015 (sửa đổi 2017)"): "51",
    ("aggravating",   "BLHS 2015 (sửa đổi 2017)"): "52",
    ("recidivism",    "BLHS 2015 (sửa đổi 2017)"): "53",
    ("below_min",     "BLHS 2015 (sửa đổi 2017)"): "54",
    ("attempt",       "BLHS 2015 (sửa đổi 2017)"): "57",
    ("consolidate",   "BLHS 2015 (sửa đổi 2017)"): "55",
    ("suspended",     "BLHS 2015 (sửa đổi 2017)"): "65",
    ("civil_comp",    "BLHS 2015 (sửa đổi 2017)"): "48",
    ("penalty_types", "BLHS 2015 (sửa đổi 2017)"): "32",
    ("retroactive",   "BLHS 2015 (sửa đổi 2017)"): "7",
    ("mitigating",    "BLHS 2015 (sửa đổi 2025)"): "51",
    ("aggravating",   "BLHS 2015 (sửa đổi 2025)"): "52",
    ("recidivism",    "BLHS 2015 (sửa đổi 2025)"): "53",
    ("below_min",     "BLHS 2015 (sửa đổi 2025)"): "54",
    ("attempt",       "BLHS 2015 (sửa đổi 2025)"): "57",
    ("consolidate",   "BLHS 2015 (sửa đổi 2025)"): "55",
    ("suspended",     "BLHS 2015 (sửa đổi 2025)"): "65",
    ("civil_comp",    "BLHS 2015 (sửa đổi 2025)"): "48",
    ("penalty_types", "BLHS 2015 (sửa đổi 2025)"): "32",
    ("retroactive",   "BLHS 2015 (sửa đổi 2025)"): "7",
}

_PINNED_PURPOSES = {
    "neutral": ["retroactive", "mitigating", "aggravating", "consolidate"],
    "defense": ["retroactive", "mitigating", "below_min", "attempt", "suspended"],
    "victim":  ["retroactive", "aggravating", "recidivism", "civil_comp", "penalty_types"],
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

_MAX_SEMANTIC_DOCS = 5

# ===========================================================
# UTILITY: DETERMINISTIC SENTENCING CALCULATIONS
# ===========================================================
def parse_date(text: str) -> Optional[datetime]:
    """Try multiple date formats to parse a date string."""
    # BUG-05 FIX: Removed the unused `patterns` parameter — it was always ignored.
    for pattern in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text.strip(), pattern)
        except ValueError:
            continue
    return None


def compute_detention_months(arrest_date_str: str, trial_date_str: str) -> Optional[float]:
    """Calculate months from arrest to trial."""
    d1 = parse_date(arrest_date_str)
    d2 = parse_date(trial_date_str)
    if d1 and d2 and d2 > d1:
        delta = d2 - d1
        return round(delta.days / 30.44, 1)
    return None


def compute_age_at_crime(dob_str: str, crime_date_str: str) -> Optional[float]:
    """Calculate victim/defendant age at time of crime."""
    dob = parse_date(dob_str)
    crime_date = parse_date(crime_date_str)
    if dob and crime_date:
        age = (crime_date - dob).days / 365.25
        return round(age, 2)
    return None


def extract_sentencing_data(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministically compute numeric sentencing data from extracted facts.
    Returns structured data to augment the LLM prompt.
    """
    result: Dict[str, Any] = {}

    # Detention period
    arrest_date = facts.get("ngay_tam_giam")
    trial_date = facts.get("ngay_xet_xu")
    if arrest_date and trial_date:
        months = compute_detention_months(arrest_date, trial_date)
        result["detention_months"] = months

    # Victim age at crime
    victim_dob = facts.get("ngay_sinh_nan_nhan")
    crime_date = facts.get("ngay_pham_toi")
    if victim_dob and crime_date:
        age = compute_age_at_crime(victim_dob, crime_date)
        result["victim_age_at_crime"] = age
        result["victim_is_minor"] = age is not None and age < 18

    # Defendant age at crime
    defendant_dob = facts.get("ngay_sinh_bi_cao")
    if defendant_dob and crime_date:
        age = compute_age_at_crime(defendant_dob, crime_date)
        result["defendant_age_at_crime"] = age
        result["defendant_is_minor"] = age is not None and age < 18

    return result


def sanitize_text(text: str) -> str:
    """Strip lone surrogate characters that crash Python's UTF-8 JSON encoder.
    Surrogates (U+D800–U+DFFF) appear in text scraped from Vietnamese PDFs via
    mixed-encoding parsers. encode('utf-8', 'replace') replaces them with U+FFFD (?)."""
    if not isinstance(text, str):
        return text
    return text.encode("utf-8", "replace").decode("utf-8")


def _sanitize_msgs(messages: list) -> list:
    """Strip surrogates from ALL LangChain BaseMessage content immediately before
    any llm.invoke() call. Last line of defence — catches anything that slipped through
    upstream sanitization (history, mapped_context, case descriptions, etc.)."""
    for m in messages:
        if isinstance(getattr(m, "content", None), str):
            m.content = sanitize_text(m.content)
    return messages


def cleanup_response(text: str) -> str:
    """
    Remove or replace 'BLHS' abbreviations in AI-generated text.
    Replaces 'BLHS' with 'Bộ luật Hình sự' for clarity.
    This ensures AI responses are user-friendly and don't use abbreviations.
    Also strips lone surrogate characters that would crash the JSON encoder.
    """
    text = sanitize_text(text)
    # Replace " BLHS " or " BLHS," with " Bộ luật Hình sự "
    text = re.sub(r"\bBLHS\b", "Bộ luật Hình sự", text, flags=re.IGNORECASE)
    return text


# ===========================================================
# LIFESPAN — LOAD MODELS ONCE
# ===========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    # Suppress the harmless "Invalid HTTP request received" noise emitted by
    # uvicorn/httptools when stale keep-alive TCP sockets, TLS probes, or
    # connection-pool cleanup frames arrive on the plain-HTTP port.
    # Applied here (not just __main__) so it works with `python -m uvicorn` too.
    class _SuppressInvalidHTTP(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "Invalid HTTP request received" not in record.getMessage()
    logging.getLogger("uvicorn.error").addFilter(_SuppressInvalidHTTP())

    print("🚀 SERVER STARTUP: Initializing...")

    # Authenticate HuggingFace
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        try:
            login(token=hf_token)
            print("✅ Logged in to Hugging Face successfully.")
        except Exception as e:
            print(f"⚠️  Failed HF login: {e}")
    else:
        print("⚠️  'HF_TOKEN' not set. Public models only.")

    # 1. Embedding model  (Jina v5 Nano via SentenceTransformer)
    # EMBEDDING_ADAPTER env var overrides the default fine-tuned model.
    # EMBEDDING_MODEL is a secondary alias for backward compat.
    # Default: the production fine-tuned adapter (set in main.py, not .env).
    _jina_model = (
        os.getenv("EMBEDDING_ADAPTER")
        or os.getenv("EMBEDDING_MODEL")
        or _DEFAULT_EMBEDDING
    )
    print(f"📌 Embedding model: {_jina_model}")
    embedding_model = JinaEmbeddings(
        model_name=_jina_model,
        device=DEVICE,
        batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "32")),
    )

    # 2. Milvus-Lite vector store — use MilvusClient directly
    # (avoids langchain_milvus version issues and MILVUS_URI env collision)
    print(f"📦 Connecting to Milvus Lite DB: {MILVUS_URI}")
    milvus_client = MilvusClient(uri=MILVUS_URI)

    _OUTPUT_FIELDS = [
        "content", "article_number", "title",
        "chapter", "source", "effective_start", "effective_end",
    ]

    class _MilvusRetriever:
        """Thin retriever wrapping MilvusClient for similarity search."""
        def __init__(self, client, emb_fn, collection, top_k, output_fields):
            self._client = client
            self._emb = emb_fn
            self._col = collection
            self._k = top_k
            self._fields = output_fields

        def invoke(self, query: str):
            vec = self._emb.embed_query(query)
            results = self._client.search(
                collection_name=self._col,
                data=[vec],
                limit=self._k,
                output_fields=self._fields,
                search_params={"metric_type": "COSINE"},
            )[0]
            # --- LOG RAG CHUNK IDs ---
            print(f"  [RAG] Retrieved {len(results)} chunks:")
            for r in results:
                ch  = r['entity'].get('chapter', '?')
                art = r['entity'].get('article_number', '?')
                src = r['entity'].get('source', '?')
                print(f"    ID={r['id']}  score={r['distance']:.4f}  | Chương: {ch}  Điều: {art}  [{src}]")
            # -------------------------
            docs = []
            for r in results:
                entity = r["entity"]
                docs.append(Document(
                    page_content=sanitize_text(entity.get("content", "")),
                    metadata={
                        k: entity.get(k, "")
                        for k in self._fields if k != "content"
                    },
                ))
            return docs

    retriever = _MilvusRetriever(
        milvus_client, embedding_model, COLLECTION_NAME, TOP_K, _OUTPUT_FIELDS
    )
    print(f"✅ Milvus ready — collection '{COLLECTION_NAME}'")

    # 3. LLM (OpenRouter)
    llm = ChatOpenAI(
        model=LLM_MODEL,
        openai_api_key=os.getenv("OPENROUTER_API_KEY"),
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0
    )

    # 4. Cross-encoder reranker — load directly via AutoModel.
    # Both sentence_transformers 3.x CrossEncoder AND FlagEmbedding call transformers
    # internals (prepare_for_model, BatchEncoding unpacking) that broke in transformers 4.57.
    # Using AutoModelForSequenceClassification directly bypasses all wrapper layers
    # and works with any transformers version.
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    import torch as _rerank_torch
    _RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    reranker_tokenizer = AutoTokenizer.from_pretrained(_RERANKER_MODEL)
    reranker_model = AutoModelForSequenceClassification.from_pretrained(
        _RERANKER_MODEL,
        torch_dtype=_rerank_torch.float16 if DEVICE == "cuda" else _rerank_torch.float32,
    )
    reranker_model.eval()
    reranker_model.to(DEVICE)
    _prec = "fp16" if DEVICE == "cuda" else "fp32"
    print(f"✅ Reranker loaded: {_RERANKER_MODEL} ({_prec}, AutoModel direct, max_length=8192).")
    del _rerank_torch

    def _rerank_scores(pairs: list[tuple[str, str]], batch_size: int = 8) -> list[float]:
        """Score (query, doc) pairs with the reranker. Returns raw logits (higher=more relevant)."""
        import torch
        all_scores: list[float] = []
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i : i + batch_size]
            with torch.no_grad():
                enc = reranker_tokenizer(
                    [p[0] for p in batch],
                    [p[1] for p in batch],
                    padding=True,
                    truncation=True,
                    max_length=8192,
                    return_tensors="pt",
                ).to(DEVICE)
                logits = reranker_model(**enc).logits.view(-1).float()
            all_scores.extend(logits.cpu().tolist())
        return all_scores


    # -------------------------------------------------------
    # NODE DEFINITIONS
    # -------------------------------------------------------

    # NODE: EXTRACT FACTS
    def extract_facts_node(state: AgentState) -> dict:
        """Extract structured legal facts from case text."""
        print("[NODE: extract_facts]")
        case_text = state.get("full_case_content", state["question"])

        system_prompt = """Bạn là chuyên gia phân tích hồ sơ pháp lý.
Nhiệm vụ: Đọc kỹ nội dung vụ án và trích xuất thông tin có cấu trúc.
Trả về JSON với các trường sau (dùng null nếu không tìm thấy thông tin):
{
  "hanh_vi": "mô tả hành vi phạm tội",
  "hau_qua": "hậu quả gây ra",
  "dong_co": "động cơ",
  "doi_tuong": "đối tượng bị hại",
  "cong_cu": "công cụ phương tiện",
  "tinh_tiet_tang_nang": ["list tình tiết tăng nặng"],
  "tinh_tiet_giam_nhe": ["list tình tiết giảm nhẹ"],
  "ngay_pham_toi": "dd/mm/yyyy",
  "ngay_xet_xu": "dd/mm/yyyy nếu có trong mô tả, nếu không để null",
  "ngay_sinh_nan_nhan": "dd/mm/yyyy",
  "ngay_sinh_bi_cao": "dd/mm/yyyy",
  "ngay_tam_giam": "dd/mm/yyyy",
  "ten_bi_cao": "tên bị cáo (nếu có nhiều bị cáo, để dạng 'A, B, C')",
  "co_tien_an": true/false,
  "da_boi_thuong": true/false,
  "da_thanh_khan_khai_bao": true/false,
  "is_multi_defendant": true/false,
  "so_luong_bi_cao": integer or null,
  "tang_vat_loai": "loại tang vật",
  "tang_vat_so_luong": "số lượng / khối lượng",
  "dia_danh": "tỉnh / thành phố nơi xảy ra vụ án (ví dụ: 'Hà Nội', 'Bình Thuận', 'TP. Hồ Chí Minh') — chỉ tên tỉnh/thành, null nếu không có",
  "per_defendant_dates": [
    {"name": "tên bị cáo", "ngay_pham_toi": "dd/mm/yyyy"}
  ]
}

QUY TẮC TRÍCH XUẤT per_defendant_dates:
- Chỉ điền nếu is_multi_defendant = true VÀ mỗi bị cáo có ngày phạm tội riêng trong mô tả.
- Nếu một bị cáo không có ngày riêng → dùng ngày chung từ "ngay_pham_toi".
- Nếu chỉ có một bị cáo hoặc không xác định được → để null (không phải []).
- CHỈ trích xuất thông tin CÓ TRONG mô tả. TUYỆT ĐỐI KHÔNG bịa đặt thông tin.

LƯU Ý: Trích xuất "ngay_xet_xu" nếu có trong mô tả (ví dụ: ngày tòa xét xử, ngày phiên tòa).
Nếu không tìm thấy, trả về null — hệ thống sẽ tự động dùng ngày hiện tại.
OUTPUT: CHỈ xuất JSON hợp lệ, không markdown, không giải thích."""

        try:
            response = llm.invoke(_sanitize_msgs([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"NỘI DUNG VỤ ÁN:\n{case_text}")
            ]))
            raw = response.content.strip()
            # Strip markdown code fences (handles ```json, ```JSON, or plain ```)
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
            facts = json.loads(raw)
        except Exception as e:
            print(f"⚠️  Fact extraction failed: {e}")
            facts = {}

        # ngay_xet_xu fallback (uses module-level _VN_TZ)
        if not facts.get("ngay_xet_xu"):
            facts["ngay_xet_xu"] = datetime.now(_VN_TZ).strftime("%d/%m/%Y")
            print(f"  ngay_xet_xu not in input — defaulted to today (GMT+7): {facts['ngay_xet_xu']}")
        else:
            print(f"  ngay_xet_xu extracted from input: {facts['ngay_xet_xu']}")

        # Promote per_defendant_dates to top-level state
        per_defendant = facts.pop("per_defendant_dates", None) or None
        if per_defendant and not isinstance(per_defendant, list):
            per_defendant = None

        sentencing_data = extract_sentencing_data(facts)
        print("  ┌─── Extracted Facts (JSON) ─────────────────────────────")
        for k, v in facts.items():
            print(f"  │  {k}: {json.dumps(v, ensure_ascii=False)}")
        print(f"  └─── Sentencing data: {json.dumps(sentencing_data, ensure_ascii=False)}")

        return {
            "extracted_facts":     facts,
            "sentencing_data":     sentencing_data,
            "per_defendant_dates": per_defendant,
        }

    # NODE 2.5: CLARIFICATION CHECK
    def clarification_check_node(state: AgentState) -> dict:
        """Validates MUST HAVE fields; writes _missing_fields to state."""
        facts = state.get("extracted_facts") or {}
        missing = [f for f in REQUIRED_FIELDS if not facts.get(f)]

        if not missing:
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    crime_date = datetime.strptime(facts["ngay_pham_toi"].strip(), fmt).date()
                    if crime_date < _MIN_SUPPORTED_DATE:
                        missing.append("_date_out_of_range")
                    break
                except (ValueError, AttributeError):
                    continue

        return {"_missing_fields": missing}

    def clarification_router(state: AgentState) -> str:
        """Router: reads _missing_fields, returns route key."""
        return "clarify" if state.get("_missing_fields") else "continue"

    def clarification_node(state: AgentState) -> dict:
        missing = state.get("_missing_fields", [])
        if "_date_out_of_range" in missing:
            facts = state.get("extracted_facts") or {}
            reply = (
                f"⚠️ **Ngày phạm tội không hợp lệ:** `{facts.get('ngay_pham_toi', '?')}`\n\n"
                "Hệ thống chỉ hỗ trợ các vụ án có ngày phạm tội từ **01/07/2000** trở đi "
                "(ngày BLHS 1999 có hiệu lực).\n\n"
                "Vui lòng kiểm tra lại ngày phạm tội và gửi lại."
            )
            return {"messages": [AIMessage(content=reply)]}

        needed_labels = [REQUIRED_FIELDS[f] for f in missing if f in REQUIRED_FIELDS]
        reply = (
            "ℹ️ Để phân tích chính xác, hệ thống cần thêm thông tin sau:\n\n"
            + "\n".join(f"{i+1}. **{label}**" for i, label in enumerate(needed_labels))
            + "\n\nVui lòng bổ sung và gửi lại mô tả vụ án."
        )
        reply += (
            "\n\n💡 **Thông tin tham khảo** (không bắt buộc, nhưng giúp phân tích tốt hơn):\n"
            "- Hậu quả gây ra (thương tích, thiệt hại tài sản)\n"
            "- Bị cáo có tiền án tiền sự không?\n"
            "- Bị cáo có thành khẩn khai báo / bồi thường không?\n"
            "- Tang vật thu giữ (loại, số lượng)"
        )
        return {"messages": [AIMessage(content=reply)]}

    # NODE 3: MULTI QUERY REWRITE
    def multi_query_rewrite(state: AgentState) -> dict:
        print("[NODE: multi_query_rewrite]")
        facts     = state.get("extracted_facts") or {}
        role      = state.get("user_role", "neutral")
        case_text = state.get("full_case_content", state["question"])
        circumstance_instruction = _ROLE_CIRCUMSTANCE_INSTRUCTION.get(
            role, _ROLE_CIRCUMSTANCE_INSTRUCTION["neutral"]
        )

        prompt = f"""Bạn là chuyên gia phân tích hồ sơ pháp lý hình sự Việt Nam.
Dựa vào nội dung vụ án và các sự kiện đã trích xuất, hãy tạo 3 câu mô tả
theo văn phong bản án tòa án để tìm kiếm điều luật phù hợp.

NỘI DUNG VỤ ÁN (nguồn dữ liệu duy nhất):
{case_text}

SỰ KIỆN ĐÃ TRÍCH XUẤT:
{json.dumps(facts, ensure_ascii=False, indent=2)}

QUY TẮc BẮT BUỘC:
1. CHỈ sử dụng thông tin có trong "NỘI DUNG VỤ ÁN" hoặc "SỰ KIỆN ĐÃ TRÍCH XUẤT".
2. TUYỆT ĐỐI KHÔNG thêm thông tin, suy luận, hoặc bọa đặt bất kỳ chi tiết nào.
3. KHÔNG được viết tên điều luật, số điều khoản (ví dụ "Điều 168", "Điều 51").
4. KHÔNG dùng ngôn ngữ tòa án như "Tòa án áp dụng", "căn cứ vào", "bị truy tố về tội".
5. Viết bằng tiếng Việt, văn phong bản án thực tế (ngôi thứ ba, quá khứ, mô tả sự kiện).
6. Mỗi câu dài 2–5 câu. Nếu không có thông tin cho một trụy vấn → trả về null.

YÊU CẦU:
- behavior_query: Mô tả hành vi phạm tội cụ thể — bị cáo đã làm gì, với ai, bằng phương tiện gì, gây hậu quả gì.
- circumstance_query: {circumstance_instruction}
- evidence_query: Mô tả tang vật, công cụ phạm tội, số lượng, trọng lượng,
  giá trị tài sản cụ thể có trong vụ án. Nếu không có tang vật → null.

TRẢ VỀ JSON (null nếu không có thông tin):
{{"behavior_query": "...", "circumstance_query": "...", "evidence_query": "..."}}
OUTPUT: CHỈ JSON hợp lệ, không markdown, không giải thích."""

        try:
            response = llm.invoke(_sanitize_msgs([HumanMessage(content=prompt)]))
            raw = re.sub(r"```(?:json)?\s*", "", response.content.strip()).strip()
            queries = json.loads(raw)
            q_list = [
                q for q in [
                    queries.get("behavior_query"),
                    queries.get("circumstance_query"),
                    queries.get("evidence_query"),
                ] if q
            ]
            if not q_list:
                raise ValueError("All queries null")
        except Exception:
            hanh_vi  = facts.get("hanh_vi", "")
            hau_qua  = facts.get("hau_qua", "")
            tang_vat = facts.get("tang_vat_loai", "")
            giam_nhe = ", ".join(facts.get("tinh_tiet_giam_nhe") or [])
            tang_nang = ", ".join(facts.get("tinh_tiet_tang_nang") or [])
            q1 = f"{hanh_vi}. {hau_qua}".strip(". ") or case_text[:300]
            q2 = (
                f"{giam_nhe}. {tang_nang}".strip(". ")
                or hanh_vi
                or case_text[:300]
            )
            q_list = [q for q in [q1, q2, tang_vat or None] if q]

        print(f"  [REWRITE] Generated {len(q_list)} queries for role={role!r}")
        for i, q in enumerate(q_list):
            print(f"  ┌─ Q{i+1} {'─'*60}")
            print(f"  │ {q}")
            print(f"  └{'─'*63}")
        return {"retrieval_queries": q_list}

    # NODE 4: PARALLEL RETRIEVE
    def parallel_retrieve(state: AgentState) -> dict:
        print("[NODE: parallel_retrieve]")
        queries       = state.get("retrieval_queries") or [state["question"]]
        role          = state.get("user_role", "neutral")
        facts         = state.get("extracted_facts") or {}
        per_defendant = state.get("per_defendant_dates") or []
        seen_ids      = set()
        all_docs      = []

        # Step 1: Semantic search (up to 3 queries)
        for q in queries:
            if not q:
                continue
            try:
                docs = retriever.invoke(q)
                for d in docs:
                    key = (d.metadata.get("article_number", ""), d.metadata.get("source", ""))
                    if key not in seen_ids:
                        seen_ids.add(key)
                        all_docs.append(d)
            except Exception as e:
                print(f"[RETRIEVE ERROR] {type(e).__name__}: {e}")

        # Step 2: Pinned fetch — edition-aware, multi-defendant safe
        if per_defendant:
            crime_editions = [
                _edition_for_date(d.get("ngay_pham_toi", ""))
                for d in per_defendant
            ]
            crime_editions = [e for e in crime_editions if e]
        else:
            single = _edition_for_date(facts.get("ngay_pham_toi", ""))
            crime_editions = [single] if single else []

        pinned_purposes = _PINNED_PURPOSES.get(role, _PINNED_PURPOSES["neutral"])

        for edition in crime_editions:
            for purpose in pinned_purposes:
                art_no = _PINNED_MAP.get((purpose, edition))
                if not art_no:
                    print(f"  [PINNED] No mapping for ({purpose}, {edition!r}) — skip")
                    continue
                key = (art_no, edition)
                if key in seen_ids:
                    # Same article+edition already retrieved semantically — skip
                    print(f"  [PINNED] Điều {art_no} ({purpose}) from {edition!r} — already in results")
                    continue
                try:
                    hits = milvus_client.query(
                        collection_name=COLLECTION_NAME,
                        filter=f'article_number == "{art_no}" AND source == "{edition}"',
                        output_fields=_OUTPUT_FIELDS,
                        limit=1,
                    )
                    for h in hits:
                        doc = Document(
                            page_content=sanitize_text(h.get("content", "")),
                            metadata={k: sanitize_text(h.get(k, "")) if isinstance(h.get(k, ""), str) else h.get(k, "")
                                      for k in _OUTPUT_FIELDS if k != "content"},
                        )
                        doc.metadata["_pinned"]  = True
                        doc.metadata["_purpose"] = purpose
                        all_docs.append(doc)
                        seen_ids.add(key)
                        print(f"  [PINNED] Điều {art_no} ({purpose}) from {edition!r}")
                except Exception as e:
                    print(f"  [PINNED] Failed to fetch Điều {art_no} ({purpose}): {e}")

        n_pinned   = sum(1 for d in all_docs if d.metadata.get("_pinned"))
        n_semantic = len(all_docs) - n_pinned
        print(f"  [RETRIEVE] Total: {len(all_docs)} docs (semantic={n_semantic}, pinned={n_pinned})")
        return {"documents": all_docs}

    # NODE 5: TEMPORAL PRIORITY TAGGER
    def temporal_priority_tagger(state: AgentState) -> dict:
        print("[NODE: temporal_priority_tagger]")
        docs  = state.get("documents", [])
        facts = state.get("extracted_facts") or {}
        trial_edition = _edition_for_date(facts.get("ngay_xet_xu", ""))

        per_defendant = state.get("per_defendant_dates") or []
        if per_defendant:
            updated_per_defendant = []
            for d_info in per_defendant:
                edition = _edition_for_date(d_info.get("ngay_pham_toi", "")) or ""
                updated_per_defendant.append({**d_info, "crime_edition": edition})
            crime_editions = {d["crime_edition"] for d in updated_per_defendant if d["crime_edition"]}
            per_defendant = updated_per_defendant
            print(f"  [TEMPORAL] Multi-defendant mode: editions={crime_editions}")
        else:
            single = _edition_for_date(facts.get("ngay_pham_toi", ""))
            crime_editions = {single} if single else set()

        if not crime_editions:
            print("  [TEMPORAL] Cannot determine any crime edition — passing all docs")
            return {"documents": docs}

        needs_comparison = any(e != trial_edition for e in crime_editions)
        print(f"  [TEMPORAL] Crime editions: {crime_editions} | Trial: {trial_edition}")
        print(f"  [TEMPORAL] Retroactivity comparison needed: {needs_comparison}")

        tagged = []
        newer  = []
        always = []

        for d in docs:
            art_no     = str(d.metadata.get("article_number", ""))
            src        = d.metadata.get("source", "")
            always_keep = _ALWAYS_KEEP_BY_EDITION.get(src, set())

            if art_no in always_keep:
                d.metadata["_temporal_role"] = "adjustment"
                always.append(d)
            elif src in crime_editions:
                d.metadata["_temporal_role"] = "primary"
                d.metadata["_primary_for"] = [
                    di["name"] for di in per_defendant
                    if di.get("crime_edition") == src
                ] or ["all"]
                tagged.append(d)
            elif needs_comparison:
                d.metadata["_temporal_role"] = "comparison"
                newer.append(d)

        ordered = tagged + newer + always
        result  = ordered if ordered else docs
        print(f"  [TEMPORAL] primary={len(tagged)}, comparison={len(newer)}, adjustment={len(always)}")
        return {"documents": result, "per_defendant_dates": per_defendant}

    # NODE 6: RERANK (replaces grade_documents)
    def rerank_node(state: AgentState) -> dict:
        print("[NODE: rerank]")
        docs = state.get("documents", [])
        if not docs:
            return {"documents": [], "is_relevant": False}

        retrieval_queries = state.get("retrieval_queries") or []
        query = (
            retrieval_queries[0]
            if retrieval_queries
            else (state.get("full_case_content") or state["question"])
        )

        # Separate into 3 buckets:
        #   pinned    = explicitly fetched by pinned step (_pinned=True)
        #   always    = adjustment-role docs (Điều 51/52/55/7 etc) — semantically
        #               retrieved but should always survive like pinned
        #   semantic  = normal semantic docs → scored by cross-encoder
        pinned_docs    = [d for d in docs if d.metadata.get("_pinned")]
        adjustment_docs = [
            d for d in docs
            if not d.metadata.get("_pinned")
            and d.metadata.get("_temporal_role") == "adjustment"
        ]
        semantic_docs  = [
            d for d in docs
            if not d.metadata.get("_pinned")
            and d.metadata.get("_temporal_role") != "adjustment"
        ]

        if semantic_docs:
            _q = query[:512]
            pairs  = [(_q, d.page_content) for d in semantic_docs]
            scores = _rerank_scores(pairs)
            ranked_semantic = sorted(zip(scores, semantic_docs), key=lambda x: x[0], reverse=True)
            top_semantic    = [doc for _, doc in ranked_semantic[:_MAX_SEMANTIC_DOCS]]
        else:
            ranked_semantic = []
            top_semantic    = []

        # Merge: semantic (cross-encoder top-K) + adjustment (always-keep) + pinned
        top_docs = top_semantic + adjustment_docs + pinned_docs

        if not top_docs:
            return {"documents": [], "is_relevant": False}

        print(f"  [RERANK] query[:80]: {query[:80]!r}")
        print(f"  [RERANK] {len(docs)} → {len(top_docs)} docs "
              f"(semantic_kept={len(top_semantic)}/{len(semantic_docs)}, "
              f"adjustment_kept={len(adjustment_docs)}, pinned={len(pinned_docs)})")
        for score, doc in ranked_semantic[:_MAX_SEMANTIC_DOCS]:
            art  = doc.metadata.get("article_number", "?")
            src  = doc.metadata.get("source", "?")
            role = doc.metadata.get("_temporal_role", "?")
            print(f"    [sem] score={score:.4f}  Điều {art} | {src} | {role}")
        for doc in adjustment_docs:
            art = doc.metadata.get("article_number", "?")
            src = doc.metadata.get("source", "?")
            print(f"    [adj] Điều {art} | {src} | always-keep")
        for doc in pinned_docs:
            art     = doc.metadata.get("article_number", "?")
            src     = doc.metadata.get("source", "?")
            purpose = doc.metadata.get("_purpose", "?")
            print(f"    [pin] Điều {art} | {src} | purpose={purpose}")

        return {"documents": top_docs, "is_relevant": True}

    def check_rebuttal(state: AgentState) -> str:
        """Router after map_laws: grade study submission or generate normally."""
        return "rebuttal" if state.get("rebuttal_against") else "generate"

    # NODE 7: MAP LAWS (fixed — no truncation, retroactivity prompt)
    def map_laws_node(state: AgentState) -> dict:
        """Map extracted facts to specific law articles."""
        print("[NODE: map_laws]")
        facts     = state.get("extracted_facts") or {}
        documents = state.get("documents") or []
        case_text = state.get("full_case_content") or state.get("question", "")

        if not documents:
            print("⚠️  map_laws: no documents in state — returning error sentinel")
            return {"mapped_laws": [{
                "article": "N/A", "clause": "N/A",
                "offense_name": "Không xác định được",
                "applicable_reason": "Không có tài liệu luật nào được trích xuất.",
                "edition_applied": "N/A", "edition_reason": "Không có tài liệu.",
                "_mapping_error": True,
            }]}

        context = "\n\n".join([
            f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','?')} "
            f"| role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
            for d in documents
        ])
        facts_str = json.dumps(facts, ensure_ascii=False, indent=2)

        system_prompt = """Bạn là chuyên gia luật hình sự Việt Nam.
Ánh xạ từng hành vi phạm tội vào điều khoản cụ thể, áp dụng ĐÚNG nguyên tắc hiệu lực của luật.

❌ NGHIÊM CẤM: Chỉ được trích dẫn điều khoản thuộc BỘ LUẬT HÌNH SỰ (BLHS).
KHÔNG được áp dụng bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự,
Bộ luật Lao động, hôn nhân gia đình, hay bất kỳ bộ luật, nghị định, thông tư nào khác.

NGUYÊN TẮC THỜI HIỆU (Điều 7 BLHS) — BẮT BUỘC ÁP DỤNG:
1. QUY TẮC CƠ BẢN: Áp dụng luật có hiệu lực tại THỜI ĐIỂM PHẠM TỘI (tài liệu có role=primary).
2. NGOẠI LỆ HỒI TỐ CÓ LỢI: Nếu luật MỚI HƠN (role=comparison) quy định hình phạt NHẸ HƠN, BẮT BUỘC áp dụng.
3. NGHIÊM CẤM hồi tố nếu luật mới NẶNG HƠN — giữ luật cũ.
4. ĐA TỘI DANH: So sánh từng tội danh riêng biệt.
5. ĐA BỊ CÁO: Mỗi bị cáo xét theo ngày họ thực hiện hành vi.

Trả về JSON array:
[
  {
    "article": "Điều 168",
    "clause": "Khoản 2",
    "offense_name": "Tội cướp tài sản",
    "applicable_reason": "Lý do áp dụng điều này",
    "edition_applied": "BLHS 2015 (sửa đổi 2017)",
    "edition_reason": "Áp dụng luật tại thời điểm phạm tội. Luật 2025 không có lợi hơn."
  }
]
OUTPUT: CHỈ JSON array hợp lệ."""

        try:
            response = llm.invoke(_sanitize_msgs([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"SỰ KIỆN:\n{facts_str}\n\nVĂN BẢN LUẬT (có nhãn role):\n{context}\n\nVỤ ÁN:\n{case_text}")
            ]))
            raw    = re.sub(r"```(?:json)?\s*", "", response.content.strip()).strip()
            mapped = json.loads(raw)
            if not isinstance(mapped, list) or len(mapped) == 0:
                raise ValueError("Empty or non-list mapped_laws")
        except Exception as e:
            print(f"⚠️  Law mapping failed: {e}")
            mapped = [{
                "article": "N/A", "clause": "N/A",
                "offense_name": "Không xác định được",
                "applicable_reason": "Hệ thống không thể ánh xạ điều luật từ các tài liệu đã trích xuất.",
                "edition_applied": "N/A",
                "edition_reason": "Lỗi phân tích pháp luật.",
                "_mapping_error": True,
            }]

        return {"mapped_laws": mapped}


    # NODE: GENERATE
    def generate(state: AgentState) -> dict:
        print("[NODE: generate]")
        case_details = state.get("full_case_content", state["question"])
        documents = state["documents"]
        role = state.get("user_role", "neutral")
        sentencing_data = state.get("sentencing_data", {})
        mapped_laws = state.get("mapped_laws", [])
        history = state.get("chat_history", [])

        if not documents:
            return {"messages": [AIMessage(content="Xin lỗi, tôi chưa tìm thấy văn bản luật phù hợp.")]}

        context_text = sanitize_text("\n\n".join([
            f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','Unknown')} | "
            f"role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
            for d in documents
        ]))

        # ── DEBUG: show exactly what chunks go into the LLM ────────────
        print(f"  [GENERATE INPUT] {len(documents)} docs → LLM (role={role}):")
        for _d in documents:
            _art  = _d.metadata.get("article_number", "?")
            _src  = _d.metadata.get("source", "?")
            _rtag = _d.metadata.get("_temporal_role", "?")
            _pin  = "📌 " if _d.metadata.get("_pinned") else "   "
            _prev = _d.page_content[:100].replace("\n", " ")
            print(f"  {_pin}Điều {str(_art):>4} | {str(_src):<30} | {str(_rtag):<12} | {_prev}...")
        # ─────────────────────────────────────────────────────────────

        if role == "defense":
            role_instruction = "VAI TRÒ: LUẬT SƯ BÀO CHỮA cho bị cáo. Mục tiêu: Tìm mọi căn cứ để giảm nhẹ hình phạt xuống mức thấp nhất (hoặc Án treo). Đứng trên góc nhìn luật sư bào chữa, không phải tòa án."
        elif role == "victim":
            role_instruction = "VAI TRÒ: LUẬT SƯ BẢO VỆ BỊ HẠI. Mục tiêu: Yêu cầu xử nghiêm minh và bồi thường tối đa."
        else:
            role_instruction = "VAI TRÒ: THẨM PHÁN CHỦ TỌA. Tư duy: Lạnh lùng, Chính xác, Chỉ dựa trên chứng cứ trong hồ sơ."

        # Deterministic data context
        det_context = ""
        if sentencing_data:
            lines = []
            if "detention_months" in sentencing_data:
                lines.append(f"- Thời gian tạm giam đã tính: {sentencing_data['detention_months']} tháng")
            if "victim_age_at_crime" in sentencing_data:
                minor = sentencing_data.get("victim_is_minor", False)
                lines.append(f"- Tuổi nạn nhân tại thời điểm phạm tội: {sentencing_data['victim_age_at_crime']} tuổi ({'DƯỚI 18 TUỔI' if minor else 'TRÊN 18 TUỔI'})")
            if "defendant_age_at_crime" in sentencing_data:
                lines.append(f"- Tuổi bị cáo tại thời điểm phạm tội: {sentencing_data['defendant_age_at_crime']} tuổi")
            if lines:
                det_context = "\n\n⚙️  DỮ LIỆU ĐÃ TÍNH TOÁN CHÍNH XÁC (BẮT BUỘC SỬ DỤNG):\n" + "\n".join(lines)

        mapped_context = ""
        mapped = state.get("mapped_laws") or []
        if any(m.get("_mapping_error") for m in mapped):
            preamble = (
                "\u26a0\ufe0f H\u1ec7 th\u1ed1ng kh\u00f4ng th\u1ec3 \u00e1nh x\u1ea1 \u0111i\u1ec1u lu\u1eadt ch\u00ednh x\u00e1c. "
                "Ph\u00e2n t\u00edch d\u01b0\u1edbi \u0111\u00e2y d\u1ef1a tr\u00ean v\u0103n b\u1ea3n lu\u1eadt \u0111\u00e3 tr\u00fa xu\u1ea5t.\n\n"
            )
        else:
            preamble = ""
        if mapped:
            mapped_context = "\n\n\ud83d\udccb \u00c1NH X\u1ea0 \u0110I\u1ec0U LU\u1eacT \u0110\u1ec0 XU\u1ea4T:\n" + "\n".join([
                f"- {m.get('offense_name', '')} \u2192 {m.get('article', '')} {m.get('clause', '')} "
                f"[{m.get('edition_applied', '')}]: {m.get('applicable_reason', '')}"
                for m in mapped
            ])
            mapped_context = sanitize_text(mapped_context)
            mapped_context += (
                "\n\nNGUY\u00caN T\u1eaec TH\u1edcI HI\u1ec6U B\u1eaeT BU\u1ed8C (\u0110i\u1ec1u 7 BLHS):\n"
                "- Lu\u1eadt \u00e1p d\u1ee5ng = lu\u1eadt c\u00f3 hi\u1ec7u l\u1ef1c T\u1ea0I TH\u1edcI \u0110I\u1ec2M PH\u1ea0M T\u1ed8I (role=primary).\n"
                "- N\u1ebfu lu\u1eadt m\u1edbi h\u01a1n (role=comparison) NH\u1eb8 H\u01a0N \u2192 b\u1eaft bu\u1ed9c \u00e1p d\u1ee5ng.\n"
                "- N\u1ebfu lu\u1eadt m\u1edbi N\u1eb6NG H\u01a0N \u2192 C\u1ea4M \u00e1p d\u1ee5ng h\u1ed3i t\u1ed1.\n"
                "- M\u1ed7i t\u1ed9i danh so s\u00e1nh \u0111\u1ed9c l\u1eadp.\n"
                "Cu\u1ed1i ph\u1ea3n h\u1ed3i LU\u00d4N th\u00eam b\u1ea3ng:\n"
                "**\u0110I\u1ec0U KHO\u1ea2N \u00c1P D\u1ee4NG:**\n"
                "| \u0110i\u1ec1u | T\u1ed9i danh | Ngu\u1ed3n \u00e1p d\u1ee5ng | L\u00fd do ch\u1ecdn ngu\u1ed3n |\n"
                "|------|----------|---------------|------------------|\n"
            )

        # ── Nhân thân context — role-aware ────────────────────────────────
        # co_tien_an = True means prior conviction(s) exist (even if án tích cleared).
        # Judge / victim: warn it raises sentence into mid-range, bars án treo.
        # Defense: reframe as advantage (án tích cleared → no tái phạm under Điều 52).
        nhan_than_context = ""
        _facts = state.get("extracted_facts") or {}
        if _facts.get("co_tien_an"):
            if role in ("neutral", "victim"):
                nhan_than_context = (
                    "\n\n⚠️ NHÂN THÂN BỊ CÁO (BẮT BUỘC CÂN NHẮC KHI LƯỢNG HÌNH):\n"
                    "- Bị cáo CÓ TIỀN ÁN. Dù án tích đã được xóa theo luật nên KHÔNG áp dụng tình tiết\n"
                    "  tái phạm (Điều 52 BLHS), nhưng NHÂN THÂN XẤU VẪN là yếu tố Tòa án phải cân nhắc\n"
                    "  khi quyết định mức hình phạt cụ thể TRONG KHUNG (Điều 45 BLHS).\n"
                    "- Hệ quả thực tiễn:\n"
                    "  • KHÔNG áp dụng án treo (nhân thân xấu là điều kiện loại trừ).\n"
                    "  • Mức hình phạt nên ở GIỮA HOẶC CAO HƠN GIỮA khung dù có 1–2 tình tiết giảm nhẹ.\n"
                    "  • Thực tiễn xét xử: Tòa thường lấy mức VKS đề nghị làm SÀN khi nhân thân xấu.\n"
                )
            else:  # defense
                nhan_than_context = (
                    "\n\n📋 VỀ TIỀN ÁN CỦA THÂN CHỦ (LUẬN ĐIỂM CÓ LỢI):\n"
                    "- Thân chủ đã chấp hành xong hình phạt và được XÓA ÁN TÍCH theo điều luật.\n"
                    "- Pháp lý: KHÔNG đủ điều kiện tái phạm (Điều 52 BLHS) → đây là lợi thế\n"
                    "  pháp lý quan trọng nhất cần nhấn mạnh trong luận điểm bào chữa.\n"
                    "- TUYỆT ĐỐI KHÔNG nhắc đến nhân thân tiêu cực hoặc các án tích đã xóa\n"
                    "  như yếu tố bất lợi trong luận điểm bào chữa.\n"
                    "- Tập trung vào: thành khẩn khai báo, hợp tác điều tra, khả năng cải tạo\n"
                    "  và điều kiện xá hội của thân chủ.\n"
                )
        # ─────────────────────────────────────────────────────────────────

        # ─────────────────────────────────────────────────────────────────
        # ROLE-SPECIFIC PROMPT TEMPLATES
        # Each template has its own persona, output structure, and framing
        # ─────────────────────────────────────────────────────────────────
        if role == "defense":
            prompt_template = """{role_instruction}

Nhiệm vụ: Đọc kỹ hồ sơ vụ án và SOẠN LUẬN ĐIỂM BÀO CHỮA để giảm nhẹ tối đa hình phạt cho thân chủ.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}
{mapped_context}
{nhan_than_context}
----------------

LƯU Ý KHI BÀO CHỮA:
0. **CHỈ trích dẫn điều khoản thuộc Bộ luật Hình sự (BLHS).** KHÔNG được nhắc đến bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự, hay bộ luật khác.
1. Ưu tiên tìm tình tiết giảm nhẹ (Điều 51 Bộ luật Hình sự): thành khẩn, bồi thường, nhân thân tốt, phạm tội lần đầu.
2. Phân tích xem có thể đề nghị án treo không (án ≤ 3 năm + không tái phạm + có nơi cư trú ổn định).
3. Nếu có nhiều tội, đề xuất tách riêng hoặc giảm nhẹ từng tội.
4. Trích dẫn chính xác điều khoản luật để tăng tính thuyết phục.
5. Kiểm tra thời gian tạm giam để đề nghị khấu trừ.

QUY TRÌNH TƯ DUY (BẮT BUỘC):
BƯỚC 1: XÁC ĐỊNH tình tiết giảm nhẹ có lợi nhất.
BƯỚC 2: ĐỀ XUẤT định tội danh nhẹ nhất có thể lập luận được.
BƯỚC 3: TÍNH khung hình phạt thấp nhất + khấu trừ tạm giam.
BƯỚC 4: Đánh giá khả năng án treo.

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. LUẬN ĐIỂM BÀO CHỮA:**
1. **Về định tội danh:** (phân tích theo hướng có lợi cho bị cáo)
2. **Tình tiết giảm nhẹ đề xuất (Điều 51 Bộ luật Hình sự):**
   - (liệt kê từng tình tiết + căn cứ pháp lý)
3. **Phân tích nhân thân bị cáo:**

**II. ĐỀ NGHỊ CỦA LUẬT SƯ BÀO CHỮA:**
1. Tội danh đề nghị: ...
2. Điều khoản áp dụng: ...
3. HÌNH PHẠT ĐỀ NGHỊ: (mức thấp nhất trong khung, hoặc dưới khung nếu có căn cứ)
4. Đề nghị án treo / cải tạo không giam giữ (nếu đủ điều kiện)
5. Khấu trừ thời gian tạm giam: ...

**III. KHUYẾN NGHỊ CHO THÂN CHỦ:**
(Các bước cụ thể: bồi thường, viết đơn xin khoan hồng, xin giấy bãi nại, nộp án phí...)
"""
        elif role == "victim":
            prompt_template = """{role_instruction}

Nhiệm vụ: Đọc kỹ hồ sơ vụ án và SOẠN LUẬN ĐIỂM BẢO VỆ QUYỀN LỢI BỊ HẠI, yêu cầu xử nghiêm minh và bồi thường tối đa.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}
{mapped_context}
{nhan_than_context}
----------------

LƯU Ý KHI BẢO VỆ BỊ HẠI:
0. **CHỈ trích dẫn điều khoản thuộc Bộ luật Hình sự (BLHS).** KHÔNG được nhắc đến bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự, hay bộ luật khác.
1. Tập trung làm rõ tình tiết tăng nặng (Điều 52 Bộ luật Hình sự): có tổ chức, tái phạm, hậu quả nghiêm trọng.
2. Phân tích mức độ thiệt hại để yêu cầu bồi thường dân sự tối đa.
3. Phản bác các tình tiết giảm nhẹ mà bị cáo có thể viện dẫn.
4. Đề nghị mức án cao nhất trong khung có thể lập luận.
5. Yêu cầu tịch thu công cụ, phương tiện phạm tội.

QUY TRÌNH TƯ DUY (BẮT BUỘC):
BƯỚC 1: XÁC ĐỊNH tình tiết tăng nặng có thể áp dụng.
BƯỚC 2: ĐỀ XUẤT định tội danh và khung nặng nhất phù hợp.
BƯỚC 3: TÍNH thiệt hại thực tế để yêu cầu bồi thường.
BƯỚC 4: Đề nghị mức án cụ thể.

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. LUẬN ĐIỂM BẢO VỆ BỊ HẠI:**
1. **Về định tội danh:** (phân tích theo hướng tội nặng nhất có thể áp dụng)
2. **Tình tiết tăng nặng đề nghị áp dụng (Điều 52 Bộ luật Hình sự):**
   - (liệt kê từng tình tiết + căn cứ pháp lý)
3. **Mức độ thiệt hại và yêu cầu bồi thường:**

**II. ĐỀ NGHỊ CỦA LUẬT SƯ BỊ HẠI:**
1. Tội danh đề nghị: ...
2. Điều khoản áp dụng: ...
3. HÌNH PHẠT ĐỀ NGHỊ: (mức cao nhất trong khung phù hợp)
4. Không chấp nhận án treo (nếu có căn cứ)
5. TRÁCH NHIỆM DÂN SỰ: (yêu cầu bồi thường cụ thể)

**III. KHUYẾN NGHỊ CHO GIA ĐÌNH BỊ HẠI:**
(Hướng dẫn thu thập hóa đơn, chứng từ thiệt hại, yêu cầu cấp dưỡng, bảo vệ quyền lợi dài hạn...)
"""
        else:  # neutral — judge perspective
            prompt_template = """{role_instruction}

Nhiệm vụ: Dựa trên dữ liệu vụ án (coi là sự thật duy nhất) và văn bản luật, hãy ra PHÁN QUYẾT CỤ THỂ.

--- DỮ LIỆU ---
<legal_context>
{context}
</legal_context>

<case_details>
{case_details}
</case_details>

{deterministic_context}
{mapped_context}
{nhan_than_context}
----------------

MỘT VÀI LƯU Ý:
0. **CHỈ trích dẫn điều khoản thuộc Bộ luật Hình sự (BLHS).** KHÔNG được nhắc đến bất kỳ điều nào của Bộ luật Tố tụng hình sự (BLTTHS), Bộ luật Dân sự, hay bộ luật khác.
1. Đối với tội liên quan tới sử dụng ma túy:
   - Phân biệt "tàng trữ" (Điều 249) và "tổ chức sử dụng" (Điều 255).
   - Kiểm tra nhân thân nạn nhân với Khoản 2 Điều 255.
2. Tình tiết giảm nhẹ: Điều 51 Bộ luật Hình sự mới (hoặc Điều 46 cũ).
3. Tình tiết tăng nặng: Điều 52 Bộ luật Hình sự mới (hoặc Điều 48 cũ).
4. Tội kinh tế: kiểm tra xem có thể phạt tiền thay phạt tù không.
5. Phạm tội chưa đạt (Điều 15, 57): áp dụng quy tắc 3/4.

QUY TRÌNH TƯ DUY LƯỢNG HÌNH (BẮT BUỘC THEO THỨ TỰ):
- KHÔNG GIẢ ĐỊNH: chỉ dùng tình tiết có trong case_details.
- NGUYÊN TẮC CÓ LỢI (Thời gian): tội trước 2018 → áp dụng Luật 2015/2017 nếu nhẹ hơn.
- NGUYÊN TẮC ĐỘC LẬP XÉT XỬ: đề nghị VKS chỉ là tham khảo.

BƯỚC 1: KIỂM TRA ÁN BẰNG THỜI GIAN TẠM GIAM (sử dụng số liệu đã tính ở trên nếu có).
BƯỚC 2: KIỂM TRA ĐỘ TUỔI (sử dụng số liệu đã tính ở trên nếu có).
BƯỚC 3: ĐỊNH TỘI DANH.
BƯỚC 4: LƯỢNG HÌNH CHO TỪNG TỘI.
BƯỚC 5: TỔNG HỢP HÌNH PHẠT (Điều 55).
BƯỚC 5.5: TỔNG HỢP VỚI BẢN ÁN CŨ (nếu có).
BƯỚC 6: QUYẾT ĐỊNH HÌNH THỨC CHẤP HÀNH (Án treo chỉ khi tổng án ≤ 3 năm).

---------------------------------------------------------
CẤU TRÚC OUTPUT BẮT BUỘC:

**I. NHẬN ĐỊNH CỦA TÒA ÁN:**
1. **Định tội danh:** (liệt kê từng hành vi + điều khoản + khung hình phạt)
2. **Phân tích tình tiết:**
   - Tình tiết Tăng nặng (Điều 52):
   - Tình tiết Giảm nhẹ (Điều 51):
3. **Nhân thân:**

**II. QUYẾT ĐỊNH:**
1. Tuyên bố bị cáo phạm tội...
2. Áp dụng điều khoản...
3. HÌNH PHẠT: (tù giam HOẶC phạt tiền, chọn 1)
4. TRÁCH NHIỆM DÂN SỰ & XỬ LÝ VẬT CHỨNG
5. ÁN PHÍ: 200.000 đồng
"""

        prompt = ChatPromptTemplate.from_template(prompt_template)
        
        # Biến đổi history thành dạng List[BaseMessage]
        history_msgs = []
        if history:
            # Lấy 4 tin nhắn gần nhất để tránh tràn context
            for msg in history[-4:]:
                if msg.get("role") == "user":
                    history_msgs.append(HumanMessage(content=sanitize_text(msg.get("content", ""))))
                else:
                    history_msgs.append(AIMessage(content=sanitize_text(msg.get("content", ""))))
        
        chain = prompt | llm | StrOutputParser()

        try:
            # Tạo prompt chính thức
            formatted_prompt = prompt.format_messages(
                role_instruction=role_instruction,
                context=context_text,
                case_details=case_details,
                deterministic_context=det_context,
                mapped_context=mapped_context,
                nhan_than_context=nhan_than_context,
            )
            
            # Nối lịch sử vào TRƯỚC prompt chính nhưng SAU system prompt (nếu có thể),
            # hoặc đơn giản là ghép tất cả lại. ChatPromptTemplate trả ra List[BaseMessage]
            final_messages = history_msgs + formatted_prompt
            
            response = llm.invoke(_sanitize_msgs(final_messages)).content
        except Exception as e:
            return {"messages": [AIMessage(content=f"Lỗi xử lý: {e}")]}

        # Clean up BLHS abbreviations in response
        cleaned_response = cleanup_response(response)
        return {"messages": [AIMessage(content=cleaned_response)]}

    # NODE: REBUTTAL (Study Mode — grades user's legal argument)
    def rebuttal_node(state: AgentState) -> dict:
        """Study mode: grade user's legal argument against ground-truth mapped_laws."""
        print("[NODE: rebuttal]")
        role          = state.get("user_role", "neutral")
        mapped_laws   = state.get("mapped_laws") or []
        documents     = state.get("documents") or []
        user_argument = state.get("rebuttal_against", "")

        context_text = sanitize_text("\n\n".join([
            f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','Unknown')} | "
            f"role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
            for d in documents
        ]))

        system_prompt = f"""Bạn là giám khảo môn luật hình sự Việt Nam.
Người dùng phân tích vụ án từ góc độ: {role}.
Chấm điểm và nhận xét lập luận của họ so với kết quả chuẩn.

TIÊU CHÍ (100 điểm):
1. Điều luật viện dẫn đúng không? (40đ)
2. Phiên bản BLHS áp dụng đúng không? (20đ)
3. Lập luận phù hợp góc độ {role}? (20đ)
4. Tình tiết tăng nặng / giảm nhẹ chính xác? (20đ)

Định dạng:
**Điểm tổng: X/100**
**Nhận xét:** ...
**Điểm mạnh:** ...
**Cần cải thiện:** ...
**Gợi ý chuẩn:** ..."""

        response = (llm.invoke(_sanitize_msgs([
            SystemMessage(content=system_prompt),
            HumanMessage(content=(
                f"LẬP LUẬN NGƯỜI DÙNG:\n{user_argument}\n\n"
                f"KẾT QUẢ CHUẨN:\n{json.dumps(mapped_laws, ensure_ascii=False)}\n\n"
                f"VĂN BẢN LUẬT:\n{context_text}"
            ))
        ]))).content
        return {"messages": [AIMessage(content=response)]}



    # -------------------------------------------------------
    # INTENT CLASSIFICATION — 3-way router
    # -------------------------------------------------------
    def classify_intent(state: AgentState) -> str:
        """
        Route messages into one of 3 paths:
          'casual'   — greeting, chit-chat, off-topic → simple canned response
          'followup' — elaboration/question about a prior AI response
          'new_case' — penal law case or legal question → full RAG pipeline
        """
        history = state.get("chat_history", []) or []
        question = state["question"].strip()

        # Fast rule: very short input with no legal keywords → likely casual
        LEGAL_KEYWORDS = ["điều", "khoản", "bộ luật", "tội", "hình phạt", "bị cáo",
                          "bị hại", "tòa án", "viện kiểm sát", "ngày", "năm", "tháng",
                          "tạm giam", "xét xử", "phạt", "án", "hành vi", "law", "penal"]
        is_short = len(question) < 120
        has_legal = any(kw in question.lower() for kw in LEGAL_KEYWORDS)

        if is_short and not has_legal and not history:
            print(f"  [INTENT] Short + no legal keywords + no history → casual")
            return "casual"

        # Long input is almost certainly a new case dump
        if len(question) > 500:
            print("  [INTENT] Long input → new_case")
            return "new_case"

        # No history → first message, send to full pipeline
        if not history:
            print("  [INTENT] No history → new_case")
            return "new_case"

        classification_prompt = (
            "Bạn là bộ phân loại đầu vào cho một hệ thống chatbot pháp luật hình sự Việt Nam.\n"
            f"Lịch sử hội thoại có {len(history)} tin nhắn.\n"
            f"Tin nhắn mới của người dùng: \"{question[:400]}\"\n\n"
            "Phân loại tin nhắn này thành MỘT trong ba loại:\n"
            "- \"casual\": Chào hỏi, hỏi chatbot là gì, nói chuyện phiếm, hoặc nội dung "
            "HOÀN TOÀN không liên quan đến pháp luật hình sự.\n"
            "- \"followup\": Hỏi thêm, yêu cầu giải thích, phân tích lại điểm cụ thể, "
            "cung cấp thêm thông tin mới để AI xem xét lại — LIÊN QUAN đến phân tích AI đã trả lời.\n"
            "- \"new_case\": Hồ sơ vụ án mới hoàn toàn hoặc câu hỏi pháp lý mới "
            "không liên quan đến cuộc hội thoại hiện tại.\n\n"
            "Chỉ trả về đúng một từ: \"casual\", \"followup\", hoặc \"new_case\"."
        )
        try:
            result = llm.invoke(_sanitize_msgs([HumanMessage(content=classification_prompt)])).content.strip().lower()
            if "casual" in result:
                intent = "casual"
            elif "followup" in result:
                intent = "followup"
            else:
                intent = "new_case"
        except Exception:
            intent = "new_case"  # fail-safe

        print(f"  [INTENT] → {intent} | query='{question[:80]}'")
        return intent

    # NODE: CASUAL RESPOND
    def casual_respond(state: AgentState) -> dict:
        """Handle greetings, off-topic, and unrelated queries."""
        print("[NODE: casual_respond]")
        question = state["question"].strip().lower()

        # Detect greeting
        GREETINGS = ["hi", "hello", "chào", "xin chào", "hey", "helo", "ola"]
        is_greeting = any(q in question for q in GREETINGS) or len(question) <= 10

        if is_greeting:
            reply = (
                "Xin chào! Tôi là **Trợ lý Pháp luật Hình sự VNPLaw**\n\n"
                "Tôi được xây dựng chuyên biệt để hỗ trợ **phân tích và tra cứu pháp luật hình sự Việt Nam**. "
                "Dưới đây là những gì tôi có thể làm cho bạn:\n\n"
                " **Phân tích vụ án hình sự**\n"
                "   Định tội danh, xác định khung hình phạt, lượng hình cụ thể theo Bộ luật Hình sự\n\n"
                " **Phân tích đa chiều theo vai trò**\n"
                "   • *Thẩm phán* — nhận định trung lập, khách quan\n"
                "   • *Luật sư bào chữa* — lập luận giảm nhẹ, bảo vệ bị cáo\n"
                "   • *Luật sư bị hại* — yêu cầu xử nghiêm, bồi thường tối đa\n\n"
                " **Tra cứu & giải thích điều luật**\n"
                "   Trích dẫn chính xác điều khoản BLHS 2015 (sửa đổi 2017/2025), giải thích tình tiết tăng nặng/giảm nhẹ\n\n"
                "---\n"
                "💡 **Cách dùng:** Dán toàn bộ nội dung hồ sơ vụ án và tôi sẽ bắt đầu phân tích.\n"
                "*Ví dụ: \"Ngày 15/3/2023, Nguyễn Văn A dùng dao đe dọa lấy tài sản của bị hại...\"*"
            )
        else:
            reply = (
                "Xin lỗi, lĩnh vực này nằm ngoài phạm vi hỗ trợ của tôi. 🙏\n\n"
                "Tôi chuyên về **pháp luật hình sự Việt Nam** — nếu bạn có:\n"
                "- Hồ sơ vụ án cần phân tích tội danh và hình phạt\n"
                "- Câu hỏi về điều khoản BLHS, tình tiết tăng nặng/giảm nhẹ\n"
                "- Cần lập luận theo góc độ thẩm phán, luật sư bào chữa hoặc luật sư bị hại\n\n"
                "Hãy chia sẻ và tôi sẽ hỗ trợ ngay! ⚖️"
            )

        return {"messages": [AIMessage(content=reply)]}

    # NODE: FOLLOW-UP GENERATE
    def followup_generate(state: AgentState) -> dict:
        """
        Handle follow-up questions about a prior response.

        BUG-3 FIX: When intent='followup', the graph routes START→followup directly,
        bypassing the full pipeline (extract_facts → retrieve → map_laws).
        This means state['documents'] is always [] and state['mapped_laws'] is always None.
        Fix: retrieve fresh law context using the question + any cached mapped_laws
        article numbers, so the follow-up response has legal grounding.
        """
        print("[NODE: followup_generate]")
        mapped_laws  = state.get("mapped_laws") or []
        chat_history = (state.get("chat_history") or [])[-6:]
        question     = state["question"]
        role         = state.get("user_role", "neutral")

        # Try to get cached documents first; if empty (followup bypasses pipeline),
        # do a lightweight retrieval using the user's follow-up question.
        documents = state.get("documents") or []
        if not documents:
            try:
                documents = retriever.invoke(question[:512])
                print(f"  [FOLLOWUP] Retrieved {len(documents)} docs (fresh — pipeline bypassed)")
            except Exception as e:
                print(f"  [FOLLOWUP] Retrieval failed: {e}")
                documents = []

        context_text = sanitize_text("\n\n".join([
            f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','Unknown')} | "
            f"role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
            for d in documents
        ]))
        history_messages = [
            HumanMessage(content=m["content"]) if m["role"] == "user"
            else AIMessage(content=m["content"])
            for m in chat_history
        ]
        response = llm.invoke(_sanitize_msgs([
            SystemMessage(content=(
                f"Bạn là chuyên gia luật hình sự Việt Nam, góc độ: {role}.\n"
                "Dựa vào văn bản luật và kết quả ánh xạ đã có, trả lời câu hỏi tiếp theo.\n"
                "Giữ nguyên quy tắc hiệu lực luật (Điều 7 Bộ luật Hình sự) từ lượt phân tích trước."
            )),
            *history_messages,
            HumanMessage(content=(
                f"CÂU HỎI: {question}\n\n"
                f"VĂN BẢN LUẬT (tham khảo):\n{context_text}\n\n"
                f"KẾT QUẢ ÁNH XẠ (nếu có):\n{json.dumps(mapped_laws, ensure_ascii=False)}"
            ))
        ]))
        return {"messages": [AIMessage(content=cleanup_response(response.content))]}

    # -------------------------------------------------------
    # BUILD LANGGRAPH
    # -------------------------------------------------------
    workflow = StateGraph(AgentState)

    workflow.add_node("extract_facts",            extract_facts_node)
    workflow.add_node("clarification_check",      clarification_check_node)
    workflow.add_node("clarification",            clarification_node)
    workflow.add_node("multi_query_rewrite",      multi_query_rewrite)
    workflow.add_node("parallel_retrieve",        parallel_retrieve)
    workflow.add_node("temporal_priority_tagger", temporal_priority_tagger)
    workflow.add_node("rerank",                   rerank_node)
    workflow.add_node("map_laws",                 map_laws_node)
    workflow.add_node("generate",                 generate)
    workflow.add_node("rebuttal",                 rebuttal_node)
    workflow.add_node("followup",                 followup_generate)
    workflow.add_node("casual",                   casual_respond)

    # START → 3-way intent router
    workflow.add_conditional_edges(
        START,
        classify_intent,
        {"new_case": "extract_facts", "followup": "followup", "casual": "casual"}
    )
    workflow.add_edge("extract_facts",            "clarification_check")
    workflow.add_conditional_edges(
        "clarification_check",
        clarification_router,
        {"clarify": "clarification", "continue": "multi_query_rewrite"}
    )
    workflow.add_edge("clarification",             END)
    workflow.add_edge("multi_query_rewrite",       "parallel_retrieve")
    workflow.add_edge("parallel_retrieve",         "temporal_priority_tagger")
    workflow.add_edge("temporal_priority_tagger",  "rerank")
    workflow.add_edge("rerank",                    "map_laws")
    workflow.add_conditional_edges(
        "map_laws",
        check_rebuttal,
        {"rebuttal": "rebuttal", "generate": "generate"}
    )
    workflow.add_edge("generate",   END)
    workflow.add_edge("rebuttal",   END)
    workflow.add_edge("followup",   END)
    workflow.add_edge("casual",     END)

    app_compiled = workflow.compile()
    # BUG-14 FIX: Store llm in app_state so /practice/evaluate can reuse it
    # instead of creating a new ChatOpenAI client on every request.
    app_state["llm"] = llm
    app_state["graph"] = app_compiled
    app_state["model_loaded"] = True

    print(f"✅ System Ready! Device: {DEVICE}")
    yield
    print("🛑 Shutting down...")


# ===========================================================
# FASTAPI APP
# ===========================================================
app = FastAPI(
    title="Vietnamese Legal AI Chatbot — AI Service",
    description="RAG-powered legal analysis using LangGraph + Milvus + OpenRouter",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="ok",
        device=DEVICE,
        model_loaded=app_state.get("model_loaded", False)
    )


@app.post("/predict", response_model=PredictResponse)
async def predict_judgment(req: RequestBody):
    graph = app_state.get("graph")
    if not graph:
        raise HTTPException(status_code=500, detail="Model not loaded")

    inputs = {
        "question":            sanitize_text(req.case_content),
        "full_case_content":   sanitize_text(req.case_content),
        "messages":            [HumanMessage(content=sanitize_text(req.case_content))],
        "user_role":           req.role,
        "retry_count":         0,
        "documents":           [],
        "retrieval_queries":   [],
        "extracted_facts":     None,
        "mapped_laws":         None,
        "sentencing_data":     None,
        "is_relevant":         None,
        "_missing_fields":     None,
        "per_defendant_dates": None,
        "rebuttal_against":    req.rebuttal_against,
        "chat_history":        req.conversation_history,
    }

    try:
        output = await graph.ainvoke(inputs)
        final_answer = output["messages"][-1].content

        # Sanitize mapped_laws: replace any None field values with "" to
        # avoid Pydantic validation errors when no legal content was found
        raw_laws = output.get("mapped_laws") or []
        clean_laws = [
            {k: (v if v is not None else "") for k, v in law.items()}
            for law in raw_laws
        ] or None

        return PredictResponse(
            result=final_answer,
            extracted_facts=output.get("extracted_facts"),
            mapped_laws=clean_laws,
            sentencing_data=output.get("sentencing_data"),
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[PREDICT ERROR] {type(e).__name__}: {e}\n{tb}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")



# ===========================================================
# PRACTICE / EVALUATE ENDPOINT
# ===========================================================
@app.post("/practice/evaluate", response_model=PracticeEvalResponse)
async def practice_evaluate(req: PracticeEvalRequest):
    """
    Evaluate a user's legal analysis from the perspective of the chosen role.
    Returns a score (0–100) plus structured feedback.
    """
    # BUG-03 FIX: Reuse the llm from app_state instead of creating a new
    # ChatOpenAI instance on every request. A new client per-request is:
    #   (1) wasteful — re-initializes the HTTP client each time, and
    #   (2) silently broken — if OPENROUTER_API_KEY is not set, the client is
    #       constructed without error but fails only when .invoke() is called.
    llm_instance = app_state.get("llm")
    if not llm_instance:
        raise HTTPException(status_code=503, detail="Model not loaded — service is still starting up")

    role_label = {
        "neutral": "Thẩm phán (trung lập)",
        "defense": "Luật sư bào chữa (bảo vệ bị cáo)",
        "victim": "Luật sư bảo vệ bị hại",
    }.get(req.user_mode, "Chuyên gia pháp lý")

    role_criteria = {
        "neutral": """
- Đánh giá khả năng xác định tội danh đúng điều luật.
- Kiểm tra việc phân tích tình tiết tăng nặng/giảm nhẹ theo Điều 51, 52 Bộ luật Hình sự.
- Kiểm tra việc lượng hình (mức phạt hợp lý, tổng hợp theo Điều 55 nếu nhiều tội).
- Kiểm tra xem đã trừ thời gian tạm giam chưa.
- Đánh giá tính trung lập, khách quan của phân tích.
""",
        "defense": """
- Đánh giá khả năng tìm và chứng dẫn tình tiết giảm nhẹ (có theo Điều 51 không).
- Kiểm tra đề xuất án treo hoặc cải tạo không giam giữ (có căn cứ pháp lý không).
- Đánh giá luận điểm bào chữa: có bác bỏ tình tiết tăng nặng hiệu quả không.
- Kiểm tra đề nghị khấu trừ thời gian tạm giam.
- Đánh giá có xuất hiện yêu cầu giảm nhẹ tội danh không.
""",
        "victim": """
- Đánh giá khả năng xác định tình tiết tăng nặng (có theo Điều 52 không).
- Kiểm tra yêu cầu bồi thường dân sự: có căn cứ thiệt hại không.
- Đánh giá luận điểm phản bác tình tiết giảm nhẹ của bị cáo.
- Kiểm tra đề nghị mức án cao nhất có căn cứ không.
- Đánh giá có yêu cầu tịch thu công cụ, vật chứng không.
""",
    }.get(req.user_mode, "")

    eval_prompt = f"""Bạn là giáo sư luật hình sự Việt Nam đang đánh giá phân tích pháp lý của người dùng.

VU ÁN:
{req.case_description}

VAI TRÒ NGƯỜI DÙNG: {role_label}

PHÂN TÍCH CỦA NGƯỜI DÙNG:
{req.user_analysis}

TIÊU CHÍ ĐÁNH GIÁ (theo vai trò {role_label}):
{role_criteria}

NHIỆM VỤ: Đánh giá chất lượng phân tích pháp lý trên. Chấm điểm từ 0 đến 100.

Trả về JSON hợp lệ (không markdown, không giải thích bên ngoài JSON) theo cấu trúc sau:
{{
  "score": <số nguyên từ 0 đến 100>,
  "feedback": {{
    "strengths": ["<điểm mạnh 1>", "<điểm mạnh 2>", ...],
    "improvements": ["<cần cải thiện 1>", "<cần cải thiện 2>", ...],
    "missed_articles": ["<Điều X Bộ luật Hình sự (ý nghĩa hoặc ghi chú)>", ...],
    "suggestion": "<Gợi ý tổng hợp cho người dùng>"
  }}
}}

Quy tắc:
- strengths: 2–4 điểm tích cực cụ thể (trích dẫn rõ luận điểm người dùng).
- improvements: 2–5 điểm cần cải thiện, cụ thể, có đều khoản tham chiếu.
- missed_articles: liệt kê các điều luật quan trọng mà người dùng bỏ sót (có thể rỗng nếu đầy đủ). TRONG TRƯỜNG HỢP CÓ CẦN CHỈ RÕ PHIÊN BẢN, DÙNG CÁCH VIẾT: "Điều 51 Bộ luật Hình sự 2015", không dùng từ viết tắt BLHS.
- suggestion: 1–2 câu gợi ý cụ thể nhất cho người dùng.
OUTPUT: CHỈ JSON."""

    try:
        response = llm_instance.invoke([HumanMessage(content=eval_prompt)])
        raw = response.content.strip()
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
        data = json.loads(raw)

        # Clean up BLHS references in missed_articles
        missed_articles = data.get("feedback", {}).get("missed_articles", [])
        cleaned_missed_articles = []
        for article in missed_articles:
            # Replace "BLHS" with full name or remove if just cleanup needed
            cleaned = re.sub(r"\bBLHS\b", "Bộ luật Hình sự", article, flags=re.IGNORECASE)
            cleaned_missed_articles.append(cleaned)

        feedback = data.get("feedback", {})
        return PracticeEvalResponse(
            score=int(data.get("score", 50)),
            feedback=PracticeEvalFeedback(
                strengths=feedback.get("strengths", []),
                improvements=feedback.get("improvements", []),
                missed_articles=cleaned_missed_articles,
                suggestion=feedback.get("suggestion", ""),
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Practice evaluation failed: {e}")


if __name__ == "__main__":
    import uvicorn, logging

    # Suppress the harmless "Invalid HTTP request received" warning that uvicorn/httptools
    # emits when stale keep-alive connections, TLS probes, or malformed TCP frames arrive.
    # These are NOT application bugs — they are noise from connection pool cleanup.
    class _SuppressInvalidHTTP(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "Invalid HTTP request received" not in record.getMessage()

    logging.getLogger("uvicorn.error").addFilter(_SuppressInvalidHTTP())

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
