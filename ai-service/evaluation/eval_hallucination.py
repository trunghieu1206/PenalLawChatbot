#!/usr/bin/env python3
"""
eval_hallucination.py — 4-Layer hallucination evaluation for VNPLaw AI service.

Run this ON the machine hosting ai-service (calls http://localhost:8000/predict).

LAYERS:
  L1 — Article Existence    : cited article not in ground-truth OR known BLHS corpus
  L2 — Edition/Retroactivity: wrong BLHS edition for the crime date (Article 7)
  L3 — Sentencing Range     : stated penalty range contradicts actual article content
  L4 — Factual Consistency  : response contradicts case facts (LLM judge, cheapest call)

WEIGHTS: L1=0.30, L2=0.30, L3=0.25, L4=0.15
  composite_score = weighted sum of triggered layers (0.0–1.0)
  hallucination_rate = fraction of cases with composite_score > 0

HOW TO RUN:
  python3 eval_hallucination.py --start 1 --end 100 --log-file hall_1_100.txt
  python3 eval_hallucination.py --start 1 --end 100 --skip-l4 --log-file hall_fast.txt
  python3 eval_hallucination.py  # all cases
"""

import os, json, re, sys, time, argparse, logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from collections import defaultdict

import requests
from openai import OpenAI
from dotenv import load_dotenv

# Load .env from project root (eval/ → ai-service/ → PenalLawChatbot/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)

# ── Constants ─────────────────────────────────────────────────────────────────
WEIGHTS = {"l1": 0.30, "l2": 0.30, "l3": 0.25, "l4": 0.15}

# Articles always valid to cite — never flagged as hallucination
_ALWAYS_VALID = {
    "7","28","32","34","42","45","46","47","48","49","50",
    "51","52","53","54","55","56","57","58","59","60","65",
}

_EDITION_RANGES = [
    ("BLHS 1999",                 date(2000,  7,  1), date(2009, 12, 31)),
    ("BLHS 1999 (sửa đổi 2009)", date(2010,  1,  1), date(2017, 12, 31)),
    ("BLHS 2015 (sửa đổi 2017)", date(2018,  1,  1), date(2025,  6, 30)),
    ("BLHS 2015 (sửa đổi 2025)", date(2025,  7,  1), date(9999,  1,  1)),
]


# ── Logging ───────────────────────────────────────────────────────────────────
from tqdm import tqdm

class TqdmLoggingHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)

def setup_logging(log_file: Optional[str]) -> logging.Logger:
    log = logging.getLogger("hallucination")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    ch = TqdmLoggingHandler()
    ch.setFormatter(fmt)
    log.addHandler(ch)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


# ── Dataset loading ───────────────────────────────────────────────────────────
def load_cases(dataset_path: str, _unused: str = "") -> list:
    """
    Load from case_eval_dataset.json. Each case exposes:
      case_url, case_description, explanation, final_verdict, crime_type.
    """
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for entry in data:
        cases.append({
            "case_url":          entry.get("url", ""),
            "crime_type":        entry.get("crime_type", ""),
            "case_description":  entry.get("case_description", ""),
            "explanation":       entry.get("explanation", ""),
            "final_verdict":     entry.get("final_verdict", ""),
            "ground_truth_articles": [],
            "article_contents":  {},
        })
    return cases


def _valid_article_set(dataset_path: str, _unused: str = "") -> set:
    """All article numbers cited in final_verdict texts — treated as known-valid corpus."""
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    nums: set = set()
    for entry in data:
        for m in re.finditer(r"(?i:điều|diều|điêu|đều)\s*(\d+[A-Za-z]?)",
                             entry.get("final_verdict", "")):
            nums.add(m.group(1))
    return nums


# ── AI service call ───────────────────────────────────────────────────────────
def call_predict(ai_url: str, question: str, timeout: int, log: logging.Logger) -> dict:
    try:
        r = requests.post(f"{ai_url.rstrip('/')}/predict",
                          json={"case_content": question, "role": "neutral",
                                "conversation_history": []},
                          timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"  /predict failed: {e}")
        return {}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_nums(text: str) -> set:
    """Extract article numbers from Vietnamese text. Uses robust Unicode pattern."""
    return set(re.findall(r"(?i:điều|diều|điêu|đều)\s*(\d+[A-Za-z]?)", text))


def _gt_nums(gt_articles: list) -> set:
    nums = set()
    for a in gt_articles:
        m = re.search(r"(\d+[A-Za-z]?)", a)
        if m:
            nums.add(m.group(1))
    return nums


def _gt_nums_from_text(verdict_text: str) -> set:
    """Extract article numbers directly from the final_verdict free text."""
    return set(re.findall(r"(?i:điều|diều|điêu|đều)\s*(\d+[A-Za-z]?)",
                          verdict_text))


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


def _article_num(s: str) -> Optional[str]:
    """Extract article number from strings like 'Điều 173' or 'Điều 173 khoản 2'."""
    m = re.search(r"(\d+[A-Za-z]?)", str(s))
    return m.group(1) if m else None


# ── LAYER 1: Article Existence ─────────────────────────────────────────────────
def layer1_article_existence(mapped_laws: list, gt_nums: set,
                              valid_corpus: set) -> dict:
    """
    Flag articles cited by the system that are:
      - NOT in the ground-truth verdict, AND
      - NOT in the always-valid procedural set, AND
      - NOT in the full BLHS corpus (i.e., article doesn't exist at all)
    """
    false_arts = []
    for law in mapped_laws:
        if law.get("_mapping_error"):
            continue
        num = _article_num(law.get("article",""))
        if not num:
            continue
        if num in _ALWAYS_VALID:
            continue
        if num not in gt_nums and num not in valid_corpus:
            false_arts.append({
                "article": law.get("article",""),
                "reason": "not_in_corpus",
            })
        elif num not in gt_nums and num in valid_corpus:
            # Article exists in BLHS but court didn't apply it → weaker hallucination
            false_arts.append({
                "article": law.get("article",""),
                "reason": "not_in_verdict",
            })
    flagged = len(false_arts) > 0
    return {"triggered": flagged, "flagged": flagged, "false_articles": false_arts}


# ── LAYER 2: Edition / Retroactivity ──────────────────────────────────────────
def layer2_edition(mapped_laws: list, extracted_facts: dict) -> dict:
    """
    Check whether each mapped law uses the correct BLHS edition for the crime date.
    Uses Article 7 logic: apply edition in force at crime date,
    UNLESS a newer edition is more lenient (retroactivity exception).
    """
    crime_date_str = (extracted_facts or {}).get("ngay_pham_toi","")
    crime_date = _parse_date(crime_date_str) if crime_date_str else None
    if not crime_date:
        return {"triggered": False, "flagged": False, "details": [],
                "note": "crime_date_unavailable — cannot check retroactivity"}

    expected_edition = _edition_for_date(crime_date)
    errors = []
    for law in mapped_laws:
        if law.get("_mapping_error"):
            continue
        num = _article_num(law.get("article",""))
        if not num or num in _ALWAYS_VALID:
            continue
        applied = law.get("edition_applied","")
        if not applied or applied == "N/A":
            continue
        # Normalize
        applied_clean = applied.strip()
        if applied_clean != expected_edition:
            # Allow if it is a newer / more-lenient edition (retroactivity is valid)
            newer = _is_newer_edition(applied_clean, expected_edition)
            if not newer:
                errors.append({
                    "article":           law.get("article",""),
                    "expected_edition":  expected_edition,
                    "applied_edition":   applied_clean,
                    "crime_date":        crime_date_str,
                })

    triggered = len(errors) > 0
    return {"triggered": triggered, "flagged": triggered, "details": errors,
            "crime_date": crime_date_str, "expected_edition": expected_edition}


def _is_newer_edition(applied: str, expected: str) -> bool:
    """Return True if `applied` is a newer edition than `expected` (retroactivity ok)."""
    order = [r[0] for r in _EDITION_RANGES]
    try:
        return order.index(applied) > order.index(expected)
    except ValueError:
        return False


# ── LAYER 3: Sentencing Range Consistency ─────────────────────────────────────
def _parse_penalty_years(text: str) -> Optional[tuple]:
    """
    Extract (min_years, max_years) from Vietnamese penalty text.
    Returns None if no imprisonment range found.
    """
    t = text.lower()
    # "từ X tháng đến Y năm"
    m = re.search(r"t[ừu]\s*(\d+)\s*th[áa]ng\s*[đd][ếe]n\s*(\d+)\s*n[ăa]m", t)
    if m:
        return (int(m.group(1)) / 12, float(m.group(2)))
    # "từ X năm đến Y năm"
    m = re.search(r"t[ừu]\s*(\d+)\s*n[ăa]m\s*[đd][ếe]n\s*(\d+)\s*n[ăa]m", t)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    # "đến X năm" — only max given
    m = re.search(r"[đd][ếe]n\s*(\d+)\s*n[ăa]m", t)
    if m:
        return (0.0, float(m.group(1)))
    # chung thân / tử hình
    if "chung th" in t:
        return (20.0, float("inf"))
    if "t[ửu] h[ìi]nh" in t or "tử hình" in t:
        return (float("inf"), float("inf"))
    return None


def _ranges_consistent(stated: tuple, actual: tuple, tol: float = 1.5) -> bool:
    """True if stated range is within tolerance of actual range."""
    if stated is None or actual is None:
        return True  # can't determine → don't flag
    s_min, s_max = stated
    a_min, a_max = actual
    # Allow ±tol years on each bound
    min_ok = abs(s_min - a_min) <= tol
    max_ok = (a_max == float("inf") and s_max >= 15) or \
             (a_max != float("inf") and abs(s_max - a_max) <= tol)
    return min_ok and max_ok


def layer3_sentencing(response_text: str, gt_articles: list,
                      article_contents: dict) -> dict:
    """
    For the primary crime article, extract the actual sentencing range from
    article_content and the stated range from the response. Flag if inconsistent.
    """
    errors = []
    # Only check primary crime article (first non-procedural in ground truth)
    primary = next(
        (a for a in gt_articles
         if _article_num(a) and _article_num(a) not in _ALWAYS_VALID),
        None
    )
    if not primary:
        return {"triggered": False, "flagged": False, "details": [], "note": "no_primary_article"}

    content = article_contents.get(primary, "")
    if not content:
        return {"triggered": False, "flagged": False, "details": [],
                "note": f"no_article_content_for_{primary}"}

    actual_range  = _parse_penalty_years(content)
    stated_range  = _parse_penalty_years(response_text)

    if actual_range and stated_range:
        if not _ranges_consistent(stated_range, actual_range):
            errors.append({
                "article":      primary,
                "actual_range": actual_range,
                "stated_range": stated_range,
            })

    triggered = len(errors) > 0
    return {"triggered": triggered, "flagged": triggered, "details": errors,
            "primary_article": primary,
            "actual_range": actual_range, "stated_range": stated_range}


# ── LAYER 4: Factual Consistency (LLM judge) ──────────────────────────────────
_L4_PROMPT = """\
Given this Vietnamese criminal case description:
{case_description}

The REAL court's reasoning (Nhận định) and verdict (Quyết định) are:
--- COURT REASONING ---
{explanation}
--- COURT VERDICT ---
{final_verdict}

Does the following AI-generated legal analysis state any FACT that DIRECTLY CONTRADICTS
the case description or the court's actual verdict above?

Examples of contradictions:
- Wrong monetary amount, date, victim count, defendant count
- Invents a weapon, substance, or method not mentioned in the case
- States the defendant was acquitted when the verdict clearly shows a conviction
- Cites a completely wrong article number for the crime charged

AI Response to check:
{response}

Reply ONLY with this JSON and nothing else:
{{"contradiction": false, "example": ""}}
"""

def layer4_factual(client: OpenAI, model: str, case: dict,
                   response_text: str, log: logging.Logger) -> dict:
    prompt = _L4_PROMPT.format(
        case_description=case["case_description"][:1000],
        explanation=case["explanation"][:800],
        final_verdict=case["final_verdict"][:600],
        response=response_text[:1800],
    )
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content":
                     "You are a strict JSON classifier. Output ONLY the raw JSON object, "
                     "no explanation, no markdown fences, no prose."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=4096,  # Gemini 2.5 Pro thinking tokens count here
                extra_body={"thinking": {"type": "disabled"}},  # disable extended thinking
            )
            raw = (r.choices[0].message.content or "").strip()
            clean = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
            brace_m = re.search(r"\{.*\}", clean, re.DOTALL)
            if brace_m:
                clean = brace_m.group(0)
            data = json.loads(clean)
            triggered = bool(data.get("contradiction", False))
            return {
                "triggered": triggered,
                "flagged":   triggered,  # both keys for compatibility
                "example":   data.get("example", ""),
            }
        except Exception as e:
            log.warning(f"    L4 attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return {"triggered": False, "flagged": False, "example": "", "note": "l4_failed_after_retries"}


# ── Composite score ───────────────────────────────────────────────────────────
def composite(l1: dict, l2: dict, l3: dict, l4: dict) -> float:
    return (WEIGHTS["l1"] * int(l1["flagged"]) +
            WEIGHTS["l2"] * int(l2["flagged"]) +
            WEIGHTS["l3"] * int(l3["flagged"]) +
            WEIGHTS["l4"] * int(l4.get("flagged", False)))


# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="4-Layer hallucination evaluation for VNPLaw AI service."
    )
    parser.add_argument("--dataset",
                        default="ai-service/evaluation/thesis_eval_unique.json",
                        help="Path to case_eval_dataset.json")
    parser.add_argument("--output",   default="ai-service/evaluation/results/hallucination_results.jsonl")
    parser.add_argument("--summary",  default="ai-service/evaluation/results/hallucination_summary.json")
    parser.add_argument("--ai-url",   default=os.getenv("AI_SERVICE_URL", "http://localhost:8000"))
    parser.add_argument("--model",    default=os.getenv("LLM_JUDGE_MODEL", "google/gemini-2.5-pro"))
    parser.add_argument("--timeout",  type=int, default=120,
                        help="AI service request timeout in seconds (use ~60 on GPU)")
    parser.add_argument("--start",    type=int, default=1, help="First case (1-indexed, inclusive)")
    parser.add_argument("--end",      type=int, default=0, help="Last case (0=all)")
    parser.add_argument("--resume",   action="store_true", help="Skip already-processed cases")
    parser.add_argument("--skip-l4",  action="store_true", help="Skip Layer 4 LLM judge (faster, no API cost)")
    parser.add_argument("--log-file", default=None, help="Log file path (appended)")
    parser.add_argument("--delay",    type=float, default=0.5)
    args = parser.parse_args()

    log = setup_logging(args.log_file)
    log.info("=" * 70)
    log.info("VNPLaw Hallucination Evaluation — 4-Layer Framework")
    log.info(f"  AI service : {args.ai_url}")
    log.info(f"  L4 judge   : {'DISABLED (--skip-l4)' if args.skip_l4 else args.model}")
    log.info(f"  Timeout    : {args.timeout}s")
    log.info(f"  Range      : {args.start}–{'END' if not args.end else args.end}")
    log.info("=" * 70)

    # Load
    cases       = load_cases(args.dataset)
    valid_corpus = _valid_article_set(args.dataset)
    log.info(f"Cases loaded: {len(cases)}  |  Valid BLHS article nums: {len(valid_corpus)}")

    # Apply range
    s_idx = max(0, args.start - 1)
    e_idx = args.end if args.end else len(cases)
    cases = cases[s_idx:e_idx]
    log.info(f"After range filter: {len(cases)} cases to evaluate")

    # Resume
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done_urls: set = set()
    if args.resume and out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done_urls.add(json.loads(line)["case_url"])
                except Exception:
                    pass
        log.info(f"Resume: {len(done_urls)} cases already done.")

    oai = OpenAI(api_key=os.getenv("OPENROUTER_LLM_JUDGE_KEY") or os.getenv("OPENROUTER_API_KEY") or "missing",
                 base_url="https://openrouter.ai/api/v1")

    scores, l1_flags, l2_flags, l3_flags, l4_flags = [], [], [], [], []
    processed = 0

    with open(out_path, "a", encoding="utf-8") as out_f:
        for i, case in enumerate(tqdm(cases, desc="Evaluating", unit="case")):
            url     = case["case_url"]
            cidx    = s_idx + i + 1
            if url in done_urls:
                continue

            log.info(f"[{cidx}/{s_idx + len(cases)}] {url[-60:]}")

            # Call AI — send case_description (full facts) to /predict
            pred = call_predict(args.ai_url, case["case_description"], args.timeout, log)
            if not pred:
                log.warning("  Empty response — skipping case")
                continue

            result_text    = pred.get("result", "")
            mapped_laws    = pred.get("mapped_laws") or []
            extracted_facts = pred.get("extracted_facts") or {}

            # Ground-truth article numbers extracted from final_verdict text
            gt = _gt_nums_from_text(case["final_verdict"])

            # Run layers
            l1 = layer1_article_existence(mapped_laws, gt, valid_corpus)
            l2 = layer2_edition(mapped_laws, extracted_facts)
            l3 = layer3_sentencing(result_text,
                                   list(gt),
                                   case["article_contents"])
            l4 = (layer4_factual(oai, args.model, case, result_text, log)
                  if not args.skip_l4 else {"flagged": False, "note": "skipped"})

            score = composite(l1, l2, l3, l4)
            scores.append(score)
            l1_flags.append(int(l1["flagged"]))
            l2_flags.append(int(l2["flagged"]))
            l3_flags.append(int(l3["flagged"]))
            l4_flags.append(int(l4.get("flagged", False)))

            tag = "🚨 HALLUCINATED" if score > 0 else "✅ OK"
            log.info(f"  {tag}  score={score:.2f}  "
                     f"L1={l1['flagged']} L2={l2['flagged']} "
                     f"L3={l3['flagged']} L4={l4.get('flagged',False)}")

            if l1["flagged"]:
                log.debug(f"    L1 articles: {l1['false_articles']}")
            if l2["flagged"]:
                log.debug(f"    L2 edition errors: {l2['details']}")
            if l3["flagged"]:
                log.debug(f"    L3 sentencing: actual={l3.get('actual_range')} stated={l3.get('stated_range')}")
            if l4.get("flagged"):
                log.debug(f"    L4 contradiction: {l4.get('example','')[:120]}")

            row = {
                "case_index":            cidx,
                "case_url":              url,
                "ground_truth_articles": case["ground_truth_articles"],
                "mapped_laws_applied":   [law.get("article","") for law in mapped_laws
                                          if not law.get("_mapping_error")],
                "hallucination": {
                    "layer1_article_existence": l1,
                    "layer2_edition_wrong":     l2,
                    "layer3_sentencing_wrong":  l3,
                    "layer4_factual_conflict":  l4,
                    "composite_score":          round(score, 4),
                    "any_hallucination":        score > 0,
                },
            }
            out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_f.flush()
            processed += 1
            time.sleep(args.delay)

    # Summary
    def _rate(lst): return round(sum(lst) / len(lst), 4) if lst else None
    def _avg(lst):  return round(sum(lst) / len(lst), 4) if lst else None

    summary = {
        "meta": {
            "n_cases": processed,
            "case_range": f"{args.start}–{'END' if not args.end else args.end}",
            "l4_used": not args.skip_l4,
            "weights": WEIGHTS,
        },
        "hallucination_rate_binary": _rate([1 if s > 0 else 0 for s in scores]),
        "hallucination_rate_weighted": _avg(scores),
        "target_pass": _rate([1 if s > 0 else 0 for s in scores]) is not None and
                       _rate([1 if s > 0 else 0 for s in scores]) <= 0.10,
        "per_layer_rate": {
            "L1_article_existence":    _rate(l1_flags),
            "L2_edition_retroactivity": _rate(l2_flags),
            "L3_sentencing_range":     _rate(l3_flags),
            "L4_factual_consistency":  _rate(l4_flags),
        },
        "interpretation": {
            "binary":   "Fraction of cases with ANY hallucination layer triggered",
            "weighted": "Mean composite score (0=clean, 1=all 4 layers triggered)",
        },
    }

    sum_path = Path(args.summary)
    sum_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log.info("=" * 70)
    log.info(f"HALLUCINATION RESULTS — {processed} cases")
    log.info("=" * 70)
    p = lambda b: "✅ PASS" if b else "❌ FAIL"
    log.info(f"  Binary rate (any layer):  {summary['hallucination_rate_binary']}  "
             f"target≤0.10  {p(summary['target_pass'])}")
    log.info(f"  Weighted rate (0-1):      {summary['hallucination_rate_weighted']}")
    log.info(f"  L1 (wrong article):       {summary['per_layer_rate']['L1_article_existence']}")
    log.info(f"  L2 (wrong edition):       {summary['per_layer_rate']['L2_edition_retroactivity']}")
    log.info(f"  L3 (wrong sentencing):    {summary['per_layer_rate']['L3_sentencing_range']}")
    log.info(f"  L4 (factual conflict):    {summary['per_layer_rate']['L4_factual_consistency']}")
    log.info(f"  Details JSONL  → {out_path}")
    log.info(f"  Summary JSON   → {sum_path}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
