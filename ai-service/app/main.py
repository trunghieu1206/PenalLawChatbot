"""
VNPLaw — FastAPI AI Service  (refactored)
All heavy node logic lives in app/graph/nodes/.
This file handles only: OS env setup, lifespan startup, and FastAPI routes.

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
# CPU cores optimally.
# intra_op_threads: parallelism WITHIN a single op (e.g. matrix multiplication)
# inter_op_threads: parallelism BETWEEN independent ops (pipeline parallelism)
_cores = os.cpu_count() or 4
os.environ["OMP_NUM_THREADS"]      = str(_cores)
os.environ["MKL_NUM_THREADS"]      = str(_cores)
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


# ── Standard library + third-party imports ───────────────────────────────────
import json
import uuid
from contextlib import asynccontextmanager
from typing import List, Literal, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from huggingface_hub import login

from langchain_core.messages import HumanMessage
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI
from pymilvus import MilvusClient

# ── Internal imports (after env vars and shims are set) ──────────────────────
from app.core.config import (
    MILVUS_URI,
    COLLECTION_NAME,
    TOP_K,
    LLM_MODEL,
    OUTPUT_FIELDS,
    DEVICE,
    _DEFAULT_EMBEDDING,
)
from app.utils.text import sanitize_text
from app.utils.clean_json import _extract_json
from app.services.embeddings import JinaEmbeddings, MilvusRetriever
from app.services.reranker import load_reranker, make_rerank_scorer
from app.services.container import Services
from app.graph.builder import build_graph

# Load Environment Variables
load_dotenv()

# ── Module-level singleton (populated in lifespan) ────────────────────────────
_svc = Services(llm=None, retriever=None, milvus_client=None, reranker_fn=None)


# ===========================================================
# REQUEST / RESPONSE MODELS
# ===========================================================
class RequestBody(BaseModel):
    case_content: str
    role: Literal["defense", "victim", "neutral"] = "neutral"
    conversation_history: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    session_id: Optional[str] = None  # thread_id for LangGraph MemorySaver checkpointing


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


class PracticeEvalRequest(BaseModel):
    case_description: str
    user_mode: Literal["defense", "victim", "neutral"] = "neutral"
    user_analysis: str


class PracticeEvalFeedback(BaseModel):
    strengths: List[str]
    improvements: List[str]
    suggestion: str
    suggested_laws: List[Dict[str, str]] = Field(default_factory=list)
    # Each item: {"article": "93", "clause": "Khoản 1", "offense_name": "Giết người", "source": "BLHS 1999"}


class PracticeEvalResponse(BaseModel):
    score: int
    feedback: PracticeEvalFeedback


# ===========================================================
# LIFESPAN — startup / shutdown
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

    # ── HuggingFace authentication ────────────────────────────────────────────
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        try:
            login(token=hf_token)
            print("✅ Logged in to Hugging Face successfully.")
        except Exception as e:
            print(f"⚠️  Failed HF login: {e}")
    else:
        print("⚠️  'HF_TOKEN' not set. Public models only.")

    # ── 1. Embedding model  (Jina v5 Nano via SentenceTransformer) ───────────
    # EMBEDDING_ADAPTER env var overrides the default fine-tuned model.
    # EMBEDDING_MODEL is a secondary alias for backward compat.
    _jina_model = (
        os.getenv("EMBEDDING_ADAPTER")
        or os.getenv("EMBEDDING_MODEL")
        or _DEFAULT_EMBEDDING
    )
    print(f"📌 Embedding model: {_jina_model}")
    # Scale batch size with device: GPU can process larger batches efficiently.
    # EMBEDDING_BATCH_SIZE env var always wins; default 64 on GPU, 32 on CPU.
    _default_batch = "64" if DEVICE.startswith("cuda") else "32"
    embedding_model = JinaEmbeddings(
        model_name=_jina_model,
        device=DEVICE,
        batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", _default_batch)),
    )

    # ── 2. Milvus-Lite vector store — use MilvusClient directly ──────────────
    # (avoids langchain_milvus version issues and MILVUS_URI env collision)
    print(f"📦 Connecting to Milvus Lite DB: {MILVUS_URI}")
    milvus_client = MilvusClient(uri=MILVUS_URI)

    retriever = MilvusRetriever(
        milvus_client=milvus_client,
        embeddings=embedding_model,
        collection_name=COLLECTION_NAME,
        top_k=TOP_K,
        output_fields=list(OUTPUT_FIELDS),
    )
    print(f"✅ Milvus ready — collection '{COLLECTION_NAME}'")

    # ── 3. LLM (OpenRouter) ───────────────────────────────────────────────────
    llm = ChatOpenAI(
        model=LLM_MODEL,
        openai_api_key=os.getenv("OPENROUTER_API_KEY"),
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0,
    )

    # ── 4. Cross-encoder reranker ─────────────────────────────────────────────
    # Uses AutoModelForSequenceClassification directly to bypass sentence_transformers
    # wrapper layers that break on transformers ≥4.57 (prepare_for_model / BatchEncoding).
    _RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    reranker_tokenizer, reranker_model = load_reranker(_RERANKER_MODEL, DEVICE)
    reranker_fn = make_rerank_scorer(reranker_tokenizer, reranker_model, DEVICE)

    # Log VRAM usage after all models are loaded on this worker
    if DEVICE.startswith("cuda"):
        _gpu_idx   = int(DEVICE.split(":")[-1]) if ":" in DEVICE else 0
        _mem_used  = torch.cuda.memory_allocated(_gpu_idx) // (1024 ** 2)
        _mem_rsvd  = torch.cuda.memory_reserved(_gpu_idx)  // (1024 ** 2)
        _mem_total = torch.cuda.get_device_properties(_gpu_idx).total_memory // (1024 ** 2)
        print(
            f"📊 GPU {_gpu_idx} VRAM after model load: "
            f"{_mem_used} MB allocated / {_mem_rsvd} MB reserved / {_mem_total} MB total"
        )

    # ── 5. BM25 Keyword Index (built once at startup from Milvus corpus) ──────
    # rank_bm25 is a pure-Python library — no GPU, no latency per request.
    # We load the entire Milvus collection into memory once and tokenize it.
    # Each request then calls bm25_index.get_scores() which is microsecond-fast.
    #
    # Fault-tolerant design:
    #   - If rank_bm25 is not installed  → server starts normally, BM25 disabled.
    #   - If Milvus full-scan fails       → server starts normally, BM25 disabled.
    #   - If BM25 returns no results      → silently skipped, no crash.
    bm25_index = None
    bm25_docs: List[Document] = []

    try:
        from rank_bm25 import BM25Okapi

        print("📚 Building BM25 keyword index from Milvus corpus...")
        _all_milvus_docs: List[Document] = []

        # Query ALL documents. offset/limit paging handles large collections.
        _batch_size = 1000
        _offset     = 0
        _page_num   = 0
        while True:
            _batch = milvus_client.query(
                collection_name=COLLECTION_NAME,
                filter="",               # no filter = all documents
                output_fields=list(OUTPUT_FIELDS),
                limit=_batch_size,
                offset=_offset,
            )
            if not _batch:
                break
            _page_num += 1
            for h in _batch:
                _all_milvus_docs.append(Document(
                    page_content=sanitize_text(h.get("content", "")),
                    metadata={
                        k: sanitize_text(h.get(k, "")) if isinstance(h.get(k, ""), str) else h.get(k, "")
                        for k in OUTPUT_FIELDS if k != "content"
                    },
                ))
            print(f"  [BM25 INIT] Page {_page_num}: fetched {len(_batch)} docs "
                  f"(running total: {len(_all_milvus_docs)})")
            _offset += _batch_size
            if len(_batch) < _batch_size:
                break  # last page

        # Tokenize: simple whitespace split is sufficient for Vietnamese BM25.
        # BM25 works on term frequency — no need for full NLP tokenization.
        _tokenized = [doc.page_content.lower().split() for doc in _all_milvus_docs]
        bm25_index = BM25Okapi(_tokenized)
        bm25_docs  = _all_milvus_docs

        print(f"✅ BM25 index built: {len(bm25_docs)} documents indexed "
              f"({len(_tokenized)} tokenized corpus entries).")

    except ImportError:
        print("⚠️  rank_bm25 not installed — BM25 keyword retrieval disabled. "
              "Run: pip install rank-bm25>=0.2.2")
    except Exception as _bm25_err:
        print(f"⚠️  BM25 index build failed ({type(_bm25_err).__name__}: {_bm25_err}) "
              "— keyword retrieval disabled, dense-only fallback active.")

    # ── 6. Build & compile LangGraph ─────────────────────────────────────────
    graph = build_graph(
        llm=llm,
        retriever=retriever,
        milvus_client=milvus_client,
        reranker_fn=reranker_fn,
        bm25_index=bm25_index,
        bm25_docs=bm25_docs,
    )

    # ── Populate global services singleton ────────────────────────────────────
    _svc.llm          = llm
    _svc.retriever    = retriever
    _svc.milvus_client = milvus_client
    _svc.reranker_fn  = reranker_fn
    _svc.bm25_index   = bm25_index
    _svc.bm25_docs    = bm25_docs
    _svc.graph        = graph
    _svc.model_loaded = True

    print(f"✅ System Ready! Device: {DEVICE}")
    yield
    print("🛑 Shutting down...")


# ===========================================================
# FASTAPI APP
# ===========================================================
app = FastAPI(
    title="Vietnamese Legal AI Chatbot - AI Service",
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
        model_loaded=_svc.model_loaded,
    )


@app.post("/predict", response_model=PredictResponse)
async def predict_judgment(req: RequestBody):
    graph = _svc.graph
    if not graph:
        raise HTTPException(status_code=500, detail="Model not loaded")

    # ── MemorySaver: two input shapes ────────────────────────────────────────
    # Turn 1 (new case): no conversation_history → full init (all fields set,
    #   documents=[], mapped_laws=None, etc.).
    # Turn 2+ (follow-up): conversation_history exists → minimal inputs only.
    #   Fields NOT included (documents, mapped_laws, extracted_facts,
    #   full_case_content, sentencing_data, per_defendant_dates) are restored
    #   from the checkpoint saved after Turn 1.  This gives the follow-up path
    #   Turn 1's fully reranked + pinned documents without re-retrieval.
    #
    # CRITICAL: including a field in inputs OVERWRITES the checkpointed value.
    # Only pass what genuinely changes between turns.
    is_new_case = not req.conversation_history

    if is_new_case:
        # Full initialization — first message in this session
        inputs = {
            "question":            sanitize_text(req.case_content),
            "full_case_content":   sanitize_text(req.case_content),
            "messages":            [HumanMessage(content=sanitize_text(req.case_content))],
            "user_role":           req.role,
            "documents":           [],
            "retrieval_queries":   [],
            "extracted_facts":     None,
            "mapped_laws":         None,
            "sentencing_data":     None,
            "_missing_fields":     None,
            "per_defendant_dates": None,
            "chat_history":        req.conversation_history,
            "is_practice_mode":    False,
            "user_analysis":       None,
        }
        print(f"[PREDICT] New case — full init | session_id={req.session_id}")
    else:
        # Follow-up — only pass what changes; checkpoint restores the rest.
        # documents, mapped_laws, extracted_facts,
        # sentencing_data, per_defendant_dates → all from Turn 1 checkpoint.
        # full_case_content is included because if classify_intent routes to
        # new_case (e.g. user re-submits corrected case after clarification),
        # extract_facts must use the NEW text, not the stale checkpoint value.
        inputs = {
            "question":            sanitize_text(req.case_content),
            "full_case_content":   sanitize_text(req.case_content),
            "messages":            [HumanMessage(content=sanitize_text(req.case_content))],
            "user_role":           req.role,
            "_missing_fields":     None,
            "chat_history":        req.conversation_history,
            "is_practice_mode":    False,
            "user_analysis":       None,
        }
        print(f"[PREDICT] Follow-up — minimal inputs (checkpoint restores state) | session_id={req.session_id}")

    # Build LangGraph config with thread_id for checkpointing.
    # If no session_id provided, generate a unique one (single-turn, no checkpoint reuse).
    thread_id = req.session_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    try:
        output = await graph.ainvoke(inputs, config=config)
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
    Practice Mode: evaluate user's legal analysis via the full LangGraph pipeline.

    Full graph path when required fields are present:
      classify_intent → extract_facts → clarification_check → multi_query_rewrite
      → parallel_retrieve → temporal_priority_tagger → rerank → map_laws
      → application_mode_router → practice_evaluate_node → END

    If required fields (hanh_vi, ngay_pham_toi) are missing after fact extraction,
    clarification_node returns a structured JSON error payload (score=0 + feedback)
    directly to END — the endpoint parses it cleanly as a PracticeEvalResponse.

    This ensures grading is done against actual RAG-retrieved laws and the
    deterministic mapped_laws table — not just the LLM's internalized knowledge.
    """
    graph = _svc.graph
    if not graph:
        raise HTTPException(status_code=503, detail="Model not loaded — service is still starting up")

    # The case description is set as both `question` and `full_case_content` so all
    # retrieval, mapping, and grading nodes receive the full case text.
    # is_practice_mode=True signals clarification_node to return JSON (not plain text)
    # and application_mode_router to route to practice_evaluate_node instead of generate.
    case_text = sanitize_text(req.case_description)
    inputs = {
        "question":            case_text,
        "full_case_content":   case_text,
        "messages":            [HumanMessage(content=case_text)],
        "user_role":           req.user_mode,
        "user_analysis":       sanitize_text(req.user_analysis),
        "is_practice_mode":    True,
        "documents":           [],
        "retrieval_queries":   [],
        "extracted_facts":     None,
        "mapped_laws":         None,
        "sentencing_data":     None,
        "_missing_fields":     None,
        "per_defendant_dates": None,
        "chat_history":        [],
    }

    try:
        # Practice Mode: use a unique thread_id so we never restore a stale
        # checkpoint from a prior chat session.  Each practice evaluation is
        # a standalone, one-shot analysis.
        practice_config = {"configurable": {"thread_id": f"practice-{uuid.uuid4()}"}}
        output = await graph.ainvoke(inputs, practice_config)
        # practice_evaluate_node writes a JSON string as the final AIMessage
        data = _extract_json(output["messages"][-1].content)

        feedback = data.get("feedback", {})
        return PracticeEvalResponse(
            score=int(data.get("score", 50)),
            feedback=PracticeEvalFeedback(
                strengths=       feedback.get("strengths", []),
                improvements=    feedback.get("improvements", []),
                suggestion=      feedback.get("suggestion", ""),
                suggested_laws=  feedback.get("suggested_laws", []),
            ),
        )
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Practice evaluation JSON parse error: {e}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[PRACTICE EVAL ERROR] {type(e).__name__}: {e}\n{tb}")
        raise HTTPException(status_code=500, detail=f"Practice evaluation failed: {type(e).__name__}: {e}")


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
