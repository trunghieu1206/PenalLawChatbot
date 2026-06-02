# Answer Verification Node — Final Implementation Plan

## Summary of Confirmed Decisions

| Decision | Choice |
|----------|--------|
| Scope | `generate` + `rebuttal` only (not `practice_evaluate`) |
| Failure behavior | **Option B** — Append warning block, never delete original answer |
| Verification | **Layer 1 (deterministic) + Layer 2 (cost-optimized LLM Judge)** |
| Function name | `answer_verify` (inside `lifespan` closure) |

---

## Architecture: Two-Layer Hybrid

```
answer_verify
  ├── Layer 1: Pure Python (always runs, $0)
  │     ├── L1-A: Hallucination     — cited articles not in context
  │     ├── L1-B: Temporal Validity — wrong BLHS edition cited
  │     └── L1-C: Role Signal       — keyword direction mismatch
  │
  └── Layer 2: LLM Judge (runs only if L1 found < 2 issues, ~$0.0005)
        ├── Q1: Factual Consistency — did AI invent facts not in extracted_facts?
        └── Q2: Role Adherence      — is tone/direction consistent with assigned role?
```

### Cost Optimization Strategy for Layer 2

The key to keeping Layer 2 cheap is **aggressively trimming the input**:

| Optimization | Effect |
|---|---|
| Truncate AI response to **first 1,500 chars** | Cuts input by ~60% (most hallucinations occur in first half) |
| Send only **4 key fact fields**, not full JSON | Saves ~200 tokens |
| Ask only **2 yes/no questions** with short answers | Output capped at 80 tokens |
| **Skip Layer 2 if L1 already found ≥ 2 issues** | Avoids redundant LLM call when major problems are obvious |
| Use `max_tokens=150` on the LLM call | Hard caps output cost |

**Estimated cost per request:** `~1,200 tokens input × $0.30/1M + 80 tokens output × $2.50/1M` = **~$0.00056**

---

## Helper Functions (Module-Level, after `_MAX_SEMANTIC_DOCS`)

### Three Pure-Python Helpers (Layer 1)

```python
# ===========================================================
# ANSWER VERIFICATION — deterministic helpers ($0 API cost)
# ===========================================================
_ARTICLE_CITE_PAT = re.compile(
    r"(?:[Ðđ]i[ềê]u|dieu)\s+(\d+)", re.IGNORECASE
)

def _verify_no_hallucinated_articles(
    text: str,
    mapped_laws: List[Dict],
    documents: List[Document],
) -> List[str]:
    """L1-A: Return article numbers cited in text but absent from retrieved context."""
    cited = set(_ARTICLE_CITE_PAT.findall(text))
    allowed = {str(m.get("article", "")) for m in mapped_laws}
    allowed |= {str(d.metadata.get("article_number", "")) for d in documents}
    allowed |= {"7"}           # Điều 7 retroactivity — always valid
    allowed.discard("")
    return sorted(cited - allowed)


def _verify_temporal_validity(
    text: str,
    crime_date: str,
    documents: List[Document],
) -> List[str]:
    """L1-B: Return 'Điều X (WrongEdition)' where the wrong BLHS edition was cited."""
    correct_edition = _edition_for_date(crime_date)
    if not correct_edition:
        return []
    cited_arts = set(_ARTICLE_CITE_PAT.findall(text))
    wrong = []
    for d in documents:
        src = d.metadata.get("source", "")
        art = str(d.metadata.get("article_number", ""))
        if src and src != correct_edition and art in cited_arts:
            wrong.append(f"Điều {art} ({src})")
    return wrong


def _verify_role_signal(text: str, role: str) -> float:
    """
    L1-C: Keyword direction score for the assigned role. Returns 0.0–1.0.
    Below 0.35 = likely role drift.
    Reuses _SENT_DIR (already defined as a module-level constant).
    """
    t = text.lower()
    role_cfg = _SENT_DIR.get(role, {})
    toward  = role_cfg.get("toward", [])
    against = role_cfg.get("against", [])
    pos = sum(1 for k in toward  if k in t)
    neg = sum(1 for k in against if k in t)
    score = (pos / max(len(toward), 1)) - 0.5 * (neg / max(len(against), 1))
    return round(max(0.0, min(1.0, score)), 4)
```

### One Cost-Optimized LLM Helper (Layer 2)

```python
def _llm_judge_verify(
    ai_text: str,
    facts: Dict,
    role: str,
    llm,                        # reuses the same llm object from app_state
) -> Dict[str, Optional[str]]:
    """
    L2: Lightweight LLM judge. Two yes/no questions only. Zero extra model load.
    Returns {"factual_issue": str|None, "role_issue": str|None}
    
    COST OPTIMIZATION:
    - Input truncated to 1,500 chars (most issues appear in the first part)
    - Only 4 key fact fields sent (not the full JSON)
    - Output max_tokens=150 (hard cap)
    - Structured JSON output forces minimal response
    """
    # --- Trim inputs aggressively ---
    response_snippet = ai_text[:1500]           # ~375 tokens
    key_facts = {
        k: facts.get(k)
        for k in ["hanh_vi", "hau_qua", "co_tien_an", "tinh_tiet_tang_nang",
                  "tinh_tiet_giam_nhe", "ngay_pham_toi"]
        if facts.get(k) is not None
    }

    role_map = {
        "defense": "Luật sư bào chữa — phải bảo vệ bị cáo, xin giảm nhẹ.",
        "victim":  "Luật sư bị hại — phải đòi xử nghiêm, bồi thường tối đa.",
        "neutral": "Thẩm phán — phải trung lập, phân tích hai chiều.",
    }

    prompt = f"""Bạn là kiểm tra viên pháp lý. Đánh giá đoạn phân tích AI dưới đây.

SỰ KIỆN THỰC TẾ (nguồn đúng duy nhất):
{json.dumps(key_facts, ensure_ascii=False)}

VAI TRÒ YÊU CẦU: {role_map.get(role, role)}

ĐOẠN PHÂN TÍCH AI (đã rút gọn):
{response_snippet}

Trả lời 2 câu hỏi sau bằng JSON:
{{
  "factual_ok": true/false,
  "factual_issue": "mô tả ngắn nếu false, null nếu true",
  "role_ok": true/false,
  "role_issue": "mô tả ngắn nếu false, null nếu true"
}}

QUY TẮC:
- factual_ok = false CHỈ KHI AI bịa ra tình tiết KHÔNG có trong SỰ KIỆN THỰC TẾ.
- role_ok = false CHỈ KHI AI rõ ràng lập luận SAI chiều với vai trò được giao.
- Nếu không chắc → true (tránh false positive).
OUTPUT: Chỉ JSON, không markdown."""

    try:
        # Use a local llm call with explicit max_tokens cap for cost control
        response = llm.invoke(
            _sanitize_msgs([HumanMessage(content=prompt)]),
            max_tokens=150,
        )
        raw = re.sub(r"```(?:json)?\s*", "", response.content.strip()).strip()
        verdict = json.loads(raw)
        return {
            "factual_issue": None if verdict.get("factual_ok", True)
                             else verdict.get("factual_issue"),
            "role_issue":    None if verdict.get("role_ok", True)
                             else verdict.get("role_issue"),
        }
    except Exception as e:
        print(f"  [answer_verify] L2 judge failed ({type(e).__name__}): {e} — skipping.")
        return {"factual_issue": None, "role_issue": None}  # Fail open, never crash
```

---

## Main Node: `answer_verify` (inside `lifespan` closure)

```python
def answer_verify(state: AgentState) -> dict:
    """
    Final answer verification — 2-layer hybrid, cost-optimized.

    Layer 1: Pure Python (always runs, $0)
      L1-A Hallucination  — article numbers cited but not in context
      L1-B Temporal       — wrong BLHS edition cited for crime date
      L1-C Role signal    — keyword direction mismatch

    Layer 2: LLM Judge (skipped if L1 already found ≥ 2 issues)
      Q1 Factual consistency — AI invented facts not in extracted_facts?
      Q2 Role adherence      — tone/argument consistent with assigned role?

    Appends a ⚠️ warning block if issues found. Silent pass-through if clean.
    """
    print("[NODE: answer_verify]")
    messages = state.get("messages") or []
    last_msg = messages[-1] if messages else None
    if not last_msg or not isinstance(last_msg, AIMessage):
        return {}

    ai_text     = last_msg.content
    role        = state.get("user_role", "neutral")
    facts       = state.get("extracted_facts") or {}
    mapped_laws = state.get("mapped_laws") or []
    documents   = state.get("documents") or []
    crime_date  = facts.get("ngay_pham_toi", "")

    issues: List[str] = []

    # ══════════════════════════════════════════════════
    # LAYER 1 — Deterministic ($0 cost)
    # ══════════════════════════════════════════════════

    # L1-A: Hallucination
    hallucinated = _verify_no_hallucinated_articles(ai_text, mapped_laws, documents)
    if hallucinated:
        arts = ", ".join(f"Điều {a}" for a in hallucinated)
        issues.append(
            f"Trích dẫn không có cơ sở: {arts} "
            f"— không có trong văn bản luật đã truy xuất."
        )

    # L1-B: Temporal Validity
    wrong_edition = _verify_temporal_validity(ai_text, crime_date, documents)
    if wrong_edition:
        correct = _edition_for_date(crime_date) or "không xác định"
        issues.append(
            f"Có thể áp dụng sai phiên bản luật: {', '.join(wrong_edition)}. "
            f"Ngày phạm tội {crime_date} → phải dùng {correct}."
        )

    # L1-C: Role Signal
    role_score = _verify_role_signal(ai_text, role)
    if role_score < 0.35:
        issues.append(
            f"Giọng văn có thể chưa nhất quán với vai '{role}' "
            f"(điểm tín hiệu = {role_score:.2f}/1.00)."
        )

    # ══════════════════════════════════════════════════
    # LAYER 2 — LLM Judge (skipped if L1 already clear)
    # Skip condition: L1 already found ≥ 2 issues (major problems obvious)
    # ══════════════════════════════════════════════════
    if len(issues) < 2:
        verdict = _llm_judge_verify(ai_text, facts, role, llm)
        if verdict.get("factual_issue"):
            issues.append(f"Nhất quán dữ liệu thực tế: {verdict['factual_issue']}")
        if verdict.get("role_issue"):
            issues.append(f"Nhập vai (LLM): {verdict['role_issue']}")
    else:
        print("  [answer_verify] L2 skipped — L1 already found ≥ 2 issues.")

    # ══════════════════════════════════════════════════
    # RESULT
    # ══════════════════════════════════════════════════
    if not issues:
        print("  ✅ Answer verification passed — no issues detected.")
        return {}   # Silent pass-through — nothing appended to response

    warning_block = (
        "\n\n---\n"
        "**🔍 Ghi chú hệ thống (Kiểm chứng câu trả lời):**\n"
        + "\n".join(f"- ⚠️ {i}" for i in issues)
        + "\n\n*(Vui lòng đối chiếu với văn bản luật gốc để xác nhận.)*"
    )
    print(f"  ⚠️ Answer verification: {len(issues)} issue(s) detected.")
    return {"messages": [AIMessage(content=ai_text + warning_block)]}
```

---

## Graph Changes

### Register node
```python
workflow.add_node("answer_verify", answer_verify)
```

### Rewire edges
```python
# Remove:
workflow.add_edge("generate", END)
workflow.add_edge("rebuttal", END)

# Add:
workflow.add_edge("generate",      "answer_verify")
workflow.add_edge("rebuttal",      "answer_verify")
workflow.add_edge("answer_verify", END)
```

---

## Updated Graph Flow

```
map_laws
  └→ [application_mode_router]
        ├→ generate        ──┐
        ├→ rebuttal        ──┴→ answer_verify ──→ END
        └→ practice_evaluate ──────────────────→ END  (untouched)
```

---

## Cost Summary

| Component | Cost |
|-----------|------|
| Layer 1 (3 checks) | **$0.00** always |
| Layer 2 (LLM Judge) | **~$0.00056/request** |
| L2 when L1 already caught ≥2 issues | **$0.00** (skipped) |
| **Expected average** | **~$0.0004/request** |

---

## Verification Test Cases

| # | Scenario | L1 | L2 | Expected Output |
|---|----------|----|----|----------------|
| TC-1 | Valid case, clean response | ✅ Pass | ✅ Pass | No warning |
| TC-2 | Response cites Điều 999 | ❌ Fail (L1-A) | Skipped if also role issue | Warning: hallucination |
| TC-3 | Crime 2017, cites BLHS 2015 | ❌ Fail (L1-B) | Runs | Warning: temporal |
| TC-4 | Defense role uses "xử nghiêm" | ❌ Fail (L1-C) | Runs | Warning: role signal |
| TC-5 | AI invents victim was a child | ✅ Pass L1 | ❌ Fail Q1 | Warning: factual |
| TC-6 | 3 L1 issues at once | ❌ Fail ×3 | Skipped | Warning: 3 issues, L2 saved |
