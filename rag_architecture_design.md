# RAG + LangGraph Architecture Design
## Vietnamese Penal Law Chatbot — Implementation Specification

> **Status:** Source-of-truth design document. Use this to implement `ai-service/app/main.py`.

---

## 1. Stack

| Layer | Choice | Notes |
|---|---|---|
| Embedding model | `trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05` | Fine-tuned from `jinaai/jina-embeddings-v5-text-nano` via LoRA. `task="retrieval"` for both encode calls |
| Vector DB | Milvus Lite (`.db` file) | Whole-article chunks |
| Chunks | **Full law article** (VB_1999, VB_2009, VB_2017, VB_2025) | No sub-clause splitting |
| Reranker | `BAAI/bge-reranker-v2-m3` | Multilingual cross-encoder, 8192-token context — fits full Vietnamese law articles (up to 3,574 tokens). PhoRanker (`itdainb/PhoRanker`) was discarded: its 256-token RoBERTa context could only see the first ~60 chars of each article. |
| LLM | Gemini 2.5 Flash (OpenRouter) | `temperature=0` |
| Framework | LangGraph `StateGraph` + FastAPI | |

---

## 2. Graph Overview

```
START
  └─► classify_intent
          ├─► casual          → casual_respond → END
          ├─► followup        → followup_generate → END
          └─► new_case
                  └─► extract_facts
                          └─► clarification_check
                                  ├─► [MUST HAVEs missing] → clarification_node → END
                                  └─► [OK] → multi_query_rewrite  (role-biased circumstance_query)
                                                  └─► parallel_retrieve
                                                          ├─► semantic search (3 queries)
                                                          └─► pinned_fetch(role)  ← NEW: direct metadata lookup
                                                                  └─► temporal_priority_tagger
                                                                          └─► rerank
                                                                                  └─► map_laws
                                                                                          ├─► [rebuttal] → rebuttal_node → END
                                                                                          └─► [normal]   → generate → END
```

---

## 3. AgentState

```python
class AgentState(TypedDict):
    messages:            Annotated[Sequence[BaseMessage], add_messages]
    question:            str
    full_case_content:   str
    documents:           List[Document]
    retrieval_queries:   List[str]                   # 3 queries from multi_query_rewrite
    retry_count:         int
    user_role:           Literal["defense", "victim", "neutral"]
    extracted_facts:     Optional[Dict[str, Any]]
    mapped_laws:         Optional[List[Dict[str, str]]]
    rebuttal_against:    Optional[str]
    sentencing_data:     Optional[Dict[str, Any]]
    chat_history:        Optional[List[Dict[str, str]]]
    is_relevant:         Optional[bool]
    _missing_fields:     Optional[List[str]]         # set by clarification_check_node
    per_defendant_dates: Optional[List[Dict[str, str]]]  # NEW — multi-defendant support
    # per_defendant_dates structure:
    # [{"name": str, "ngay_pham_toi": "dd/mm/yyyy", "crime_edition": str (filled by tagger)}]
```

> [!NOTE]
> `per_defendant_dates` is populated by `extract_facts` when `is_multi_defendant=True`.
> `temporal_priority_tagger` uses it to compute the **union of all crime editions** needed
> across all defendants, ensuring each defendant's acts are judged by their own crime-date law.

---

## 4. Node Specifications

---

### NODE 1 — `classify_intent`
**Keep as-is.** Routes to `casual`, `followup`, or `new_case`.

Edge change: `new_case` → `extract_facts` (not `rewrite`).

Expected return values from `classify_intent(state)`: `"new_case"`, `"followup"`, or `"casual"`.

> [!NOTE]
> The `workflow.add_conditional_edges(START, classify_intent, {...})` call lives in **Section 5**.
> Do NOT add it again here — it must only appear once in the graph definition.

---

#### `classify_intent` contract (for implementors)

```python
def classify_intent(state: AgentState) -> str:
    """Router: classifies the incoming message as a new case, follow-up, or casual query.
    Reads: state['messages'] (latest user message), state.get('chat_history')
    Returns: 'new_case' | 'followup' | 'casual'
    Keep existing implementation — do not change routing logic.
    """
    ...
```

#### `casual_respond` contract (for implementors)

```python
def casual_respond(state: AgentState) -> dict:
    """Handles greetings, off-topic questions, and meta-questions about the chatbot.
    Reads: state['question']
    Returns: {"messages": [AIMessage(content=reply)]}
    Keep existing implementation.
    """
    ...
```

---

### NODE 2 — `extract_facts`

**Purpose:** Extract observable legal facts from the raw case description. The user writes what *happened* — not the legal classification (tội danh). Charges are determined later by `map_laws`.

#### Field Taxonomy

| Field | Type | Status | Behaviour when absent |
|---|---|---|---|
| `hanh_vi` | str | 🔴 **MUST HAVE** | Trigger `clarification_node` |
| `ngay_pham_toi` | date `dd/mm/yyyy` | 🔴 **MUST HAVE** | Trigger `clarification_node` |
| `ngay_xet_xu` | date `dd/mm/yyyy` | 🟠 **RECOMMENDED** (GMT+7 fallback) | LLM extracts from user input if present; system injects current GMT+7 date if absent |
| `hau_qua` | str | 🟠 RECOMMENDED | `null` — surface suggestion to user |
| `doi_tuong` | str | 🟠 RECOMMENDED | `null` |
| `tinh_tiet_tang_nang` | list[str] | 🟠 RECOMMENDED | `[]` |
| `tinh_tiet_giam_nhe` | list[str] | 🟠 RECOMMENDED | `[]` |
| `co_tien_an` | bool | 🟠 RECOMMENDED | `null` |
| `cong_cu` | str | 🟡 OPTIONAL | `null` — silent |
| `tang_vat_loai` | str | 🟡 OPTIONAL | `null` — silent |
| `tang_vat_so_luong` | str | 🟡 OPTIONAL | `null` — silent |
| `dong_co` | str | 🟡 OPTIONAL | `null` — silent |
| `ten_bi_cao` | str | 🟡 OPTIONAL | `null` — silent |
| `is_multi_defendant` | bool | 🟡 OPTIONAL | `null` — silent |
| `so_luong_bi_cao` | int | 🟡 OPTIONAL | `null` — silent |
| `da_boi_thuong` | bool | 🟡 OPTIONAL | `null` — silent |
| `da_thanh_khan_khai_bao` | bool | 🟡 OPTIONAL | `null` — silent |
| `ngay_tam_giam` | date | 🟡 OPTIONAL | `null` — silent |
| `ngay_sinh_nan_nhan` | date | 🟡 OPTIONAL | `null` — silent |
| `ngay_sinh_bi_cao` | date | 🟡 OPTIONAL | `null` — silent |

#### System Prompt

```
Bạn là chuyên gia phân tích hồ sơ pháp lý.
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
  "per_defendant_dates": [
    {"name": "tên bị cáo", "ngay_pham_toi": "dd/mm/yyyy"}
  ]
}

QUY TẮC TRÍCH XUẤT per_defendant_dates:
- Chỉ điền nếu is_multi_defendant = true VÀ mỗi bị cáo có ngày phạm tội riêng trong mô tả.
- Nếu một bị cáo không có ngày riêng → dùng ngày chung từ "ngay_pham_toi".
- Nếu chỉ có một bị cáo hoặc không xác định được → để null (không phải []).
- CHỈ trích xuất thông tin CÓ TRONG mô tả. TUYỆT ĐỐI KHÔNG bị đặt thông tin.

LƯU Ý: Trích xuất "ngay_xet_xu" nếu có trong mô tả (ví dụ: ngày tòa xét xử, ngày phiên tòa).
Nếu không tìm thấy, trả về null — hệ thống sẽ tự động dùng ngày hiện tại.
LLM không được bịa đặt thông tin không có trong mô tả.
OUTPUT: CHỈ xuất JSON hợp lệ, không markdown, không giải thích.
```

#### `ngay_xet_xu` fallback and `extract_facts_node` return (Python, after LLM call)

The LLM is asked to extract the trial/hearing date from the input.
Only if it returns `null` does the system inject the current GMT+7 date.
**Crucially, `per_defendant_dates` must be promoted from the facts dict to a top-level
state key — otherwise `parallel_retrieve` and `temporal_priority_tagger` will never find it.**

```python
from datetime import date, datetime, timezone, timedelta  # full import block required
_VN_TZ = timezone(timedelta(hours=7))

# --- ngay_xet_xu fallback ---
if not facts.get("ngay_xet_xu"):
    facts["ngay_xet_xu"] = datetime.now(_VN_TZ).strftime("%d/%m/%Y")
    print(f"  ngay_xet_xu not in input — defaulted to today (GMT+7): {facts['ngay_xet_xu']}")
else:
    print(f"  ngay_xet_xu extracted from input: {facts['ngay_xet_xu']}")

# --- Promote per_defendant_dates to top-level state ---
# The LLM embeds it inside the facts JSON. We must pop it out so
# parallel_retrieve and temporal_priority_tagger can read it via state.get("per_defendant_dates").
per_defendant = facts.pop("per_defendant_dates", None) or None
if per_defendant and not isinstance(per_defendant, list):
    per_defendant = None   # discard malformed output

# --- Node return ---
return {
    "extracted_facts":     facts,
    "per_defendant_dates": per_defendant,  # None for single-defendant cases
}
```

> [!IMPORTANT]
> The import at the top of `main.py` MUST include `from datetime import date, datetime, timezone, timedelta`.
> `date` is needed for `_MIN_SUPPORTED_DATE = date(2000, 7, 1)` in `clarification_check_node`.

---

### NODE 2.5 — `clarification_check` ← NEW

Runs immediately after `extract_facts`. Two parts: a **node** that writes to state,
and a **router function** that reads state to return the route key.

> [!IMPORTANT]
> LangGraph router functions CANNOT write to state — they must only return a string.
> The state write (`_missing_fields`) must happen in a separate node first.

```python
REQUIRED_FIELDS = {
    "hanh_vi":       "mô tả hành vi phạm tội (bị cáo đã làm gì?)",
    "ngay_pham_toi": "ngày xảy ra hành vi phạm tội (dd/mm/yyyy)",
}

# SUPPORTED date range: 2000-07-01 onward (BLHS 1999 effective date)
_MIN_SUPPORTED_DATE = date(2000, 7, 1)

def clarification_check_node(state: AgentState) -> dict:
    """Node: validates MUST HAVE fields and writes _missing_fields to state."""
    facts = state.get("extracted_facts") or {}
    missing = [f for f in REQUIRED_FIELDS if not facts.get(f)]

    # BUG E fix: reject crime dates before BLHS 1999 effective date
    if not missing:   # only check date if hanh_vi + ngay_pham_toi both present
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
    """Router function: reads _missing_fields, returns route key."""
    return "clarify" if state.get("_missing_fields") else "continue"
```

#### `clarification_node` response format

```python
# _FIELD_LABELS is not needed — clarification_node uses REQUIRED_FIELDS directly.
# "_date_out_of_range" is handled via an explicit if-check, not a label lookup.

def clarification_node(state: AgentState) -> dict:
    missing = state.get("_missing_fields", [])

    # Special case: crime date is before supported range
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
```

#### Graph edges

```python
workflow.add_edge("extract_facts",     "clarification_check")     # ← MUST NOT be missing
workflow.add_conditional_edges(
    "clarification_check",
    clarification_router,                                          # ← router fn, NOT node
    {"clarify": "clarification", "continue": "multi_query_rewrite"}
)
workflow.add_edge("clarification", END)
```

---

### NODE 3 — `multi_query_rewrite` ← NEW (replaces `rewrite_question`)

Generates **3 retrieval queries** from extracted behavioral facts.

#### Query style — must match embedding model training distribution

> [!IMPORTANT]
> The embedding model (`trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05`) was fine-tuned from `jinaai/jina-embeddings-v5-text-nano` on `question` fields from `toaan_gov_datasets.json`.
> Those questions are **Vietnamese factual prose sentences** (2–5 sentences) written in
> court verdict narrative style — *who did what to whom, with what, resulting in what harm*.
>
> Queries must NOT be keyword strings. They must be narrative sentences.
> Queries must NOT contain any information not present in the extracted facts.
> Queries must NOT include article numbers, legal conclusions, or court language.

**Training data style examples:**
```
✅ "Bị cáo lén lút đột nhập vào nhà nạn nhân trộm cắp điện thoại trị giá 7.000.000 đồng."
✅ "Bị cáo đã thành khẩn khai báo tại cơ quan điều tra và ăn năn hối cải về hành vi của mình."
❌ "dùng dao đe dọa chiếm đoạt tài sản điều 168"       ← keyword style, wrong
❌ "Tòa án áp dụng Điều 51 vì bị cáo thành khẩn"      ← legal conclusion, wrong
```

#### Role bias for `circumstance_query` (narrative instruction, not keywords)

| Role | `circumstance_query` instruction |
|---|---|
| `neutral` | Describe both mitigating AND aggravating facts neutrally |
| `defense` | Describe ONLY mitigating facts (confession, clean record, compensation paid, young age) |
| `victim` | Describe ONLY aggravating facts (injury severity, weapon use, organised group, prior record) |

```python
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

def multi_query_rewrite(state: AgentState) -> dict:
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

QUY TẮC BẮT BUỘC — ĐỌC KỸ TRƯỚC KHI VIẾT:
1. CHỈ sử dụng thông tin có trong "NỘI DUNG VỤ ÁN" hoặc "SỰ KIỆN ĐÃ TRÍCH XUẤT" ở trên.
2. TUYỆT ĐỐI KHÔNG thêm thông tin, suy luận, hoặc bịa đặt bất kỳ chi tiết nào.
3. KHÔNG được viết tên điều luật, số điều khoản (ví dụ "Điều 168", "Điều 51").
4. KHÔNG dùng ngôn ngữ tòa án như "Tòa án áp dụng", "căn cứ vào", "bị truy tố về tội".
5. Viết bằng tiếng Việt, văn phong bản án thực tế (ngôi thứ ba, quá khứ, mô tả sự kiện).
6. Mỗi câu dài 2–5 câu. Nếu không có thông tin cho một truy vấn → trả về null.

YÊU CẦU:
- behavior_query: Mô tả hành vi phạm tội cụ thể — bị cáo đã làm gì, với ai,
  bằng phương tiện gì, gây hậu quả gì. (KHÁCH QUAN — không thiên vị vai trò)
- circumstance_query: {circumstance_instruction}
- evidence_query: Mô tả tang vật, công cụ phạm tội, số lượng, trọng lượng,
  giá trị tài sản cụ thể có trong vụ án. Nếu không có tang vật → null.

TRẢ VỀ JSON (null nếu không có thông tin):
{{
  "behavior_query": "...",
  "circumstance_query": "...",
  "evidence_query": "..."
}}
OUTPUT: CHỈ JSON hợp lệ, không markdown, không giải thích."""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = re.sub(r"```json?\s*", "", response.content.strip()).strip("`").strip()
        queries = json.loads(raw)
        q_list = [
            q for q in [
                queries.get("behavior_query"),
                queries.get("circumstance_query"),
                queries.get("evidence_query"),
            ] if q  # drop null queries
        ]
        if not q_list:
            raise ValueError("All queries null")
    except Exception:
        # Fallback: construct 2 narrative queries from facts directly (no LLM)
        hanh_vi = facts.get("hanh_vi", "")
        hau_qua = facts.get("hau_qua", "")
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
        print(f"    Q{i+1}: {q[:100]}...")
    return {"retrieval_queries": q_list}
```



---

### NODE 4 — `parallel_retrieve` ← NEW (replaces single `retrieve`)

Two-step retrieval:
1. **Semantic search** — fires all 3 queries against Milvus, merges, deduplicates
2. **Pinned fetch** — directly fetches role-critical procedural articles by metadata filter
   (zero extra embedding or LLM calls — Milvus metadata query only)

#### Why pinned fetch?
Semantic search reliably misses procedural articles because they share no vocabulary with
the case description. A robbery case description won't contain "án treo" or "dưới mức thấp nhất",
so Điều 65 (suspended sentence) would never surface for defense — even though it's always relevant.

#### Pinned articles per role — edition-aware

> [!IMPORTANT]
> Article numbers differ between BLHS 1999-era and BLHS 2015-era. The pinned fetch
> must resolve the correct article number for `crime_edition` before querying Milvus.

**Cross-edition mapping (verified from actual VB_1999/2009/2017/2025 JSON files):**

| Purpose | BLHS 1999 | BLHS 1999 (sửa đổi 2009) | BLHS 2015 (sửa đổi 2017) | BLHS 2015 (sửa đổi 2025) |
|---|---|---|---|---|
| Retroactivity rule | Điều 7 | Điều 7 | Điều 7 | Điều 7 |
| Mitigating factors | **Điều 46** | **Điều 46** | **Điều 51** | **Điều 51** |
| Aggravating factors | **Điều 48** | **Điều 48** | **Điều 52** | **Điều 52** |
| Recidivism | **Điều 49** | **Điều 49** | **Điều 53** | **Điều 53** |
| Penalty below minimum | **Điều 47** | **Điều 47** | **Điều 54** | **Điều 54** |
| Attempt/preparation reduction | **Điều 52** | **Điều 52** | **Điều 57** | **Điều 57** |
| Penalty consolidation | **Điều 50** | **Điều 50** | **Điều 55** | **Điều 55** |
| Suspended sentence | **Điều 60** | **Điều 60** | **Điều 65** | **Điều 65** |
| Civil compensation | **Điều 42** | **Điều 42** | **Điều 48** | **Điều 48** |
| Penalty types | **Điều 28** | **Điều 28** | **Điều 32** | **Điều 32** |

```python
# Maps (purpose, edition_source) → article_number
# Source values must match the 'source' field in Milvus metadata exactly
_PINNED_MAP = {
    # ── BLHS 1999 ─────────────────────────────────────────────────────────
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
    # ── BLHS 1999 (sửa đổi 2009) — same numbers as 1999 base ─────────────
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
    # ── BLHS 2015 (sửa đổi 2017) — numbers shift significantly ───────────
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
    # ── BLHS 2015 (sửa đổi 2025) — same numbers as 2017 ─────────────────
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

# Purposes to fetch per role
_PINNED_PURPOSES = {
    "neutral": ["retroactive", "mitigating", "aggravating", "consolidate"],
    "defense": ["retroactive", "mitigating", "below_min", "attempt", "suspended"],
    "victim":  ["retroactive", "aggravating", "recidivism", "civil_comp", "penalty_types"],
}
```

| Role | Purposes pinned | 2015-era articles | 1999-era articles |
|---|---|---|---|
| All | retroactive | Điều 7 | Điều 7 |
| `neutral` | mitigating, aggravating, consolidate | 51, 52, 55 | 46, 48, 50 |
| `defense` | mitigating, below_min, attempt, suspended | 51, 54, 57, 65 | 46, 47, 52, 60 |
| `victim` | aggravating, recidivism, civil_comp, penalty_types | 52, 53, 48, 32 | 48, 49, 42, 28 |


```python
def parallel_retrieve(state: AgentState) -> dict:
    queries       = state.get("retrieval_queries") or [state["question"]]
    role          = state.get("user_role", "neutral")
    facts         = state.get("extracted_facts") or {}
    per_defendant = state.get("per_defendant_dates") or []
    seen_ids      = set()
    all_docs      = []

    # ── Step 1: Semantic search (3 queries) ──────────────────────────────
    for q in queries:
        if not q:
            continue
        docs = retriever.invoke(q)   # existing MilvusRetriever
        for d in docs:
            key = (d.metadata.get("article_number", ""), d.metadata.get("source", ""))
            if key not in seen_ids:
                seen_ids.add(key)
                all_docs.append(d)

    # ── Step 2: Pinned fetch — edition-aware, multi-defendant safe ────────
    # Collect ALL crime editions across defendants so every edition gets pinned
    if per_defendant:
        crime_editions = [
            _edition_for_date(d.get("ngay_pham_toi", ""))
            for d in per_defendant
        ]
        crime_editions = [e for e in crime_editions if e]   # drop None
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
                        page_content=h.get("content", ""),
                        metadata={k: h.get(k, "") for k in _OUTPUT_FIELDS if k != "content"},
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
```

---

### NODE 5 — `temporal_priority_tagger` ← REPLACES `temporal_filter`

> [!IMPORTANT]
> **Do NOT hard-filter to a single BLHS edition.**
> Under Article 7 BLHS, the court must compare editions when a newer law exists:
> - Use the **crime-date edition** by default (baseline rule)
> - If a **newer edition is more lenient** for a specific charge, apply that instead
> - Both editions may be cited in the same verdict (different charges, different defendants)
>
> The system must retrieve articles from **all relevant editions** and let `map_laws` + `generate`
> decide which edition applies per charge based on the leniency comparison.

#### Logic

```
crime_date  →  determines "primary" edition (baseline)
now (trial) →  determines "latest" edition (may be more lenient)

If primary == latest → only one edition needed
If primary != latest → retrieve BOTH; tag each doc with priority
```

#### Implementation

```python
# Edition effective-date ranges — source names MUST match 'source' field in Milvus exactly
_EDITION_RANGES = [
    ("BLHS 1999",                  date(2000, 7, 1),  date(2010, 1, 1)),
    ("BLHS 1999 (sửa đổi 2009)",  date(2010, 1, 1),  date(2018, 1, 1)),
    ("BLHS 2015 (sửa đổi 2017)",  date(2018, 1, 1),  date(2025, 7, 1)),
    ("BLHS 2015 (sửa đổi 2025)",  date(2025, 7, 1),  date(9999, 1, 1)),
]

# Edition-aware always-keep sets — article numbers differ between 1999-era and 2015-era
# A single flat set would keep wrong articles (e.g. Điều 46 in 2015 is NOT mitigating factors)
_ALWAYS_KEEP_BY_EDITION = {
    "BLHS 1999":                  {"7", "46", "47", "48", "49", "50", "51", "52", "60"},
    "BLHS 1999 (sửa đổi 2009)": {"7", "46", "47", "48", "49", "50", "51", "52", "60"},
    "BLHS 2015 (sửa đổi 2017)": {"7", "51", "52", "53", "54", "55", "56", "57", "65"},
    "BLHS 2015 (sửa đổi 2025)": {"7", "51", "52", "53", "54", "55", "56", "57", "65"},
}

def _edition_for_date(date_str: str) -> Optional[str]:
    if not isinstance(date_str, str) or not date_str.strip():
        return None   # guard: None / int / empty string from LLM output
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(date_str.strip(), fmt).date()
            for name, start, end in _EDITION_RANGES:
                if start <= d < end:
                    return name
        except (ValueError, AttributeError, TypeError):
            continue
    return None

def temporal_priority_tagger(state: AgentState) -> dict:
    docs  = state.get("documents", [])
    facts = state.get("extracted_facts") or {}
    trial_edition = _edition_for_date(facts.get("ngay_xet_xu", ""))

    # ── Multi-defendant: collect all crime editions across defendants ───────
    per_defendant = state.get("per_defendant_dates") or []
    if per_defendant:
        # Each defendant may have acted under a different BLHS edition.
        # Build a NEW list (do not mutate state dicts in-place — LangGraph immutability).
        updated_per_defendant = []
        for d_info in per_defendant:
            edition = _edition_for_date(d_info.get("ngay_pham_toi", "")) or ""
            updated_per_defendant.append({**d_info, "crime_edition": edition})
        crime_editions = {d["crime_edition"] for d in updated_per_defendant if d["crime_edition"]}
        per_defendant = updated_per_defendant   # use the updated copy downstream
        print(f"  [TEMPORAL] Multi-defendant mode: editions={crime_editions}")
    else:
        # Single-defendant
        single = _edition_for_date(facts.get("ngay_pham_toi", ""))
        crime_editions = {single} if single else set()

    if not crime_editions:
        print("  [TEMPORAL] Cannot determine any crime edition — passing all docs")
        return {"documents": docs}

    needs_comparison = any(e != trial_edition for e in crime_editions)
    print(f"  [TEMPORAL] Crime editions: {crime_editions} | Trial: {trial_edition}")
    print(f"  [TEMPORAL] Retroactivity comparison needed: {needs_comparison}")

    tagged = []   # docs from a crime-date edition (primary for ≥1 defendant)
    newer  = []   # docs from newer editions (for leniency comparison)
    always = []   # sentencing-adjustment articles (always include)

    for d in docs:
        art_no     = str(d.metadata.get("article_number", ""))
        src        = d.metadata.get("source", "")
        always_keep = _ALWAYS_KEEP_BY_EDITION.get(src, set())

        if art_no in always_keep:
            d.metadata["_temporal_role"] = "adjustment"
            always.append(d)
        elif src in crime_editions:
            # Primary for at least one defendant — tag which ones
            d.metadata["_temporal_role"] = "primary"
            d.metadata["_primary_for"] = [
                di["name"] for di in per_defendant
                if di.get("crime_edition") == src
            ] or ["all"]
            tagged.append(d)
        elif needs_comparison:
            d.metadata["_temporal_role"] = "comparison"
            newer.append(d)
        # else: unrelated edition in a single-edition case — discard

    ordered = tagged + newer + always
    result  = ordered if ordered else docs
    print(f"  [TEMPORAL] primary={len(tagged)}, comparison={len(newer)}, adjustment={len(always)}")
    return {"documents": result, "per_defendant_dates": per_defendant}
```

#### What `_temporal_role` means downstream

| Value | Meaning |
|---|---|
| `"primary"` | Article from the crime-date edition — apply by default |
| `"comparison"` | Article from a newer edition — apply only if more lenient |
| `"adjustment"` | Điều 51/52/65 etc. — always relevant regardless of edition |


### NODE 6 — `rerank` ← NEW (replaces `grade_documents`)

Uses a cross-encoder to re-score all retrieved articles in one batch.
Loads **once at server startup** (not per-request).

#### Startup (inside `lifespan`)

```python
from sentence_transformers import CrossEncoder
cross_encoder = CrossEncoder(
    "BAAI/bge-reranker-v2-m3",
    max_length=8192,   # fits full Vietnamese law articles (up to ~3,574 tokens)
)
cross_encoder.tokenizer.model_max_length = 8192
if DEVICE == "cuda":
    cross_encoder.model = cross_encoder.model.half()  # fp16 on GPU
```

#### Node

```python
# Module-level constant: top-N semantic docs kept after reranking.
# Pinned docs are ALWAYS kept on top of this limit.
# Total max context = _MAX_SEMANTIC_DOCS + number of pinned articles.
_MAX_SEMANTIC_DOCS = 5

def rerank_node(state: AgentState) -> dict:
    docs = state.get("documents", [])
    if not docs:
        return {"documents": [], "is_relevant": False}  # nothing retrieved — signal downstream

    # Use behavior_query (first retrieval query) as the cross-encoder query.
    retrieval_queries = state.get("retrieval_queries") or []
    query = (
        retrieval_queries[0]
        if retrieval_queries
        else (state.get("full_case_content") or state["question"])
    )

    # Split docs: pinned articles must survive reranking regardless of score.
    # Only semantic docs compete for the top-N slots.
    # _MAX_SEMANTIC_DOCS is defined at module level above — do NOT redefine here.
    pinned_docs   = [d for d in docs if d.metadata.get("_pinned")]
    semantic_docs = [d for d in docs if not d.metadata.get("_pinned")]

    # bge-reranker-v2-m3: raw logits, higher = more relevant. No sigmoid needed for ranking.
    # Full article text is passed — no truncation needed (8192-token context).
    if semantic_docs:
        _q = query[:512]   # cap query to ~130 tokens, leaving room for the full article
        pairs  = [(_q, d.page_content) for d in semantic_docs]
        scores = cross_encoder.predict(pairs)
        ranked_semantic = sorted(zip(scores, semantic_docs), key=lambda x: x[0], reverse=True)
        top_semantic    = [doc for _, doc in ranked_semantic[:_MAX_SEMANTIC_DOCS]]
    else:
        ranked_semantic = []
        top_semantic    = []

    # Final list: top semantic first, then all pinned (guaranteed inclusion)
    top_docs = top_semantic + pinned_docs

    if not top_docs:
        # Edge case: both semantic and pinned empty (e.g. empty Milvus collection)
        return {"documents": [], "is_relevant": False}

    print(f"  [RERANK] query[:80]: {query[:80]!r}")
    print(f"  [RERANK] {len(docs)} → {len(top_docs)} docs "
          f"(semantic_kept={len(top_semantic)}/{len(semantic_docs)}, pinned={len(pinned_docs)})")
    for score, doc in ranked_semantic[:_MAX_SEMANTIC_DOCS]:
        art  = doc.metadata.get("article_number", "?")
        src  = doc.metadata.get("source", "?")
        role = doc.metadata.get("_temporal_role", "?")
        print(f"    [sem] score={score:.4f}  Điều {art} | {src} | {role}")
    for doc in pinned_docs:
        art     = doc.metadata.get("article_number", "?")
        src     = doc.metadata.get("source", "?")
        purpose = doc.metadata.get("_purpose", "?")
        print(f"    [pin] Điều {art} | {src} | purpose={purpose}")

    return {"documents": top_docs, "is_relevant": True}
```

> **Removes `grade_documents` entirely.** No more 15 serial LLM calls per request.

---

### NODE 7 — `map_laws` (fix truncation)

**Current bugs fixed:**
- `documents[:5]` → use all reranked docs
- `case_text[:1000]` → no truncation (Gemini 2.5 Flash supports 1M tokens)

```python
def map_laws_node(state: AgentState) -> dict:
    facts      = state.get("extracted_facts") or {}
    documents  = state.get("documents") or []        # safe .get() — rerank may return []
    case_text  = state.get("full_case_content") or state.get("question", "")  # no truncation

    # Short-circuit: no docs to map against
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


RETROACTIVITY_RULE = """
NGUYÊN TẮC THỜI HIỆU (Điều 7 BLHS) — BẮT BUỘC ÁP DỤNG:
1. QUY TẮC CƠ BẢN: Áp dụng luật có hiệu lực tại THỜI ĐIỂM PHẠM TỘI (tài liệu có role=primary).
2. NGOẠI LỆ HỒI TỐ CÓ LỢI: Nếu luật MỚI HƠN (role=comparison) quy định hình phạt NHẸ HƠN
   cho hành vi đó (giảm khung, bỏ tội danh, thêm tình tiết giảm nhẹ), thì BẮT BUỘC áp dụng
   luật mới đó thay thế.
3. NGHIÊM CẤM hồi tố nếu luật mới NẶNG HƠN (tăng khung, thêm aggravating) — giữ luật cũ.
4. ĐA TỘI DANH: So sánh từng tội danh riêng biệt — tội này có thể dùng luật 2017,
   tội khác dùng luật 2025 nếu có lợi hơn.
5. ĐA BỊ CÁO: Mỗi bị cáo xét theo ngày họ thực hiện hành vi, không phải ngày xét xử.

    "article": "Điều 168",
    "clause": "Khoản 2",
    "offense_name": "Tội cướp tài sản",
    "applicable_reason": "Lý do áp dụng điều này",
    "edition_applied": "BLHS 2015 (sửa đổi 2017)",
    "edition_reason": "Áp dụng luật tại thời điểm phạm tội (2022). Luật 2025 không có lợi hơn."
  }
]
OUTPUT: CHỈ JSON array hợp lệ."""

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"SỰ KIỆN:\n{facts_str}\n\nVĂN BẢN LUẬT (có nhãn role):\n{context}\n\nVỤ ÁN:\n{case_text}")
        ])
        raw    = re.sub(r"```json?\s*", "", response.content.strip()).strip("`").strip()
        mapped = json.loads(raw)
        if not isinstance(mapped, list) or len(mapped) == 0:
            raise ValueError("Empty or non-list mapped_laws")
    except Exception as e:
        print(f"⚠️  Law mapping failed: {e}")
        # Explicit fallback — generate node checks _mapping_error and surfaces warning to user
        mapped = [{
            "article": "N/A",
            "clause": "N/A",
            "offense_name": "Không xác định được",
            "applicable_reason": "Hệ thống không thể ánh xạ điều luật từ các tài liệu đã trích xuất.",
            "edition_applied": "N/A",
            "edition_reason": "Lỗi phân tích pháp luật.",
            "_mapping_error": True,
        }]

    return {"mapped_laws": mapped}
```

---

### NODE 8 — `generate` (retroactivity-aware response)

Reads `mapped_laws` and `documents` (reranked, tagged with `_temporal_role`).

**Every prompt template must include:**

```python
RETROACTIVITY_RULE = """
NGUYÊN TẮC THỜI HIỆU BẮT BUỘC (Điều 7 BLHS):
- Luật áp dụng = luật có hiệu lực TẠI THỜI ĐIỂM PHẠM TỘI (role=primary).
- NẾU luật mới hơn (role=comparison) NHẸ HƠN → bắt buộc áp dụng.
- NẾU luật mới NẶNG HƠN → CẤM áp dụng hồi tố.
- Mỗi tội danh so sánh độc lập.
Cuối phản hồi LUÔN thêm bảng:
**ĐIỀU KHOẢN ÁP DỤNG:**
| Điều | Tội danh | Nguồn áp dụng | Lý do chọn nguồn |
|------|----------|---------------|------------------|
"""

context_text = "\n\n".join([
    f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','?')} | "
    f"role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
    for d in state["documents"]
])
chat_history = (state.get("chat_history") or [])[-6:]

mapped = state.get("mapped_laws") or []
if any(m.get("_mapping_error") for m in mapped):
    preamble = (
        "⚠️ Hệ thống không thể ánh xạ điều luật chính xác. "
        "Phân tích dưới đây dựa trên văn bản luật đã truy xuất.\n\n"
    )
else:
    preamble = ""
```

---

### NODE 9 — `followup_generate`

**Purpose:** Answer follow-up questions reusing `documents` + `mapped_laws` from the previous `new_case` turn. No re-retrieval triggered.

```python
def followup_generate(state: AgentState) -> dict:
    documents    = state.get("documents") or []
    mapped_laws  = state.get("mapped_laws") or []
    chat_history = (state.get("chat_history") or [])[-6:]
    question     = state["question"]
    role         = state.get("user_role", "neutral")

    context_text = "\n\n".join([
        f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','?')} | "
        f"role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
        for d in documents
    ])
    history_messages = [
        HumanMessage(content=m["content"]) if m["role"] == "user"
        else AIMessage(content=m["content"])
        for m in chat_history
    ]
    response = llm.invoke([
        SystemMessage(content=(
            f"Bạn là chuyên gia luật hình sự Việt Nam, góc độ: {role}.\n"
            "Dựa vào văn bản luật và kết quả ánh xạ đã có, trả lời câu hỏi tiếp theo.\n"
            "Giữ nguyên quy tắc hiệu lực luật (Điều 7 BLHS) từ lượt phân tích trước."
        )),
        *history_messages,
        HumanMessage(content=(
            f"CÂU HỎI: {question}\n\n"
            f"VĂN BẢN LUẬT (cache):\n{context_text}\n\n"
            f"KẾT QUẢ ÁNH XẠ (cache):\n{json.dumps(mapped_laws, ensure_ascii=False)}"
        ))
    ])
    return {"messages": [AIMessage(content=response.content)]}
```

> [!NOTE]
> If the user raises a completely new case in a follow-up, `classify_intent` should route
> to `new_case` — `followup_generate` does not re-run retrieval.

---

### NODE 10 — `rebuttal_node` (Study Mode)

**Purpose:** Study mode — user picks a perspective on a mock case and writes their own legal
argument. This node grades and critiques the submission against ground-truth `mapped_laws`.

**Trigger:** `rebuttal_against` in state is non-null (set by frontend on study-mode submit).
`rebuttal_against` is NOT extracted by `extract_facts` — frontend pre-populates it.

```python
def check_rebuttal(state: AgentState) -> str:
    """Router after map_laws: grade study submission or generate normally."""
    return "rebuttal" if state.get("rebuttal_against") else "generate"

def rebuttal_node(state: AgentState) -> dict:
    role          = state.get("user_role", "neutral")
    mapped_laws   = state.get("mapped_laws") or []
    documents     = state.get("documents") or []
    user_argument = state["rebuttal_against"]

    context_text = "\n\n".join([
        f"[Điều {d.metadata.get('article_number','?')} - {d.metadata.get('source','?')} | "
        f"role={d.metadata.get('_temporal_role','unknown')}]\n{d.page_content}"
        for d in documents
    ])
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

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=(
            f"LẬP LUẬN NGƯỜI DÙNG:\n{user_argument}\n\n"
            f"KẾT QUẢ CHUẨN:\n{json.dumps(mapped_laws, ensure_ascii=False)}\n\n"
            f"VĂN BẢN LUẬT:\n{context_text}"
        ))
    ])
    return {"messages": [AIMessage(content=response.content)]}
```

> [!NOTE]
> `rebuttal_against` is pre-populated by the frontend when the user submits a study answer.
> It is never extracted by `extract_facts`.

---

## 5. Complete Graph Edge Definition

```python
workflow.add_node("extract_facts",             extract_facts_node)
workflow.add_node("clarification_check",       clarification_check_node)
workflow.add_node("clarification",             clarification_node)
workflow.add_node("multi_query_rewrite",       multi_query_rewrite)
workflow.add_node("parallel_retrieve",         parallel_retrieve)
workflow.add_node("temporal_priority_tagger",  temporal_priority_tagger)
workflow.add_node("rerank",                    rerank_node)
workflow.add_node("map_laws",                  map_laws_node)
workflow.add_node("generate",                  generate)
workflow.add_node("rebuttal",                  rebuttal_node)
workflow.add_node("followup",                  followup_generate)
workflow.add_node("casual",                    casual_respond)

workflow.add_conditional_edges(START, classify_intent, {
    "new_case": "extract_facts",
    "followup": "followup",
    "casual":   "casual",
})
workflow.add_edge("extract_facts",            "clarification_check")   # MUST NOT be missing
workflow.add_conditional_edges(
    "clarification_check",
    clarification_router,                                               # router fn, NOT node
    {"clarify": "clarification", "continue": "multi_query_rewrite"}
)
workflow.add_edge("clarification",             END)
workflow.add_edge("multi_query_rewrite",       "parallel_retrieve")
workflow.add_edge("parallel_retrieve",         "temporal_priority_tagger")
workflow.add_edge("temporal_priority_tagger",  "rerank")
workflow.add_edge("rerank",                    "map_laws")
workflow.add_conditional_edges(
    "map_laws",
    check_rebuttal,                                                     # defined in NODE 10
    {"rebuttal": "rebuttal", "generate": "generate"}
)
workflow.add_edge("generate",   END)
workflow.add_edge("rebuttal",   END)
workflow.add_edge("followup",   END)
workflow.add_edge("casual",     END)
```

---

## 6. New Dependencies (`requirements.txt`)

```
sentence-transformers>=2.2.0    # already present for Jina embeddings
FlagEmbedding                   # required by bge-reranker-v2-m3
```

Load in `lifespan` startup:
```python
from sentence_transformers import CrossEncoder
cross_encoder = CrossEncoder(
    "BAAI/bge-reranker-v2-m3",
    max_length=8192,
)
cross_encoder.tokenizer.model_max_length = 8192
```

---

## 7. Implementation Priority

| Priority | Node / Change | Impact |
|---|---|---|
| 🔴 P0 | `multi_query_rewrite` (3 behavior-based queries) | +15–25% retrieval recall |
| 🔴 P0 | `temporal_priority_tagger` by crime date | Eliminates wrong-edition citations |
| 🔴 P0 | `ngay_xet_xu` auto-inject GMT+7 | Already implemented in `main.py` |
| 🟠 P1 | `rerank` (cross-encoder, replaces `grade_documents`) | -50% latency, better precision |
| 🟠 P1 | `clarification_check` + `clarification_node` | Better UX for incomplete inputs |
| 🟠 P1 | Fix `map_laws` truncation | Correct mapping for long cases |
| 🟡 P2 | Structured citation table in `generate` | Frontend citation linking |
| 🟡 P2 | Route `rebuttal` through `map_laws` | Grounded counter-arguments |
| 🟢 P3 | Streaming (`ainvoke` → `astream_events`) | UX improvement |

---

## 8. Key Design Constraints

> [!IMPORTANT]
> `extract_facts` must **never** extract `charges` or `tội danh`. The user describes facts.
> Legal classification happens only in `map_laws` after retrieval.

> [!IMPORTANT]
> `ngay_xet_xu` is extracted by the LLM from user input when present.
> If absent (null), the system defaults to current GMT+7 date.
> It is **never blindly overwritten** — the LLM-extracted value takes priority.

> [!NOTE]
> MUST HAVE fields are only `hanh_vi` and `ngay_pham_toi`.
> All other fields are RECOMMENDED or OPTIONAL — the pipeline continues with `null` values.

> [!NOTE]
> Cross-encoder is loaded **once at startup** in `lifespan`. Never instantiate it inside a node.

> [!WARNING]
> Do not remove `LoRABGEM3Embeddings` class from `main.py` — keep it for backward compatibility.
> Only the instantiation has changed to `JinaEmbeddings`.
