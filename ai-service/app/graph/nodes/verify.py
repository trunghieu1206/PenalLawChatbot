"""
graph/verify.py — VNPLaw AI Service
Answer-verification helpers (L1 deterministic checks).
These are module-level pure functions — no LLM calls, no side effects.
"""


import re
from typing import List, Dict, Any

from langchain_core.documents import Document

from app.utils.dates import _edition_for_date


# Regex: matches "Đi\u1ec1u 51", "đi\u1ec1u 51", "Dieu 51" etc.
_ARTICLE_CITE_PAT = re.compile(
    r"(?:[\u00d0\u0111]i[\u1ec1\u00ea\u1ebb\u1ebd\u1e99]u|[Dd]ieu)\s+(\d+)",
    re.IGNORECASE,
)


def _verify_no_hallucinated_articles(
    text: str,
    mapped_laws: List[Dict[str, Any]],
    documents: List[Document],
) -> List[str]:
    """L1-A: Return article numbers cited in the AI's text but absent
    from the retrieved context.

    KEY DESIGN DECISION: We scan the full text to ensure any cited article
    must be grounded in the retrieved documents. mapped_laws is intentionally
    excluded from the allowed set — if map_laws cited an article not in the
    retrieved context (using parametric knowledge), it should be flagged here.
    """
    cited = set(_ARTICLE_CITE_PAT.findall(text))
    allowed: set = {str(d.metadata.get("article_number", "")) for d in documents}
    allowed |= {"7"}  # \u0110i\u1ec1u 7 retroactivity — always valid
    allowed.discard("")
    return sorted(cited - allowed)


def _verify_temporal_validity(
    text: str,
    crime_date: str,
    documents: List[Document],
) -> List[str]:
    """L1-B: Return 'Điều X (WrongEdition)' where wrong BLHS edition is cited.

    KEY DESIGN DECISION:
    We SKIP documents whose _temporal_role is 'comparison' or 'adjustment'
    because those are intentionally fetched for retroactivity analysis or
    general sentencing mechanics — citing them is legal.
    """
    valid_editions: set = set()
    ed = _edition_for_date(crime_date)
    if ed:
        valid_editions.add(ed)
    if not valid_editions:
        return []

    cited_arts = set(_ARTICLE_CITE_PAT.findall(text))
    wrong: List[str] = []
    for d in documents:
        role = d.metadata.get("_temporal_role", "")
        if role in ("comparison", "adjustment"):
            continue
        src = d.metadata.get("source", "")
        art = str(d.metadata.get("article_number", ""))
        if src and src not in valid_editions and art in cited_arts:
            wrong.append(f"\u0110i\u1ec1u {art} ({src})")
    return wrong


def _verify_role_signal(text: str, role: str) -> float:
    """
    L1-C: Keyword direction score for the assigned role. Returns 0.0\u20131.0.
    Below 0.35 = likely role drift.
    Self-contained vocab \u2014 no external dependency.
    """
    _ROLE_SIGNAL_VOCAB = {
        "defense": {
            "toward":  ["\u00e1n treo", "c\u1ea3i t\u1ea1o kh\u00f4ng giam gi\u1eef", "d\u01b0\u1edbi m\u1ee9c th\u1ea5p nh\u1ea5t",
                        "\u0111\u1ec1 ngh\u1ecb gi\u1ea3m", "xin gi\u1ea3m nh\u1eb9", "m\u1ee9c \u00e1n th\u1ea5p nh\u1ea5t", "khoan h\u1ed3ng",
                        "th\u00e0nh kh\u1ea9n", "t\u00ecnh ti\u1ebft gi\u1ea3m nh\u1eb9", "th\u00e2n ch\u1ee7", "b\u00e0o ch\u1eefa",
                        "\u0103n n\u0103n", "l\u1ea7n \u0111\u1ea7u ph\u1ea1m t\u1ed9i", "nh\u00e2n th\u00e2n t\u1ed1t"],
            "against": ["m\u1ee9c \u00e1n cao nh\u1ea5t", "ph\u1ea1t t\u00f9 d\u00e0i h\u1ea1n", "kh\u00f4ng cho h\u01b0\u1edfng \u00e1n treo",
                        "t\u01b0\u1edbc quy\u1ec1n", "t\u1ecbch thu", "x\u1eed nghi\u00eam minh", "h\u00ecnh ph\u1ea1t nghi\u00eam kh\u1eafc"],
        },
        "victim": {
            "toward":  ["m\u1ee9c \u00e1n cao nh\u1ea5t", "h\u00ecnh ph\u1ea1t nghi\u00eam kh\u1eafc", "kh\u00f4ng cho h\u01b0\u1edfng \u00e1n treo",
                        "kh\u00f4ng \u00e1p d\u1ee5ng \u00e1n treo", "t\u01b0\u1edbc quy\u1ec1n", "b\u1ed3i th\u01b0\u1eddng thi\u1ec7t h\u1ea1i",
                        "y\u00eau c\u1ea7u b\u1ed3i th\u01b0\u1eddng", "\u0111\u1ec1 ngh\u1ecb ph\u1ea1t n\u1eb7ng", "b\u1ecb h\u1ea1i",
                        "t\u00ecnh ti\u1ebft t\u0103ng n\u1eb7ng", "h\u1eadu qu\u1ea3 nghi\u00eam tr\u1ecdng"],
            "against": ["\u0111\u1ec1 ngh\u1ecb \u00e1n treo", "xin mi\u1ec5n", "gi\u1ea3m nh\u1eb9 h\u00ecnh ph\u1ea1t",
                        "n\u00ean \u00e1p d\u1ee5ng \u00e1n treo", "kh\u00f4ng \u0111\u00e1ng b\u1ecb ph\u1ea1t", "th\u00e2n ch\u1ee7"],
        },
        "neutral": {
            "toward":  ["c\u0103n c\u1ee9", "nh\u1eadn \u0111\u1ecbnh", "xem x\u00e9t", "c\u00e2n nh\u1eafc", "theo quy \u0111\u1ecbnh",
                        "h\u1ed9i \u0111\u1ed3ng x\u00e9t x\u1eed", "quy \u0111\u1ecbnh t\u1ea1i", "ph\u00e1p lu\u1eadt quy \u0111\u1ecbnh"],
            "against": ["ki\u00ean quy\u1ebft \u0111\u1ec1 ngh\u1ecb", "nh\u1ea5t \u0111\u1ecbnh ph\u1ea3i ph\u1ea1t",
                        "b\u1ea3o v\u1ec7 b\u1ecb c\u00e1o b\u1eb1ng m\u1ecdi gi\u00e1", "ph\u1ea3i tr\u1ea3 gi\u00e1"],
        },
    }
    t = text.lower()
    role_cfg = _ROLE_SIGNAL_VOCAB.get(role, {})
    toward  = role_cfg.get("toward", [])
    against = role_cfg.get("against", [])
    pos = sum(1 for k in toward  if k in t)
    neg = sum(1 for k in against if k in t)
    score = (pos / max(len(toward), 1)) - 0.5 * (neg / max(len(against), 1))
    return round(max(0.0, min(1.0, score)), 4)
