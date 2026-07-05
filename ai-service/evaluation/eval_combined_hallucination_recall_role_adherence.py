#!/usr/bin/env python3
"""
eval_combined_hallucination_recall_role_adherence.py — SELF-CONTAINED evaluation script.
No imports from other eval_*.py files — all logic is inlined here.

METRICS:
  1. Retrieval Recall  — primary article in rerank node output (retrieved_article_nums)
  2. Generation Recall — primary article cited in system's response text (regex)
  3. Hallucination     — binary: any of L1/L2/L3 fires → hallucinated (1), else clean (0)
                         Rate = % of evaluations marked as hallucinated
  4. Role Adherence    — 4-dim deterministic keyword signal (0–1)

HOW TO RUN (100 cases, resume-safe) (fresh start):
  cd ~/PenalLawChatbot/ai-service/evaluation

    python3 eval_combined_hallucination_recall_role_adherence.py \
    --start 1 \
    --end 100 \
    --ai-url http://localhost:8000 \
    --timeout 600 \
    --log-file logs/eval_1_100.txt

RESUME:
    python3 eval_combined_hallucination_recall_role_adherence.py \
    --start 1 \
    --end 100 \
    --resume \
    --ai-url http://localhost:8000 \
    --timeout 600 \
    --log-file logs/eval_1_100.txt



OUTPUTS (all in results/ folder):
  new_combined_results.jsonl               — full per-case data (append-safe)
  new_combined_summary.json                — aggregated scores (overwritten on finish)
  new_combined_report.txt                  — human-readable report (append on resume)
  new_combined_results_role_progress.jsonl — mid-case resume sidecar (append-safe)

DOWNLOAD to local:
  scp -i chatbot-key.pem -r ubuntu@<EC2>:~/PenalLawChatbot/ai-service/evaluation/results/ \
      ~/Desktop/Projects/PenalLawChatbot/ai-service/evaluation/
"""

import os, json, re, sys, time, argparse, logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from tqdm import tqdm
import requests
from dotenv import load_dotenv

_HERE        = Path(__file__).resolve().parent
_AI_SERVICE  = _HERE.parent
PROJECT_ROOT = _AI_SERVICE.parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)


# ── Logging ───────────────────────────────────────────────────────────────────
class TqdmLoggingHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
    def emit(self, record):
        try:
            tqdm.write(self.format(record))
            self.flush()
        except Exception:
            self.handleError(record)

def setup_logging(log_file):
    log = logging.getLogger("combined_eval")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ch = TqdmLoggingHandler()
    ch.setFormatter(fmt)
    log.addHandler(ch)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — EDITION-AWARE GENERAL PART DETECTION
# ═══════════════════════════════════════════════════════════════════════════════
# BLHS 1999 / 1999 (sửa đổi 2009) : General Part = Điều 1–77
# BLHS 2015 and later              : General Part = Điều 1–107

_BLHS_1999_MARKERS = ["1999", "2009"]
_BLHS_2015_MARKERS = ["2015", "2017", "2025"]
_BLTTHS_ARTICLES: set = set()
_BLTTHS_MARKERS = ["tố tụng hình sự", "bltths", "b.l.t.t.h.s", "luật tố tụng"]
_BLHS_MARKERS   = ["bộ luật hình sự", "blhs", "b.l.h.s", "luật hình sự"]

# For hallucination: articles 1-77 are always valid to cite in any response
_ALWAYS_VALID = frozenset({str(i) for i in range(1, 78)})


def _is_general_part(num: str, nearby_text: str = "") -> bool:
    """
    True if the article is in the General Part (Phần chung) of BLHS.
    General Part articles define principles/penalties — never the primary crime article.
      BLHS 1999 / sửa đổi 2009  →  Điều 1–77   are General Part
      BLHS 2015 and later        →  Điều 1–107  are General Part
    For the ambiguous 78–107 range, edition markers in nearby_text decide.
    """
    try:
        n = int(re.match(r'\d+', str(num)).group(0))
    except (AttributeError, ValueError):
        return False
    if n <= 77:
        return True
    if n <= 107:
        t = nearby_text.lower()
        has_2015 = any(mk in t for mk in _BLHS_2015_MARKERS)
        has_1999 = any(mk in t for mk in _BLHS_1999_MARKERS)
        if has_2015 and not has_1999:
            return True   # BLHS 2015 — Điều 78–107 is General Part
        if has_1999:
            return False  # BLHS 1999 — Điều 78–107 are crime articles
        return False      # conservative: keep as possible crime article
    return False


def _nearest_marker_dist(t_low: str, art_mid: int, markers: list, window: int) -> int:
    best = window + 1
    lo, hi = max(0, art_mid - window), min(len(t_low), art_mid + window)
    region = t_low[lo:hi]
    for mk in markers:
        idx = 0
        while True:
            pos = region.find(mk, idx)
            if pos == -1:
                break
            dist = abs(lo + pos - art_mid)
            if dist < best:
                best = dist
            idx = pos + 1
    return best


def _extract_blhs_articles(text: str):
    """
    Extract BLHS crime article numbers from verdict text.
    Uses nearest-marker distance to distinguish BLHS vs BLTTHS citations.
    General Part articles are excluded (edition-aware).
    Returns (list_of_nums, confidence) where confidence='high' if explicit BLHS label found.
    """
    BLHS_WIN, BLTTHS_WIN, EDITION_WIN = 300, 160, 300
    t_low = text.lower()
    seen: dict = {}
    has_explicit_blhs = False

    for m in re.finditer(r"(?:đi[eề]u|dieu)\s*(\d+[a-z]?)", t_low):
        num     = m.group(1)
        art_mid = (m.start() + m.end()) // 2
        bd  = _nearest_marker_dist(t_low, art_mid, _BLHS_MARKERS,   BLHS_WIN)
        bld = _nearest_marker_dist(t_low, art_mid, _BLTTHS_MARKERS, BLTTHS_WIN)
        found_blhs, found_bltths = bd <= BLHS_WIN, bld <= BLTTHS_WIN

        ed_lo  = max(0, art_mid - EDITION_WIN)
        ed_hi  = min(len(t_low), art_mid + EDITION_WIN)
        nearby = t_low[ed_lo:ed_hi]

        if found_blhs and found_bltths:
            if bd <= bld and not _is_general_part(num, nearby):
                has_explicit_blhs = True
                seen.setdefault(num, None)
        elif found_blhs:
            if not _is_general_part(num, nearby):
                has_explicit_blhs = True
                seen.setdefault(num, None)
        elif not found_bltths:
            if num not in _BLTTHS_ARTICLES and not _is_general_part(num, nearby):
                seen.setdefault(num, None)

    return list(seen.keys()), "high" if has_explicit_blhs else "low"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATASET LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_all_cases(dataset_path: str) -> list:
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for entry in data:
        final_text = entry.get("final_verdict", "")
        all_gt_nums, confidence = _extract_blhs_articles(final_text)
        primary_num = next(
            (n for n in all_gt_nums
             if not _is_general_part(n, final_text)
             and n not in _BLTTHS_ARTICLES),
            None,
        )
        cases.append({
            "case_url":         entry.get("url", ""),
            "crime_type":       entry.get("crime_type", ""),
            "case_description": entry.get("case_description", ""),
            "final_verdict":    final_text,
            "primary_article":  f"Điều {primary_num}" if primary_num else "N/A",
            "primary_num":      primary_num,
            "all_gt_articles":  [f"Điều {n}" for n in all_gt_nums],
            "explanation":      entry.get("explanation", ""),
            "gt_confidence":    confidence,
        })
    return cases


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — AI SERVICE CALL
# ═══════════════════════════════════════════════════════════════════════════════

def call_system(ai_url, question, role, timeout, log):
    try:
        r = requests.post(
            f"{ai_url.rstrip('/')}/predict",
            json={"case_content": question, "role": role, "conversation_history": []},
            headers={"Connection": "close"},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        log.warning(f"  /predict TIMEOUT after {timeout}s")
        return {"_timeout": True}
    except Exception as e:
        log.warning(f"  /predict failed: {e}")
        return {"_error": True, "_error_msg": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — RECALL MEASUREMENTS
# Two independent recall metrics:
#   Metric A — RETRIEVAL RECALL : primary article in rerank output (retrieved_article_nums)
#   Metric B — GENERATION RECALL: primary article cited in response text (regex)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_nums_from_text(text: str) -> set:
    """Extract all 'Điều N' article numbers from free text using regex."""
    return set(re.findall(
        r"(?i:điều|diều|điêu|đều)\s*(\d+[A-Za-z]?)", text
    ))


def recall_retrieval(primary_num: str, retrieved_nums: set) -> dict:
    """
    RETRIEVAL RECALL: Was the primary article in the rerank node output?
    retrieved_nums = set from /predict response field `retrieved_article_nums`
    (populated from state["documents"] after the rerank node runs).
    """
    if not retrieved_nums:
        return {
            "hit":    None,
            "source": "unavailable",
            "note":   "retrieved_article_nums not returned by API (old version?)",
        }
    hit = primary_num in retrieved_nums
    return {
        "hit":             hit,
        "source":          "retrieved_docs",
        "retrieved_nums":  sorted(retrieved_nums),
    }


def recall_generation(primary_num: str, result_text: str) -> dict:
    """
    GENERATION RECALL: Did the system's response text cite the primary article?
    Uses regex scan only — NOT mapped_laws — to measure what the LLM actually wrote.
    """
    text_nums = _extract_nums_from_text(result_text)
    hit = primary_num in text_nums
    return {
        "hit":        hit,
        "source":     "response_text" if hit else "miss",
        "cited_nums": sorted(text_nums),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — HALLUCINATION (BINARY)
# Binary: if ANY of L1/L2/L3 fires → hallucinated=True, else False.
# Rate = fraction of evaluations where hallucinated=True.
# ═══════════════════════════════════════════════════════════════════════════════

# Edition ranges for L2 check
_EDITION_RANGES = [
    ("BLHS 1999",                 date(2000,  7,  1), date(2009, 12, 31)),
    ("BLHS 1999 (sửa đổi 2009)", date(2010,  1,  1), date(2017, 12, 31)),
    ("BLHS 2015 (sửa đổi 2017)", date(2018,  1,  1), date(2025,  6, 30)),
    ("BLHS 2015 (sửa đổi 2025)", date(2025,  7,  1), date(9999,  1,  1)),
]


def _parse_date(s: str) -> Optional[date]:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            pass
    return None


def _edition_for_date(d: date) -> str:
    for name, start, end in _EDITION_RANGES:
        if start <= d <= end:
            return name
    return "BLHS 2015 (sửa đổi 2017)"


def _is_newer_edition(applied: str, expected: str) -> bool:
    order = [r[0] for r in _EDITION_RANGES]
    try:
        return order.index(applied) > order.index(expected)
    except ValueError:
        return False


def _article_num(s: str) -> Optional[str]:
    m = re.search(r"(\d+[A-Za-z]?)", str(s))
    return m.group(1) if m else None


# L1 — Retrieved-context: cited article in final response text not in retrieved docs
def layer1_vs_retrieved(result_text: str, retrieved_nums: set) -> dict:
    """
    L1: Does the final response text cite any article NOT in the RAG-retrieved documents?
    Checks the generate node's output (free-form Vietnamese text) directly.
    Catches training-knowledge leakage even when map_laws JSON stays clean.
    Articles 1-77 (BLHS General Part — sentencing principles) are excluded
    from the check via _ALWAYS_VALID since they are legitimately cited everywhere.
    """
    if not retrieved_nums:
        return {"triggered": False, "false_articles": [],
                "note": "retrieved_nums unavailable — cannot check L1"}
    cited_in_text = _extract_nums_from_text(result_text or "")
    false_arts = []
    for num in cited_in_text:
        if num in _ALWAYS_VALID:
            continue
        if num not in retrieved_nums:
            false_arts.append({"article": f"Điều {num}", "reason": "cited_in_response_but_not_retrieved"})
    return {"triggered": len(false_arts) > 0, "false_articles": false_arts}


# Map Laws Inefficiency — two-layer quality signal for the map_laws node
def layer_map_laws_inefficiency(mapped_laws: list, retrieved_nums: set) -> dict:
    """
    Two-layer quality signal for the map_laws node.

    ML1 (Citation check): Any valid article in mapped_laws is NOT in the retrieved set?
         Detects cases where the map_laws LLM maps to articles that were
         never retrieved — grounding failure.

    ML2 (Return check): Did map_laws return any valid law articles at all?
         Detects cases where the node returned nothing (null / empty / all errors)
         — complete mapping failure.

    triggered = ML1 OR ML2 (one fail counts as one inefficiency).
    Excludes BLHS General Part (articles 1–77) from the citation check.
    """
    # ── ML2: did map_laws return valid results? ────────────────────────────────
    valid_laws = [
        law for law in mapped_laws
        if not law.get("_mapping_error") and _article_num(law.get("article", ""))
    ]
    ml2_triggered = len(valid_laws) == 0  # map_laws returned nothing usable

    # ── ML1: did map_laws cite articles outside the retrieved set? ────────────
    if not retrieved_nums:
        ml1_triggered = False
        false_arts    = []
    else:
        false_arts = []
        for law in valid_laws:
            num = _article_num(law.get("article", ""))
            if not num or num in _ALWAYS_VALID:
                continue
            if num not in retrieved_nums:
                false_arts.append({"article": law.get("article", ""),
                                   "reason": "in_mapped_laws_but_not_retrieved"})
        ml1_triggered = len(false_arts) > 0

    return {
        "triggered":     ml1_triggered or ml2_triggered,
        "ml1_triggered": ml1_triggered,   # cited unretrieved article
        "ml2_triggered": ml2_triggered,   # returned no valid results
        "false_articles": false_arts,
    }


# L2 — Edition consistency: wrong BLHS edition for crime date
def layer2_edition(mapped_laws: list, extracted_facts: dict) -> dict:
    crime_date_str = (extracted_facts or {}).get("ngay_pham_toi", "")
    crime_date = _parse_date(crime_date_str) if crime_date_str else None
    if not crime_date:
        return {"triggered": False, "details": [],
                "note": "crime_date unavailable — cannot check L2"}
    expected = _edition_for_date(crime_date)
    errors = []
    for law in mapped_laws:
        if law.get("_mapping_error"):
            continue
        num = _article_num(law.get("article", ""))
        if not num or num in _ALWAYS_VALID:
            continue
        applied = (law.get("edition_applied") or "").strip()
        if not applied or applied == "N/A":
            continue
        if applied != expected and not _is_newer_edition(applied, expected):
            errors.append({"article": law.get("article", ""),
                           "expected": expected, "applied": applied})
    return {"triggered": len(errors) > 0, "details": errors,
            "expected_edition": expected, "crime_date": crime_date_str}


# L3 — Sentencing range: stated penalty contradicts the actual article range
def _parse_penalty_years(text: str) -> Optional[tuple]:
    t = text.lower()
    m = re.search(r"t[ừu]\s*(\d+)\s*th[áa]ng\s*[đd][ếe]n\s*(\d+)\s*n[ăa]m", t)
    if m:
        return (int(m.group(1)) / 12, float(m.group(2)))
    m = re.search(r"t[ừu]\s*(\d+)\s*n[ăa]m\s*[đd][ếe]n\s*(\d+)\s*n[ăa]m", t)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r"[đd][ếe]n\s*(\d+)\s*n[ăa]m", t)
    if m:
        return (0.0, float(m.group(1)))
    if "chung th" in t:
        return (20.0, float("inf"))
    if "tử hình" in t:
        return (float("inf"), float("inf"))
    return None


def layer3_sentencing(response_text: str, gt_articles: list,
                      article_contents: dict) -> dict:
    primary = next(
        (a for a in gt_articles
         if _article_num(a) and _article_num(a) not in _ALWAYS_VALID),
        None,
    )
    if not primary:
        return {"triggered": False, "note": "no_primary_article"}
    content = article_contents.get(primary, "")
    if not content:
        return {"triggered": False, "note": f"no_content_for_{primary}"}
    actual = _parse_penalty_years(content)
    stated = _parse_penalty_years(response_text)
    if actual and stated:
        s_min, s_max = stated
        a_min, a_max = actual
        tol = 1.5
        min_ok = abs(s_min - a_min) <= tol
        max_ok = (a_max == float("inf") and s_max >= 15) or \
                 (a_max != float("inf") and abs(s_max - a_max) <= tol)
        if not (min_ok and max_ok):
            return {"triggered": True,
                    "stated": stated, "actual": actual, "primary": primary}
    return {"triggered": False, "stated": stated, "actual": actual, "primary": primary}


def hallucination_binary(mapped_laws, retrieved_nums, extracted_facts,
                         result_text, gt_articles) -> dict:
    """
    Binary hallucination check.
    If ANY layer fires → hallucinated=True (1), else False (0).
    L1 now checks the final response TEXT (generate node output) — not the
    intermediate mapped_laws JSON — to catch training-knowledge leakage in
    the free-form Vietnamese generation.
    """
    l1 = layer1_vs_retrieved(result_text, retrieved_nums)  # ← text-level check
    l2 = layer2_edition(mapped_laws, extracted_facts)
    l3 = layer3_sentencing(result_text, gt_articles, {})
    any_triggered = l1["triggered"] or l2["triggered"] or l3["triggered"]
    return {
        "hallucinated":      any_triggered,
        "l1_triggered":      l1["triggered"],
        "l1_false_articles": l1.get("false_articles", []),
        "l2_triggered":      l2["triggered"],
        "l2_details":        l2.get("details", []),
        "l3_triggered":      l3["triggered"],
        "l3_stated":         l3.get("stated"),
        "l3_actual":         l3.get("actual"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — ROLE ADHERENCE (4-dim deterministic signal)
# Inlined from eval_role_adherence.py — zero API calls.
# ═══════════════════════════════════════════════════════════════════════════════

import re as _re

_ART_ALIGN = {
    "defense": {
        "positive": ["điều 51", "điều 54", "điều 65", "điều 59", "điều 62", "điều 63"],
        "negative": ["điều 52"],
    },
    "victim": {
        "positive": ["điều 52", "điều 48"],
        "negative": ["điều 54", "điều 65"],
    },
    "neutral": {"positive": [], "negative": []},
}

_SENT_DIR = {
    "defense": {
        "toward":  ["án treo", "cải tạo không giam giữ", "dưới mức thấp nhất",
                    "đề nghị giảm", "xin giảm nhẹ", "mức án thấp nhất", "khoan hồng",
                    "không cần thiết giam giữ", "không tái phạm"],
        "against": ["mức án cao nhất", "phạt tù dài hạn", "không cho hưởng án treo",
                    "tước quyền", "tịch thu"],
    },
    "victim": {
        "toward":  ["mức án cao nhất", "hình phạt nghiêm khắc", "không cho hưởng án treo",
                    "không áp dụng án treo", "tước quyền", "bồi thường thiệt hại",
                    "yêu cầu bồi thường", "đề nghị phạt nặng"],
        "against": ["đề nghị án treo", "xin miễn", "giảm nhẹ hình phạt",
                    "nên áp dụng án treo", "không đáng bị phạt"],
    },
    "neutral": {
        "toward":  ["căn cứ", "nhận định", "xem xét", "cân nhắc", "theo quy định",
                    "hội đồng xét xử", "quy định tại"],
        "against": ["kiên quyết đề nghị", "nhất định phải phạt",
                    "bảo vệ bị cáo bằng mọi giá", "phải trả giá"],
    },
}

_VOCAB = {
    "defense": {
        "positive": ["giảm nhẹ", "tình tiết giảm nhẹ", "thành khẩn", "ăn năn", "hối cải",
                     "lần đầu phạm tội", "phạm tội lần đầu", "nhân thân tốt",
                     "bồi thường", "khắc phục hậu quả", "hoàn cảnh khó khăn",
                     "bào chữa", "bảo vệ bị cáo", "thân chủ"],
        "negative": ["tăng nặng trách nhiệm", "không có khả năng cải tạo",
                     "nguy hiểm cho xã hội", "cần xử lý nghiêm"],
    },
    "victim": {
        "positive": ["tình tiết tăng nặng", "tăng nặng", "hậu quả nghiêm trọng",
                     "bồi thường thiệt hại", "thiệt hại", "bị hại",
                     "tiền án", "tái phạm", "có tổ chức", "dùng hung khí",
                     "bảo vệ quyền lợi bị hại", "đại diện bị hại"],
        "negative": ["đề nghị án treo", "xin miễn", "giảm nhẹ hình phạt",
                     "không đáng bị phạt"],
    },
    "neutral": {
        "positive": ["giảm nhẹ", "tăng nặng", "nhận định", "xem xét", "cân nhắc",
                     "theo quy định", "căn cứ", "pháp luật quy định"],
        "negative": ["kiên quyết đề nghị", "nhất định phải phạt",
                     "bảo vệ bị cáo bằng mọi giá"],
    },
}

_NEUTRAL_BALANCE_REQUIRED = ["giảm nhẹ", "tăng nặng"]
_CITATION_PAT   = _re.compile(r"điều\s*\d+[a-z]?(?:\s*(?:khoản|điểm)\s*[\d\w]+)?", _re.I | _re.U)
_CONCLUSION_PAT = _re.compile(
    r"(đề nghị|kiến nghị|kết luận|nhận định|quyết định|xử phạt|tuyên|yêu cầu)",
    _re.I | _re.U,
)


def _d1_article_alignment(text: str, role: str) -> float:
    if role == "neutral":
        has_mitigating = any(a in text for a in ["điều 51", "điều 54", "điều 65"])
        has_aggravating = any(a in text for a in ["điều 52", "điều 48"])
        has_primary = bool(_CITATION_PAT.search(text))
        score = 0.0
        if has_primary:     score += 0.4
        if has_mitigating:  score += 0.3
        if has_aggravating: score += 0.3
        return min(1.0, score)
    cfg = _ART_ALIGN[role]
    pos_hits = sum(1 for a in cfg["positive"] if a in text)
    neg_hits = sum(1 for a in cfg["negative"] if a in text)
    req = max(1, len(cfg["positive"]))
    pos_rate = min(1.0, pos_hits / req) if cfg["positive"] else 0.5
    penalty  = 0.3 * min(1.0, neg_hits / max(1, len(cfg["negative"])))
    return max(0.0, pos_rate - penalty)


def _d2_sentencing_direction(text: str, role: str) -> float:
    cfg = _SENT_DIR[role]
    toward_hits  = sum(1 for p in cfg["toward"]  if p in text)
    against_hits = sum(1 for p in cfg["against"] if p in text)
    req = max(1, int(len(cfg["toward"]) * 0.4))
    toward_rate = min(1.0, toward_hits / req)  if cfg["toward"]  else 0.5
    against_pen = 0.4 * min(1.0, against_hits / max(1, len(cfg["against"]))) if cfg["against"] else 0.0
    return max(0.0, toward_rate - against_pen)


def _d3_vocabulary_stance(text: str, role: str) -> float:
    cfg = _VOCAB[role]
    pos_hits = [p for p in cfg["positive"] if p in text]
    neg_hits = [n for n in cfg["negative"]  if n in text]
    req_pos  = max(1, int(len(cfg["positive"]) * 0.4))
    pos_rate = min(1.0, len(pos_hits) / req_pos) if cfg["positive"] else 1.0
    neg_rate = min(1.0, len(neg_hits) / max(1, len(cfg["negative"]))) if cfg["negative"] else 0.0
    bonus = 0.0
    if role == "neutral":
        both  = all(t in text for t in _NEUTRAL_BALANCE_REQUIRED)
        bonus = 0.15 if both else -0.15
    return max(0.0, min(1.0, pos_rate - 0.5 * neg_rate + bonus))


def _d4_citation_structure(text: str) -> float:
    cit_count = len(_CITATION_PAT.findall(text))
    has_concl = bool(_CONCLUSION_PAT.search(text))
    cit_score = min(1.0, cit_count / 3)
    return round(0.6 * cit_score + 0.4 * float(has_concl), 4)


def signal_score(response: str, role: str) -> dict:
    """4-dimension deterministic role-adherence scorer. Zero API cost."""
    t  = response.lower()
    d1 = _d1_article_alignment(t, role)
    d2 = _d2_sentencing_direction(t, role)
    d3 = _d3_vocabulary_stance(t, role)
    d4 = _d4_citation_structure(t)
    score = round(0.30 * d1 + 0.30 * d2 + 0.25 * d3 + 0.15 * d4, 4)
    return {
        "score":       score,
        "d1_article":  round(d1, 4),
        "d2_sentence": round(d2, 4),
        "d3_vocab":    round(d3, 4),
        "d4_struct":   round(d4, 4),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — CORE METRICS COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_metrics(response_dict, case, role):
    """
    Computes all 5 deterministic metrics for a single /predict response.
    No LLM calls — runs instantly.
    Metrics:
      A. Retrieval Recall       — primary GT article in reranker output
      B. Generation Recall      — primary GT article cited in final response text
      C. Map Laws Inefficiency  — map_laws JSON cites articles not in retrieved set
      D. Hallucination          — final response text cites articles not retrieved (L1)
                                  + wrong BLHS edition (L2) + wrong sentencing range (L3)
      E. Role Adherence         — 4-dim deterministic keyword signal
    """
    result_text     = response_dict.get("result", response_dict.get("text", ""))
    mapped_laws     = response_dict.get("mapped_laws") or []
    extracted_facts = response_dict.get("extracted_facts") or {}
    retrieved_nums  = set(response_dict.get("retrieved_article_nums") or [])
    primary_num     = case.get("primary_num")

    # ── Metric A: Retrieval Recall ────────────────────────────────────────────
    if primary_num:
        ret_recall = recall_retrieval(primary_num, retrieved_nums)
    else:
        ret_recall = {"hit": None, "source": "no_gt", "note": "no ground truth primary"}

    # ── Metric B: Generation Recall ───────────────────────────────────────────
    if primary_num:
        gen_recall = recall_generation(primary_num, result_text)
    else:
        gen_recall = {"hit": None, "source": "no_gt", "cited_nums": []}

    # ── Metric C: Map Laws Inefficiency ──────────────────────────────────────
    # Quality signal for the map_laws node: how often does it map to articles
    # that were NOT in the retrieved context?
    ml_ineff = layer_map_laws_inefficiency(mapped_laws, retrieved_nums)

    # ── Metric D: Hallucination (binary) ─────────────────────────────────────
    # L1 checks final response TEXT (not mapped_laws JSON).
    hall = hallucination_binary(
        mapped_laws, retrieved_nums, extracted_facts,
        result_text, case["all_gt_articles"],
    )

    # ── Metric E: Role Adherence ──────────────────────────────────────────────
    sig        = signal_score(result_text, role)
    role_score = sig["score"]

    return {
        # Retrieval Recall
        "retrieval_recall_hit":    ret_recall["hit"],
        "retrieval_recall_source": ret_recall.get("source", ""),
        "retrieved_nums":          sorted(retrieved_nums),
        # Generation Recall
        "generation_recall_hit":   gen_recall["hit"],
        "generation_recall_cited": gen_recall.get("cited_nums", []),
        # Map Laws Inefficiency (ML1=citation check, ML2=return check)
        "map_laws_inefficiency":          ml_ineff["triggered"],
        "map_laws_ineff_ml1_triggered":   ml_ineff["ml1_triggered"],
        "map_laws_ineff_ml2_triggered":   ml_ineff["ml2_triggered"],
        "map_laws_ineff_false_articles":  ml_ineff.get("false_articles", []),
        # Hallucination (binary, L1=response text, L2=edition, L3=sentencing)
        "hallucinated":            hall["hallucinated"],
        "hall_l1_triggered":       hall["l1_triggered"],
        "hall_l1_false_articles":  hall["l1_false_articles"],
        "hall_l2_triggered":       hall["l2_triggered"],
        "hall_l2_details":         hall["l2_details"],
        "hall_l3_triggered":       hall["l3_triggered"],
        "hall_l3_stated":          hall.get("l3_stated"),
        "hall_l3_actual":          hall.get("l3_actual"),
        # Role Adherence
        "role_adherence":          role_score,
        "role_d1":                 sig["d1_article"],
        "role_d2":                 sig["d2_sentence"],
        "role_d3":                 sig["d3_vocab"],
        "role_d4":                 sig["d4_struct"],
        # Response
        "text_preview":            result_text[:300],
        "full_response":           result_text,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — REPORTING
# ═══════════════════════════════════════════════════════════════════════════════

def _is_clarification(pred: dict) -> bool:
    if not pred or pred.get("_timeout") or pred.get("_error"):
        return False
    result = pred.get("result", "")
    is_clarification_text = (
        "\u2139\ufe0f" in result[:30]
        or "\u24d8" in result[:30]
        or result.strip().startswith("Để phân tích chính xác")
    )
    return (not pred.get("mapped_laws")) and is_clarification_text


def _pct(val):
    return f"{val * 100:.1f}%"


def _icon(hit):
    if hit is True:  return "✅"
    if hit is False: return "❌"
    return "➖"


def _print_case_report(report, cidx, total, case, role, ev):
    gt_conf = case.get("gt_confidence", "high")
    gt_flag = "  ⚠️ [LOW CONFIDENCE GT]" if gt_conf == "low" else ""

    report(f"  ┌─ [{cidx}/{total}]  Role: {role.upper()}  ──────────────────────────────────────")
    report(f"  │  Crime   : {case.get('crime_type', 'N/A')}")
    report(f"  │  GT Art  : {case.get('primary_article', 'N/A')}  [confidence: {gt_conf}]{gt_flag}")
    report(f"  │")

    # Retrieval Recall
    rr  = ev["retrieval_recall_hit"]
    retrieved = ev.get("retrieved_nums", [])
    report(f"  │  {_icon(rr)} Retrieval Recall  : {'HIT' if rr else ('MISS' if rr is False else 'N/A')}")
    if retrieved:
        report(f"  │       Rerank output articles : {', '.join(retrieved)}")

    # Generation Recall
    gr   = ev["generation_recall_hit"]
    cited = ev.get("generation_recall_cited", [])
    report(f"  │  {_icon(gr)} Generation Recall : {'HIT' if gr else ('MISS' if gr is False else 'N/A')}")
    report(f"  │       Cited in response text    : {', '.join(cited) or 'None'}")

    # Map Laws Inefficiency
    ml_ineff  = ev.get("map_laws_inefficiency", False)
    ml1       = ev.get("map_laws_ineff_ml1_triggered", False)
    ml2       = ev.get("map_laws_ineff_ml2_triggered", False)
    ml_icon   = "✅" if not ml_ineff else "⚠️ "
    ml_status = "CLEAN" if not ml_ineff else "INEFFICIENT"
    ml_arts   = ev.get("map_laws_ineff_false_articles", [])
    report(f"  │  {ml_icon} Map Laws Ineff.  : {ml_status}  (ML1={ml1}  ML2={ml2})")
    if ml2:
        report(f"  │       ML2 — map_laws returned no valid articles (empty / all errors)")
    if ml_arts:
        report(f"  │       ML1 — Mapped but not retrieved : {', '.join(a['article'] for a in ml_arts)}")

    # Hallucination
    h_icon = "✅" if not ev["hallucinated"] else "🚨"
    report(f"  │  {h_icon} Hallucination     : {'CLEAN' if not ev['hallucinated'] else 'HALLUCINATED'}"
           f"  (L1={ev['hall_l1_triggered']}  L2={ev['hall_l2_triggered']}  L3={ev['hall_l3_triggered']})")
    if ev["hall_l1_false_articles"]:
        report(f"  │       L1 — Cited in response but not retrieved : {', '.join(a['article'] for a in ev['hall_l1_false_articles'])}")
    if ev["hall_l2_details"]:
        for d in ev["hall_l2_details"]:
            report(f"  │       L2 — Wrong edition : {d['article']} applied={d['applied']} expected={d['expected']}")
    if ev["hall_l3_triggered"]:
        report(f"  │       L3 — Sentencing mismatch: stated={ev['hall_l3_stated']}  actual={ev['hall_l3_actual']}")

    # Role Adherence
    ra_icon = "✅" if (ev["role_adherence"] or 0) >= 0.7 else "⚠️ "
    report(f"  │  {ra_icon} Role Adherence    : {ev['role_adherence']:.3f}"
           f"  (d1={ev.get('role_d1','?')}  d2={ev.get('role_d2','?')}"
           f"  d3={ev.get('role_d3','?')}  d4={ev.get('role_d4','?')})")
    report(f"  │  Preview : {repr(ev['text_preview'][:200])}")
    report(f"  │  ── FULL RESPONSE ──────────────────────────────────────────")
    for line in (ev.get('full_response', '') or '').splitlines():
        report(f"  │  {line}")
    report(f"  └──────────────────────────────────────────────────────────────")


def _print_running_totals(report, metrics, processed):
    n = metrics["total_evals"]
    if n == 0:
        return
    s = metrics["system"]

    def _rate(hits, total): return hits / total if total else 0.0
    def _avg(lst): return sum(lst) / len(lst) if lst else 0.0

    rr_rate   = _rate(s["ret_recall_hits"],  s["ret_recall_total"])
    gr_rate   = _rate(s["gen_recall_hits"],  s["gen_recall_total"])
    ml_rate   = _avg(s["map_laws_ineff_flags"])   # fraction of evals where map_laws was inefficient
    hall_rate = _avg(s["hallucination_flags"])     # avg of binary flags = rate
    role_rate = _avg(s["role_scores"])

    report(f"  📊 Running totals after {processed} case(s)  ({n} role evals)")
    report(f"     {'Metric':<30} {'Value':>9}  Target")
    report(f"     {'-'*55}")
    report(f"     {'Retrieval Recall':<30} {_pct(rr_rate):>9}  ≥90%  {'✅' if rr_rate >= 0.90 else '❌'}"
           f"  ({s['ret_recall_hits']}/{s['ret_recall_total']})")
    report(f"     {'Generation Recall':<30} {_pct(gr_rate):>9}  ≥90%  {'✅' if gr_rate >= 0.90 else '❌'}"
           f"  ({s['gen_recall_hits']}/{s['gen_recall_total']})")
    report(f"     {'Map Laws Inefficiency':<30} {_pct(ml_rate):>9}  ≤15%  {'✅' if ml_rate <= 0.15 else '❌'}")
    report(f"     {'Hallucination Rate':<30} {_pct(hall_rate):>9}  ≤10%  {'✅' if hall_rate <= 0.10 else '❌'}")
    report(f"     {'Role Adherence':<30} {_pct(role_rate):>9}  ≥85%  {'✅' if role_rate >= 0.85 else '❌'}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Combined Eval — Retrieval Recall · Generation Recall · Hallucination · Role Adherence")
    parser.add_argument("--dataset",  default=str(PROJECT_ROOT / "ai-service/evaluation/thesis_eval_unique.json"))
    parser.add_argument("--output",   default=str(PROJECT_ROOT / "ai-service/evaluation/results/new_combined_results.jsonl"))
    parser.add_argument("--summary",  default=str(PROJECT_ROOT / "ai-service/evaluation/results/new_combined_summary.json"))
    parser.add_argument("--report",   default=str(PROJECT_ROOT / "ai-service/evaluation/results/new_combined_report.txt"))
    parser.add_argument("--ai-url",   default=os.getenv("AI_SERVICE_URL", "http://localhost:8000"))
    parser.add_argument("--timeout",  type=int,   default=600)
    parser.add_argument("--start",    type=int,   default=1)
    parser.add_argument("--end",      type=int,   default=0)
    parser.add_argument("--resume",   action="store_true")
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--delay",    type=float, default=0.5)
    args = parser.parse_args()

    log = setup_logging(args.log_file)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_fh = open(report_path, "a", encoding="utf-8")

    def report(line=""):
        log.info(line)
        report_fh.write(line + "\n")
        report_fh.flush()

    report("=" * 70)
    report("VNPLaw Combined Evaluation")
    report("  Metrics: Retrieval Recall · Generation Recall · Map Laws Inefficiency · Hallucination (binary) · Role Adherence")
    report("  ⚡ Fully deterministic — zero LLM API calls")
    report(f"  AI service : {args.ai_url}")
    report(f"  Case range : {args.start} – {'END' if not args.end else args.end}")
    report("=" * 70)

    cases = load_all_cases(args.dataset)
    s_idx = max(0, args.start - 1)
    e_idx = args.end if args.end else len(cases)
    cases = cases[s_idx:e_idx]
    n_with_primary = sum(1 for c in cases if c["primary_num"])
    report(f"Cases loaded: {len(cases)}  ({n_with_primary} have primary GT article for recall)")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Resume state ──────────────────────────────────────────────────────────
    done_urls = set()
    preloaded = {
        "ret_recall_hits": 0, "ret_recall_total": 0,
        "gen_recall_hits": 0, "gen_recall_total": 0,
        "map_laws_ineff_flags": [],
        "hallucination_flags": [], "role_scores": [],
        "clarification_skipped": 0, "timeout_skipped": 0,
        "ret_misses": [], "gen_misses": [],
    }

    # ── Resume: also try the progress sidecar (faster — one entry per role eval)
    prog_path = out_path.with_name("new_combined_results_role_progress.jsonl")

    if args.resume and prog_path.exists():
        with open(prog_path, encoding="utf-8") as pf:
            for line in pf:
                try:
                    entry = json.loads(line)
                    url  = entry.get("url", "")
                    role = entry.get("role", "")
                    ev   = entry.get("eval", {})
                    if url:
                        done_urls.add(url)  # tentative — will validate below
                    if ev.get("_skipped"):
                        reason = ev.get("_skip_reason", "")
                        if "clarification" in reason:
                            preloaded["clarification_skipped"] += 1
                        else:
                            preloaded["timeout_skipped"] += 1
                        continue
                    # Accumulate metrics from already-completed evaluations
                    rr = ev.get("retrieval_recall_hit")
                    if rr is not None:
                        preloaded["ret_recall_hits"]  += int(bool(rr))
                        preloaded["ret_recall_total"] += 1
                    gr = ev.get("generation_recall_hit")
                    if gr is not None:
                        preloaded["gen_recall_hits"]  += int(bool(gr))
                        preloaded["gen_recall_total"] += 1
                    ml = ev.get("map_laws_inefficiency")
                    if ml is not None:
                        preloaded["map_laws_ineff_flags"].append(int(bool(ml)))
                    h = ev.get("hallucinated")
                    if h is not None:
                        preloaded["hallucination_flags"].append(int(bool(h)))
                    ra = ev.get("role_adherence")
                    if ra is not None:
                        preloaded["role_scores"].append(float(ra))
                except Exception:
                    pass

    # A URL is truly "done" only when ALL 3 roles have been evaluated.
    # Re-parse the progress file to build per-url per-role tracking.
    done_roles_from_resume: dict = {}
    if args.resume and prog_path.exists():
        with open(prog_path, encoding="utf-8") as pf:
            for line in pf:
                try:
                    entry = json.loads(line)
                    u, r = entry.get("url", ""), entry.get("role", "")
                    if u and r:
                        done_roles_from_resume.setdefault(u, set()).add(r)
                except Exception:
                    pass
    # Only mark as fully done when all 3 roles are complete
    done_urls = {u for u, roles in done_roles_from_resume.items()
                 if {"neutral", "defense", "victim"} <= roles}

    if args.resume:
        report(f"Resume mode: {len(done_urls)} fully-done cases + "
               f"{len(done_roles_from_resume) - len(done_urls)} partial — loaded from progress sidecar.")

    # done_roles tracks per-url per-role completion during THIS run
    # Seed it with whatever was already done (from resume sidecar above)
    done_roles: dict = {u: set(rs) for u, rs in done_roles_from_resume.items()}

    metrics = {
        "system": {
            "ret_recall_hits":       preloaded["ret_recall_hits"],
            "ret_recall_total":      preloaded["ret_recall_total"],
            "gen_recall_hits":       preloaded["gen_recall_hits"],
            "gen_recall_total":      preloaded["gen_recall_total"],
            "map_laws_ineff_flags":  list(preloaded["map_laws_ineff_flags"]),
            "hallucination_flags":   list(preloaded["hallucination_flags"]),
            "role_scores":           list(preloaded["role_scores"]),
            "clarification_skipped": preloaded["clarification_skipped"],
            "timeout_skipped":       preloaded["timeout_skipped"],
        },
        "total_evals": len(preloaded["hallucination_flags"]),
    }
    processed   = len(done_urls)
    n_preloaded = len(done_urls)

    interrupted = False
    try:
        with open(out_path, "a", encoding="utf-8") as out_f, \
             open(prog_path, "a", encoding="utf-8") as prog_f:
            for i, case in enumerate(tqdm(cases, desc="Evaluating", unit="case")):
                url = case["case_url"]
                if url in done_urls:
                    continue

                cidx  = s_idx + i + 1
                total = s_idx + len(cases)

                report("")
                report(f"━━━ CASE {cidx}/{total} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                report(f"  URL: {url}")

                row   = {"case_index": cidx, "case_url": url, "evaluations": {}}
                roles = ["neutral", "defense", "victim"]

                for role in roles:
                    # Mid-case resume
                    if url in done_roles and role in done_roles[url]:
                        report(f"  ⏭️  [SKIP-ROLE] {role.upper()} already done.")
                        try:
                            stored_ev = next(
                                json.loads(l)["eval"]
                                for l in open(prog_path, encoding="utf-8")
                                if json.loads(l).get("url") == url and json.loads(l).get("role") == role
                            )
                            row["evaluations"][role] = {"system": stored_ev}
                        except Exception:
                            pass
                        continue

                    report(f"  ┌─ ⏳ Processing Role: {role.upper()} ───────────────────────")

                    t0       = time.time()
                    sys_pred = call_system(args.ai_url, case["case_description"], role, args.timeout, log)
                    report(f"  │  ✅ Fetched in {time.time()-t0:.1f}s")

                    # Timeout / error
                    if sys_pred.get("_timeout") or sys_pred.get("_error"):
                        metrics["system"]["timeout_skipped"] += 1
                        reason = "TIMEOUT" if sys_pred.get("_timeout") else "ERROR"
                        report(f"  ⚠️  [{reason}] Role {role.upper()} excluded from metrics.")
                        skip_ev = {"_skipped": True, "_skip_reason": reason.lower()}
                        row["evaluations"][role] = {"system": skip_ev}
                        prog_f.write(json.dumps({"url": url, "role": role, "eval": skip_ev}, ensure_ascii=False) + "\n")
                        prog_f.flush()
                        done_roles.setdefault(url, set()).add(role)
                        metrics["total_evals"] += 1
                        time.sleep(args.delay)
                        continue

                    # Clarification
                    if _is_clarification(sys_pred):
                        metrics["system"]["clarification_skipped"] += 1
                        report(f"  ⤼ [SKIP] Role {role.upper()} returned clarification — excluded.")
                        skip_ev = {"_skipped": True, "_skip_reason": "clarification"}
                        row["evaluations"][role] = {"system": skip_ev}
                        prog_f.write(json.dumps({"url": url, "role": role, "eval": skip_ev}, ensure_ascii=False) + "\n")
                        prog_f.flush()
                        done_roles.setdefault(url, set()).add(role)
                        metrics["total_evals"] += 1
                        time.sleep(args.delay)
                        continue

                    ev = evaluate_metrics(sys_pred, case, role)
                    _print_case_report(report, cidx, total, case, role, ev)
                    row["evaluations"][role] = {"system": ev}

                    prog_f.write(json.dumps({"url": url, "role": role, "eval": ev}, ensure_ascii=False) + "\n")
                    prog_f.flush()
                    done_roles.setdefault(url, set()).add(role)
                    metrics["total_evals"] += 1

                    # Accumulate metrics
                    rr = ev["retrieval_recall_hit"]
                    if rr is not None:
                        metrics["system"]["ret_recall_hits"]  += int(rr)
                        metrics["system"]["ret_recall_total"] += 1
                    gr = ev["generation_recall_hit"]
                    if gr is not None:
                        metrics["system"]["gen_recall_hits"]  += int(gr)
                        metrics["system"]["gen_recall_total"] += 1
                    metrics["system"]["map_laws_ineff_flags"].append(int(bool(ev.get("map_laws_inefficiency", False))))
                    metrics["system"]["hallucination_flags"].append(int(ev["hallucinated"]))
                    metrics["system"]["role_scores"].append(ev["role_adherence"])

                    time.sleep(args.delay)

                out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                out_f.flush()
                processed += 1
                _print_running_totals(report, metrics, processed)

    except KeyboardInterrupt:
        interrupted = True
        report("")
        report("⚠️  Interrupted (Ctrl+C) — writing partial summary...")

    finally:
        def _rate(h, t): return round(h / t, 4) if t else None
        def _avg(lst): return round(sum(lst) / len(lst), 4) if lst else 0.0
        def _pct_or_na(v): return "N/A" if v is None else _pct(v)
        def _pass_str(v, tgt, op="ge"):
            if v is None: return "➖ N/A"
            passed = (v >= tgt) if op == "ge" else (v <= tgt)
            return "✅ PASS" if passed else "❌ FAIL"

        s = metrics["system"]
        rr_rate   = _rate(s["ret_recall_hits"],  s["ret_recall_total"])
        gr_rate   = _rate(s["gen_recall_hits"],  s["gen_recall_total"])
        ml_rate   = _avg(s["map_laws_ineff_flags"])
        hall_rate = _avg(s["hallucination_flags"])
        role_rate = _avg(s["role_scores"])
        status    = "PARTIAL (interrupted)" if interrupted else "COMPLETE"

        summary = {
            "meta": {
                "status":                 status,
                "n_cases_evaluated":      processed,
                "total_role_evaluations": metrics["total_evals"],
                "case_range": f"{args.start}–{'END' if not args.end else args.end}",
            },
            "system": {
                "retrieval_recall":       rr_rate,
                "generation_recall":      gr_rate,
                "map_laws_inefficiency":  ml_rate,
                "hallucination_rate":     hall_rate,
                "role_adherence":         role_rate,
            },
            "pass": {
                "retrieval_recall":      (rr_rate  >= 0.90) if rr_rate  is not None else None,
                "generation_recall":     (gr_rate  >= 0.90) if gr_rate  is not None else None,
                "map_laws_inefficiency": ml_rate   <= 0.15,
                "hallucination":         hall_rate <= 0.10,
                "role":                  role_rate >= 0.85,
            },
        }

        sum_path = Path(args.summary)
        sum_path.parent.mkdir(parents=True, exist_ok=True)
        with open(sum_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        resume_note  = f" ({n_preloaded} preloaded + {processed-n_preloaded} new)" if n_preloaded else ""
        n_clarif  = s["clarification_skipped"]
        n_timeout = s["timeout_skipped"]
        skip_parts = []
        if n_clarif:  skip_parts.append(f"{n_clarif} clarification")
        if n_timeout: skip_parts.append(f"{n_timeout} timeout/error")
        skipped_note = f"  ({', '.join(skip_parts)} excluded)" if skip_parts else ""

        report("")
        report("=" * 70)
        report(f"  {'⚠️  PARTIAL ' if interrupted else ''}RESULTS — {processed} cases{resume_note}"
               f"  ({metrics['total_evals']} role evals){skipped_note}")
        report("=" * 70)
        report(f"  {'Metric':<32} {'Value':>9}  {'Target':>8}  Pass?")
        report(f"  {'-'*60}")
        report(f"  {'Retrieval Recall':<32} {_pct_or_na(rr_rate):>9}  {'≥90%':>8}  {_pass_str(rr_rate, 0.90)}")
        report(f"  {'Generation Recall':<32} {_pct_or_na(gr_rate):>9}  {'≥90%':>8}  {_pass_str(gr_rate, 0.90)}")
        report(f"  {'Map Laws Inefficiency':<32} {_pct(ml_rate):>9}  {'≤15%':>8}  {_pass_str(ml_rate, 0.15, 'le')}")
        report(f"  {'Hallucination Rate':<32} {_pct(hall_rate):>9}  {'≤10%':>8}  {_pass_str(hall_rate, 0.10, 'le')}")
        report(f"  {'Role Adherence':<32} {_pct(role_rate):>9}  {'≥85%':>8}  {_pass_str(role_rate, 0.85)}")
        report(f"  {'-'*60}")
        report(f"  Detailed JSONL : {out_path}")
        report(f"  Summary JSON   : {sum_path}")
        report(f"  Report TXT     : {report_path}  ← download this for offline review")
        if interrupted:
            report(f"  ↺  Resume: --resume --start {args.start} --end {'END' if not args.end else args.end}")
        report("=" * 70)
        report("")

        report_fh.flush()
        report_fh.close()


if __name__ == "__main__":
    main()
