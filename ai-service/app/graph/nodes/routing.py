"""
graph/nodes/routing.py — VNPLaw AI Service
Pure routing functions (no LLM dependency):
  - classify_intent:        START → new_case | followup | casual
  - application_mode_router: map_laws → generate | practice_evaluate

Keyword lists are also exported so other nodes (e.g. _looks_like_case
inside make_generation_nodes) can import them without creating a
circular dependency.
"""

from app.core.schemas import AgentState


# ─────────────────────────────────────────────────────────────────────────────
# KEYWORD LISTS
# ─────────────────────────────────────────────────────────────────────────────

CASUAL_PHRASES = [
    "xin chào", "chào bạn", "hi", "hello", "hey", "helo", "ola",
    "bạn là ai", "bạn là gì", "chatbot là gì", "cảm ơn", "thank",
    "ok bạn", "được rồi", "bye", "tạm biệt", "hẹn gặp",
]

LEGAL_KEYWORDS = [
    # Legal acts
    "điều", "khoản", "bộ luật", "tội", "hình phạt", "hành vi", "án",
    "phạt", "tù", "phạm tội", "phạm tội", "ngày", "năm", "tháng",
    # Actors
    "bị cáo", "bị hại", "nạn nhân", "bị can", "nghi phạm", "thủ phạm",
    "luật sư", "viện kiểm sát", "tòa án", "công an", "cảnh sát", "thẩm phán",
    # Criminal acts (Vietnamese)
    "giết", "đánh", "chém", "bắn", "cướp", "trộm", "lừa đảo", "hiếp",
    "tống tiền", "bắt cóc", "đốt", "buôn bán", "ma túy", "mua bán",
    "tàng trữ", "sản xuất", "vận chuyển", "chiếm đoạt", "xâm phạm",
    "gây thương tích", "tham nhũng", "hối lộ", "trốn thuế", "gian lận",
    # Legal process
    "tạm giam", "xét xử", "khởi tố", "điều tra", "truy tố", "kết án",
    "bắt giữ", "khám xét", "thu giữ", "tang vật", "biên bản",
    # Sentencing
    "tình tiết", "giảm nhẹ", "tăng nặng", "án treo", "cải tạo",
    "chung thân", "tử hình", "bồi thường", "tịch thu",
    # English fallback
    "law", "penal", "crime", "criminal", "offense", "sentence",
]

FOLLOWUP_PHRASES = [
    "giải thích thêm", "tại sao", "vì sao", "thế còn", "thế nếu",
    "còn điều", "điều đó có nghĩa", "bạn vừa nói", "ý bạn là",
    "phân tích thêm", "nói rõ hơn", "chi tiết hơn", "ví dụ",
    "như vậy thì", "trong trường hợp", "nếu bị cáo", "nếu nạn nhân",
    "why", "what if", "can you explain", "elaborate", "clarify",
    "you said", "earlier you", "in that case",
]


# ─────────────────────────────────────────────────────────────────────────────
# ROUTERS
# ─────────────────────────────────────────────────────────────────────────────

def classify_intent(state: AgentState) -> str:
    """
    Route messages into one of 3 paths:
      'casual'   — greeting, chit-chat, off-topic → simple canned response
      'followup' — elaboration/question about a prior AI response
      'new_case' — penal law case or legal question → full RAG pipeline

    Practice Mode always routes as 'new_case'.
    """
    # Practice Mode bypass
    if state.get("is_practice_mode"):
        print("  [INTENT] Practice Mode → new_case (bypass heuristics)")
        return "new_case"

    history  = state.get("chat_history", []) or []
    question = state["question"].strip()
    q_lower  = question.lower()

    # Layer 2a: Greeting / casual fast-path
    if any(phrase in q_lower for phrase in CASUAL_PHRASES) and len(question) < 80:
        print(f"  [INTENT] Greeting phrase detected → casual | query='{question[:60]}'")
        return "casual"

    # Layer 2b: Legal keyword detection
    has_legal = any(kw in q_lower for kw in LEGAL_KEYWORDS)

    # Layer 2c: No legal keywords and no history → casual
    if len(question) < 120 and not has_legal and not history:
        print("  [INTENT] Short + no legal keywords + no history → casual")
        return "casual"

    # Layer 2d: Long input with legal keywords → new_case
    if len(question) > 400 and has_legal:
        print(f"  [INTENT] Long ({len(question)} chars) + legal keywords → new_case")
        return "new_case"

    # Very long input regardless of keywords
    if len(question) > 600:
        print("  [INTENT] Very long input → new_case (no keyword check)")
        return "new_case"

    # Layer 2e: No history → treat as new case
    if not history:
        print("  [INTENT] No history → new_case")
        return "new_case"

    # Layer 2f: Follow-up phrase fast-path
    if any(phrase in q_lower for phrase in FOLLOWUP_PHRASES) and history:
        print(f"  [INTENT] Follow-up phrase detected → followup | query='{question[:60]}'")
        return "followup"

    # Layer 3: Deterministic fallback
    if history:
        intent = "followup"
        print("  [INTENT] No fast-path matched + has history → followup (default)")
    else:
        intent = "new_case"
        print("  [INTENT] No fast-path matched + no history → new_case (default)")

    print(f"  [INTENT] → {intent} | query='{question[:80]}'")
    return intent


def application_mode_router(state: AgentState) -> str:
    """Router: after map_laws, decide generate vs practice_evaluate."""
    if state.get("is_practice_mode"):
        print("  [ROUTER: application_mode] practice_evaluate")
        return "practice_evaluate"
    print("  [ROUTER: application_mode] generate")
    return "generate"
