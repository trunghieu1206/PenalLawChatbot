# Refactoring `ai-service/app/main.py` — Final Implementation Plan (v5, Final)

> **Status:** Approved ✅ | **Strategy:** Services Container Pattern
> **Audit:** 7 issues corrected in v2 + 4 corrected in v3 + 5 corrected in v4 + 3 corrected in v5

---

## 1. Current State & Problem

The AI microservice relies on a single monolithic `main.py` file that is nearly **3,300 lines long**. It contains:

- FastAPI app setup and API routes (`/predict`, `/health`, `/practice/evaluate`)
- The `lifespan` context manager (model initialization)
- External service connections (Milvus, Gemini LLM via OpenRouter, Jina Embeddings, BGE Reranker, BM25)
- All Pydantic request/response schemas and `AgentState` TypedDict
- Over a dozen LangGraph nodes, routers, and a timing decorator — **all nested as closures inside `lifespan`**
- All large Vietnamese system prompt strings embedded within node functions
- All utility functions (date parsing, text sanitization, legal logic)

---

## 2. Corrected Final Architecture

### File Structure

```text
ai-service/app/
├── main.py                        # ≈80 lines: FastAPI app, lifespan, routes
│                                  #   Uses app.state (NOT app_state global)
│
├── core/
│   ├── config.py                  # ALL constants and lookup tables:
│   │                              #   MILVUS_URI, COLLECTION_NAME, TOP_K, LLM_MODEL,
│   │                              #   _DEFAULT_EMBEDDING (default HF model name),
│   │                              #   DEVICE (computed ONCE here via _detect_device),
│   │                              #   OUTPUT_FIELDS (was local inside lifespan — move here),
│   │                              #   EDITION_RANGES, ALWAYS_KEEP_BY_EDITION,
│   │                              #   PINNED_MAP, PINNED_PURPOSES, ROLE_CIRCUMSTANCE_INSTRUCTION,
│   │                              #   MAX_SEMANTIC_DOCS, REQUIRED_FIELDS, MIN_SUPPORTED_DATE,
│   │                              #   _VN_TZ (Vietnam timezone — used by extract_facts_node)
│   │                              #   _ARTICLE_CITE_PAT (compiled regex — shared by _verify_* fns)
│   └── schemas.py                 # AgentState TypedDict + all Pydantic models
│                                  #   (RequestBody, PredictResponse, HealthResponse,
│                                  #    PracticeEvalRequest, PracticeEvalFeedback, PracticeEvalResponse)
│
├── services/
│   ├── container.py               # Services dataclass (see §3 below)
│   ├── embeddings.py              # JinaEmbeddings class (imports DEVICE from core.config)
│   │                              #   _detect_device() lives in core/config.py, NOT here
│   ├── vector_store.py            # _MilvusRetriever + build_bm25_index()
│   ├── reranker.py                # load_reranker() + rerank_scores(model, tokenizer, device, pairs)
│   └── llm_client.py             # init_llm() → ChatOpenAI
│
├── graph/
│   ├── builder.py                 # build_workflow(svc) → compiled LangGraph
│   │                              #   Wraps each node with measure_time()
│   │                              #   MemorySaver created ONCE here
│   ├── prompts.py                 # All Vietnamese system prompt strings (constants)
│   ├── routers.py                 # Stateless router functions (NO Services dependency):
│   │                              #   classify_intent, clarification_router,
│   │                              #   application_mode_router
│   └── nodes/
│       ├── extraction.py          # make_extract_facts_node(svc)            ← factory (needs llm)
│       │                          # clarification_check_node(state)          ← plain fn (no svc)
│       │                          # clarification_node(state)                ← plain fn (no svc)
│       ├── retrieval.py           # make_multi_query_rewrite_node(svc)       ← factory (needs llm)
│       │                          # make_parallel_retrieve_node(svc)         ← factory (needs retriever+milvus+bm25)
│       │                          # temporal_priority_tagger(state)          ← plain fn (no svc)
│       │                          # make_rerank_node(svc)                    ← factory (needs reranker+device)
│       ├── law_mapping.py         # make_map_laws_node(svc)                  ← factory (needs llm)
│       └── generation.py         # make_generate_node(svc)                  ← factory (needs llm)
│                                  # make_answer_verify_node(svc)             ← factory (needs llm)
│                                  # make_practice_evaluate_node(svc)         ← factory (needs llm)
│                                  # make_followup_generate_node(svc)         ← factory (needs llm)
│                                  # casual_respond(state)                    ← plain fn (no svc)
│
└── utils/
    ├── timing.py                  # measure_time(name) decorator
    ├── text.py                    # sanitize_text, cleanup_response, _sanitize_msgs
    ├── dates.py                   # parse_date, compute_detention_months,
    │                              #   compute_age_at_crime,
    │                              #   _edition_for_date ← IMPORTANT: lives here (not legal.py)
    │                              #   because parallel_retrieve + temporal_priority_tagger
    │                              #   both call it; dates.py has no legal-logic dependency
    └── legal.py                   # extract_sentencing_data, _extract_json,
                                   # _verify_no_hallucinated_articles,
                                   # _verify_temporal_validity, _verify_role_signal
```

---

## 3. The `Services` Dataclass (Corrected)

The v1 plan was missing `milvus_client`, `collection_name`, `output_fields`, and `device`.
These are all required by `parallel_retrieve` (pinned-article fetching calls `milvus_client.query()` directly)
and by `rerank_node` (which needs `device` for tensor placement).

```python
# services/container.py
from dataclasses import dataclass
from typing import Any, List, Optional

@dataclass
class Services:
    # LLM
    llm:                  Any            # ChatOpenAI (OpenRouter)
    # Semantic vector retrieval
    retriever:            Any            # _MilvusRetriever instance
    # Raw Milvus access for pinned-article queries (parallel_retrieve uses this directly)
    milvus_client:        Any            # pymilvus.MilvusClient
    collection_name:      str            # COLLECTION_NAME env var
    output_fields:        List[str]      # ["content", "article_number", ...]
    # Cross-encoder reranker
    reranker_model:       Any            # AutoModelForSequenceClassification
    reranker_tokenizer:   Any            # AutoTokenizer
    device:               str            # "cpu" | "cuda:0" etc.
    # BM25 keyword index
    bm25_index:           Optional[Any]  # BM25Okapi or None (disabled if not installed)
    bm25_docs:            List           # List[Document] corpus for BM25 scoring
```

---

## 4. Key Design Corrections (from Audit)

### Correction A — `DEVICE` computed once in `core/config.py` (self-contained, no circular import)
`_detect_device()` must live **directly inside `core/config.py`** — not in `services/embeddings.py`.
If it were defined in `embeddings.py` and imported by `config.py`, and `embeddings.py` also
imports constants from `config.py`, Python would detect a **circular import at startup and crash**.

`_detect_device()` only uses `os` and `torch` — no service-layer imports — so placing it in
`core/config.py` is clean and self-contained:
```python
# core/config.py — _detect_device() defined HERE, not imported from services/
import os, torch

def _detect_device() -> str:
    if os.getenv("FORCE_CPU", "0") == "1":
        return "cpu"
    if not torch.cuda.is_available():
        return "cpu"
    gpu_count = torch.cuda.device_count()
    if gpu_count == 0:
        return "cpu"
    gpu_idx = os.getpid() % gpu_count
    try:
        probe = torch.zeros(1, device=f"cuda:{gpu_idx}")
        _ = probe + 1
        del probe
        return f"cuda:{gpu_idx}"
    except Exception:
        return "cpu"

DEVICE = _detect_device()   # computed ONCE at module load
```
`services/embeddings.py` then simply imports `DEVICE` from `core.config` — no circular dependency.

### Correction B — `rerank_scores` receives model objects as parameters
The function is NOT a node — it is a helper called inside `make_rerank_node`. It must
receive the model objects explicitly (cannot capture from an outer scope in its new location):
```python
# services/reranker.py
def rerank_scores(model, tokenizer, device: str, pairs: list, batch_size: int = 8) -> list:
    all_scores = []
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        with torch.no_grad():
            enc = tokenizer([p[0] for p in batch], [p[1] for p in batch],
                            padding=True, truncation=True,
                            max_length=1024, return_tensors="pt").to(device)
            logits = model(**enc).logits.view(-1).float()
        all_scores.extend(logits.cpu().tolist())
    return all_scores
```

### Correction C — Routers are NOT nodes; they need no factory wrapper
`classify_intent`, `clarification_router`, and `application_mode_router` are passed
to `workflow.add_conditional_edges()` as plain callables. They are stateless (no `svc`
dependency). They go to `graph/routers.py` WITHOUT the `make_*(svc)` pattern:
```python
# graph/routers.py
from app.core.schemas import AgentState

def classify_intent(state: AgentState) -> str: ...
def clarification_router(state: AgentState) -> str: ...
def application_mode_router(state: AgentState) -> str: ...
```

### Correction D — Static lookup tables stay in `core/config.py`, NOT `utils/legal.py`
`utils/legal.py` contains only **functions**. Constants (`PINNED_MAP`, `EDITION_RANGES`,
`ALWAYS_KEEP_BY_EDITION`, `_ARTICLE_CITE_PAT`, etc.) belong in `core/config.py`:

| File | Contains |
|---|---|
| `core/config.py` | All dicts, sets, tuples, scalar constants, `_ARTICLE_CITE_PAT` regex |
| `utils/legal.py` | `extract_sentencing_data`, `_extract_json`, `_verify_no_hallucinated_articles`, `_verify_temporal_validity`, `_verify_role_signal` |
| `utils/dates.py` | `parse_date`, `compute_detention_months`, `compute_age_at_crime`, **`_edition_for_date`** |
| `utils/text.py` | `sanitize_text`, `cleanup_response`, `_sanitize_msgs` |
| `utils/timing.py` | `measure_time` decorator |

### Correction E — Replace `app_state` dict with FastAPI `app.state`

> **Note:** Route handlers must add `request: Request` as a parameter (from `fastapi import Request`).
> `DEVICE` is still accessed as a **module-level constant** imported from `core.config` — it does NOT
> go through `app.state`. Only the compiled graph and `model_loaded` flag go into `app.state`.

```python
# main.py imports needed
from fastapi import FastAPI, HTTPException, Request
from app.core.config import DEVICE

# BEFORE (anti-pattern: module-level mutable global)
app_state: Dict[str, Any] = {}
app_state["graph"] = compiled
app_state["model_loaded"] = True

# AFTER (FastAPI idiomatic)
app.state.graph = build_workflow(svc)   # inside lifespan
app.state.model_loaded = True

# /health — DEVICE read from module-level import, model_loaded from app.state
@app.get("/health")
async def health_check(request: Request):
    return HealthResponse(
        status="ok",
        device=DEVICE,                           # module-level constant, not app.state
        model_loaded=request.app.state.model_loaded
    )

@app.post("/predict")
async def predict_judgment(req: RequestBody, request: Request):
    graph = request.app.state.graph              # compiled graph from app.state
```

---

## 5. MemorySaver Safety Guarantee

| | Before refactor | After refactor |
|---|---|---|
| `MemorySaver` created | Inside `lifespan` | Inside `build_workflow()`, called from `lifespan` |
| Times created per server start | **1** | **1** ✅ |
| Stored in | `app_state["graph"]` | `app.state.graph` ✅ |
| Follow-up `thread_id` lookup | Finds correct checkpoint | Finds correct checkpoint ✅ |
| `MemorySaver` lifetime | Tied to FastAPI worker process | Tied to FastAPI worker process ✅ |

The refactoring is a **pure code organization change**. No execution order, memory model,
or `MemorySaver` checkpoint behaviour changes.

---

## 6. Naming Convention: Plain Functions vs. Factory Functions

**Rule:** The `make_*` prefix **exclusively** signals "this node requires dependency injection via `svc`".
Stateless nodes are **plain functions** — no factory wrapper, registered directly in `builder.py`.
This makes the distinction unambiguous for any developer reading `builder.py`.

```python
# graph/builder.py — the convention is immediately visible at registration time
workflow.add_node("clarification_check", clarification_check_node)   # plain fn
workflow.add_node("extract_facts",       make_extract_facts_node(svc)) # factory
workflow.add_node("rerank",              make_rerank_node(svc))        # factory
workflow.add_node("casual_respond",      casual_respond)               # plain fn
```

| Node | Pattern | Reason |
|---|---|---|
| `clarification_check_node` | Plain fn | Reads state, checks REQUIRED_FIELDS constant |
| `clarification_node` | Plain fn | Builds hardcoded string reply |
| `temporal_priority_tagger` | Plain fn | Pure computation on doc metadata |
| `casual_respond` | Plain fn | Returns hardcoded Vietnamese string |
| `classify_intent` | Plain fn (router) | Keyword heuristics only — passed to `add_conditional_edges` |
| `clarification_router` | Plain fn (router) | Reads `_missing_fields` flag |
| `application_mode_router` | Plain fn (router) | Reads `is_practice_mode` flag |
| `extract_facts_node` | `make_*(svc)` | Calls `svc.llm` |
| `multi_query_rewrite` | `make_*(svc)` | Calls `svc.llm` |
| `parallel_retrieve` | `make_*(svc)` | Calls `svc.retriever`, `svc.milvus_client`, `svc.bm25_*` |
| `rerank_node` | `make_*(svc)` | Calls `rerank_scores(svc.reranker_model, ...)` |
| `map_laws_node` | `make_*(svc)` | Calls `svc.llm` |
| `generate` | `make_*(svc)` | Calls `svc.llm` |
| `answer_verify` | `make_*(svc)` | Calls `svc.llm`; also imports `_ARTICLE_CITE_PAT` from `core.config` and `_edition_for_date` from `utils.dates` |
| `practice_evaluate_node` | `make_*(svc)` | Calls `svc.llm`; also uses `svc.bm25_docs` + `svc.retriever` for `suggested_laws` doc scan |
| `followup_generate` | `make_*(svc)` | Calls `svc.llm`, `svc.retriever`, `svc.bm25_index`, `svc.bm25_docs` (fallback retrieval path) |

---

## 7. Exact Graph Edge Wiring (for `builder.py` verification)

This is the **exact** edge structure from `main.py` (lines 3040–3074) that `builder.py` must replicate:

```python
# Nodes
workflow.add_node("extract_facts",           make_extract_facts_node(svc))
workflow.add_node("clarification_check",      clarification_check_node)       # plain fn
workflow.add_node("clarification",            clarification_node)              # plain fn
workflow.add_node("multi_query_rewrite",      make_multi_query_rewrite_node(svc))
workflow.add_node("parallel_retrieve",        make_parallel_retrieve_node(svc))
workflow.add_node("temporal_priority_tagger", temporal_priority_tagger)        # plain fn
workflow.add_node("rerank",                   make_rerank_node(svc))
workflow.add_node("map_laws",                 make_map_laws_node(svc))
workflow.add_node("generate",                 make_generate_node(svc))
workflow.add_node("answer_verify",            make_answer_verify_node(svc))
workflow.add_node("practice_evaluate",        make_practice_evaluate_node(svc))
workflow.add_node("followup",                 make_followup_generate_node(svc))
workflow.add_node("casual",                   casual_respond)                  # plain fn

# Edges
workflow.add_conditional_edges(START, classify_intent,
    {"new_case": "extract_facts", "followup": "followup", "casual": "casual"})
workflow.add_edge("extract_facts",            "clarification_check")
workflow.add_conditional_edges("clarification_check", clarification_router,
    {"clarify": "clarification", "continue": "multi_query_rewrite"})
workflow.add_edge("clarification",            END)
workflow.add_edge("multi_query_rewrite",      "parallel_retrieve")
workflow.add_edge("parallel_retrieve",        "temporal_priority_tagger")
workflow.add_edge("temporal_priority_tagger", "rerank")
workflow.add_edge("rerank",                   "map_laws")
workflow.add_conditional_edges("map_laws", application_mode_router,
    {"practice_evaluate": "practice_evaluate", "generate": "generate"})
workflow.add_edge("generate",                 "answer_verify")
workflow.add_edge("answer_verify",            END)
workflow.add_edge("practice_evaluate",        END)
workflow.add_edge("followup",                 END)
workflow.add_edge("casual",                   END)

# Compile with MemorySaver
checkpointer = MemorySaver()
return workflow.compile(checkpointer=checkpointer)
```

> **Critical:** `measure_time()` is applied as a wrapper at registration time in `builder.py`,
> NOT as a decorator `@` inside the node module. Example:
> ```python
> from app.utils.timing import measure_time
> workflow.add_node("extract_facts", measure_time("extract_facts")(make_extract_facts_node(svc)))
> workflow.add_node("clarification_check", measure_time("clarification_check")(clarification_check_node))
> ```

---

## 8. v4 Audit Corrections (5 new findings)

### Correction F — `_DEFAULT_EMBEDDING` must move to `core/config.py`
In `main.py` line 152, `_DEFAULT_EMBEDDING` is a module-level constant holding the
default HF fine-tuned adapter name. It is read inside `lifespan` when computing `_jina_model`.
It must be defined in `core/config.py` and imported from there into the refactored `lifespan`.

### Correction G — `_edition_for_date` belongs in `utils/dates.py`, NOT `utils/legal.py`
In `main.py`, `_edition_for_date` (lines 405–416) sits with the date-parsing utility block
(`parse_date`, `compute_detention_months`, `compute_age_at_crime`). It has **zero** legal-logic
dependency — it only parses a date string and does a range lookup against `_EDITION_RANGES`
(a constant from `core/config.py`). Placing it in `utils/legal.py` would create a confusing
cross-import. It belongs in `utils/dates.py`:

```python
# utils/dates.py
from app.core.config import EDITION_RANGES

def _edition_for_date(date_str: str) -> Optional[str]: ...
```

### Correction H — `_ARTICLE_CITE_PAT` is a module-level compiled regex (not a constant)
In `main.py` lines 566–571, `_ARTICLE_CITE_PAT = re.compile(...)` is defined at module level.
This compiled regex is shared by `_verify_no_hallucinated_articles` and `_verify_temporal_validity`.
It must be defined in `core/config.py` (alongside the other module-level constants) and imported
by `utils/legal.py`:

```python
# core/config.py
import re
_ARTICLE_CITE_PAT = re.compile(
    r"(?:[Ðđ]i[ềêẻẽẹ]u|[Dd]ieu)\s+(\d+)",
    re.IGNORECASE,
)
```

### Correction I — `load_reranker()` must preserve dtype logic from `main.py`
In `main.py` lines 933–940, the reranker is loaded with:
```python
torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32
```
Note: the check is `DEVICE == "cuda"` not `DEVICE.startswith("cuda")`. This is intentional —
for named CUDA devices like `"cuda:0"` the check evaluates to `False` and falls back to fp32.
**This behaviour must be preserved exactly** in `services/reranker.py → load_reranker()`:
```python
def load_reranker(device: str) -> tuple:
    model_name = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,  # exact original logic
    )
    model.eval()
    model.to(device)
    return model, tokenizer
```

### Correction J — `/predict` two-input-shape logic must survive `main.py` rewrite
The refactored `main.py` `/predict` handler must preserve the **two-input-shape** pattern
exactly (lines 3140–3178): `is_new_case = not req.conversation_history`.
- **New case (Turn 1):** full state init — `documents=[]`, `mapped_laws=None`, etc.
- **Follow-up (Turn 2+):** minimal inputs only — checkpointed fields NOT re-sent to avoid
  overwriting the Turn 1 checkpoint (`documents`, `mapped_laws`, `extracted_facts`,
  `sentencing_data`, `per_defendant_dates` are intentionally omitted).

Additionally, `app_state["llm"]` (line 3086) is stored in the original so `/practice/evaluate`
could access the LLM directly. After refactor, the LLM is available via `svc.llm` inside the
compiled graph — **do NOT add `app.state.llm`**. The practice endpoint uses `graph.ainvoke()`
which already has the LLM bound inside the nodes via `svc`.

---

## 8b. v5 Audit Corrections (3 new findings from final read-through)

### Correction K — `answer_verify` must import `_ARTICLE_CITE_PAT` and `_edition_for_date`
In `main.py` line 2278, `answer_verify` calls `_ARTICLE_CITE_PAT.findall(ai_text)` directly,
and at line 2307 calls `_edition_for_date(crime_date)` for the human-readable edition name in
the warning block. In the refactored `generation.py`, `make_answer_verify_node(svc)` must add
these two imports at the top of the file:
```python
# graph/nodes/generation.py
from app.core.config import _ARTICLE_CITE_PAT
from app.utils.dates import _edition_for_date
```
Both are **module-level** — no `svc` dependency needed for either.

### Correction L — `followup_generate` fallback path uses `retriever`, `_bm25_index`, `_bm25_docs`
In `main.py` lines 2931–2964, when the MemorySaver checkpoint is empty (cold start / no
session_id), `followup_generate` falls back to:
1. `retriever.invoke(original_case)` — semantic search
2. `_bm25_index.get_scores(tokenized_q)` + `_bm25_docs[idx]` — BM25 keyword search

These are closure variables from `lifespan`. In `make_followup_generate_node(svc)`, the factory
must capture them via the `Services` object:
```python
def make_followup_generate_node(svc: Services):
    retriever  = svc.retriever
    bm25_index = svc.bm25_index   # may be None
    bm25_docs  = svc.bm25_docs
    llm        = svc.llm

    def followup_generate(state: AgentState) -> dict:
        ...
        if _bm25_index is not None and bm25_docs:  # Note: use local names
            ...
```
This is consistent with how `make_parallel_retrieve_node(svc)` already captures these fields.

### Correction M — Timing decorator names must match original `@measure_time(...)` strings
In `main.py`, each node is decorated at definition time with a **named string**, e.g.:
- `@measure_time('multi_query_rewrite')` (not `'multi_query_rewrite_node'`)
- `@measure_time('casual_respond')` (not `'casual'`)
- `@measure_time('followup_generate')` (not `'followup'`)

In the refactor, timing is applied at `builder.py` registration time. The timing string must
**match the original decorator name** so log output is unchanged:
```python
# graph/builder.py — timing strings must match original @measure_time('...') names
workflow.add_node("multi_query_rewrite",
    measure_time("multi_query_rewrite")(make_multi_query_rewrite_node(svc)))  # NOT 'multi_query_rewrite_node'
workflow.add_node("casual",
    measure_time("casual_respond")(casual_respond))                           # NOT 'casual'
workflow.add_node("followup",
    measure_time("followup_generate")(make_followup_generate_node(svc)))      # NOT 'followup'
```

Complete timing name lookup table (from original `@measure_time(...)` decorators):

| `workflow.add_node(key, ...)` | `measure_time(name)` string |
|---|---|
| `"extract_facts"` | `"extract_facts"` |
| `"clarification_check"` | `"clarification_check"` |
| `"clarification"` | `"clarification"` |
| `"multi_query_rewrite"` | `"multi_query_rewrite"` |
| `"parallel_retrieve"` | `"parallel_retrieve"` |
| `"temporal_priority_tagger"` | `"temporal_priority_tagger"` |
| `"rerank"` | `"rerank"` |
| `"map_laws"` | `"map_laws"` |
| `"generate"` | `"generate"` |
| `"answer_verify"` | `"answer_verify"` |
| `"practice_evaluate"` | `"practice_evaluate"` |
| `"followup"` | **`"followup_generate"`** |
| `"casual"` | **`"casual_respond"`** |

---

## 9. Migration Sequence (Lowest Risk First)

| Step | Action | Risk |
|---|---|---|
| 1 | Create `core/config.py` — define `_detect_device()` + `DEVICE` here; move ALL constants: `_OUTPUT_FIELDS`, `_VN_TZ`, `_DEFAULT_EMBEDDING`, `_ARTICLE_CITE_PAT`, all lookup tables | Very low |
| 2 | Create `core/schemas.py` — move `AgentState` TypedDict + all Pydantic models (5 classes) | Low |
| 3 | Create `utils/timing.py` — move `measure_time` decorator | Low |
| 4 | Create `utils/dates.py` — `parse_date`, `compute_detention_months`, `compute_age_at_crime`, **`_edition_for_date`** | Low |
| 5 | Create `utils/text.py` — `sanitize_text`, `cleanup_response`, `_sanitize_msgs` | Low |
| 6 | Create `utils/legal.py` — `extract_sentencing_data`, `_extract_json`, `_verify_no_hallucinated_articles`, `_verify_temporal_validity`, `_verify_role_signal` (imports `_ARTICLE_CITE_PAT` from `core.config`) | Low |
| 7 | Create `services/container.py` — full corrected `Services` dataclass | Low |
| 8 | Create `services/embeddings.py` — `JinaEmbeddings` only; imports `DEVICE` from `core.config` | Low |
| 9 | Create `services/reranker.py` — `load_reranker(device)` with exact fp16/fp32 dtype logic + parameterized `rerank_scores(model, tokenizer, device, pairs)` | Low |
| 10 | Create `services/vector_store.py` — `_MilvusRetriever` class + `build_bm25_index()`; imports `OUTPUT_FIELDS`, `COLLECTION_NAME`, `TOP_K` from `core.config` | Low |
| 11 | Create `services/llm_client.py` — `init_llm()` → `ChatOpenAI` | Low |
| 12 | Create `graph/prompts.py` — extract all Vietnamese system prompt strings as constants | Low |
| 13 | Create `graph/routers.py` — plain stateless router functions (no `svc`): `classify_intent`, `clarification_router`, `application_mode_router` | Low |
| 14 | Create `graph/nodes/extraction.py` — factory `make_extract_facts_node(svc)` + plain `clarification_check_node`, `clarification_node` | Medium |
| 15 | Create `graph/nodes/retrieval.py` — factories for rewrite/retrieve/rerank + plain `temporal_priority_tagger` | Medium |
| 16 | Create `graph/nodes/law_mapping.py` — `make_map_laws_node(svc)` | Medium |
| 17 | Create `graph/nodes/generation.py` — factories for generate/verify/practice/followup + plain `casual_respond` | Medium |
| 18 | Create `graph/builder.py` — register all nodes and edges exactly per §7; wrap ALL nodes with `measure_time()`; compile with `MemorySaver` | Medium |
| 19 | Rewrite `main.py` — lifespan builds `Services` + calls `build_workflow(svc)` → `app.state.graph`; preserve two-input-shape `/predict` logic; routes add `request: Request` param; HF login + logging filter stay here; do NOT add `app.state.llm` | Medium |
| 20 | Smoke test (see §10) | Verification |

---

## 10. Verification Plan

After all steps:

1. ✅ `GET /health` → `{"status": "ok", "model_loaded": true, "device": "cpu|cuda:X"}`
2. ✅ `POST /predict` (new case, no `session_id`) → full pipeline runs all 13 nodes, returns `mapped_laws` + `sentencing_data`
3. ✅ `POST /predict` (follow-up, same `session_id`) → `MemorySaver` restores Turn 1 state; `documents` NOT re-retrieved; `followup` node runs
4. ✅ `POST /practice/evaluate` → runs full pipeline with `is_practice_mode=True`; returns `{"score": N, "feedback": {...}}`
5. ✅ `git diff --stat` → zero logic changes; only file moves and import updates
6. ✅ Server logs show **original timing strings** for all 13 nodes:
   `extract_facts`, `clarification_check`, `clarification`, `multi_query_rewrite`,
   `parallel_retrieve`, `temporal_priority_tagger`, `rerank`, `map_laws`, `generate`,
   `answer_verify`, `practice_evaluate`, **`followup_generate`**, **`casual_respond``**
   (Note: `followup_generate` ≠ `followup`; `casual_respond` ≠ `casual`)
7. ✅ `_edition_for_date` is importable from `app.utils.dates` — NOT from `app.utils.legal`
8. ✅ Reranker loads with `fp32` on `cuda:0` (not `fp16`) — matches original `device == "cuda"` check
9. ✅ `answer_verify` imports `_ARTICLE_CITE_PAT` from `app.core.config` and `_edition_for_date` from `app.utils.dates`
10. ✅ `followup_generate` fallback path resolves `retriever`, `bm25_index`, `bm25_docs` from `svc.*` (not closure globals)
