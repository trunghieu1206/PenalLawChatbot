#!/usr/bin/env python3
"""
eval_primary_recall.py — Measures Primary Article Recall for VNPLaw AI service.

PRIMARY ARTICLE = the BLHS article that DEFINES the crime (tội danh) — the
non-procedural article whose name matches the charge (e.g., Điều 173 = tội trộm cắp).

METRIC:
  primary_recall = cases where system correctly identified the primary crime article
                   ─────────────────────────────────────────────────────────────────
                                    total cases evaluated

  Pass threshold: ≥ 0.90

HOW IT WORKS:
  1. Ground truth primary = first non-procedural article in the court verdict
     (from toaan_gov_datasets.json, grouped by case URL)
  2. System call → /predict (role=neutral) → mapped_laws [structured JSON]
  3. Hit if primary article number appears in mapped_laws.
     Fallback: check free-text response with regex (in case mapped_laws is empty).

RUN ON THE GPU SERVER (ai-service host):
  # Cases 1–100, log to file:
  python3 eval_primary_recall.py --start 1 --end 100 --log-file recall_1_100.txt

  # All cases:
  python3 eval_primary_recall.py --log-file recall_all.txt

  # Resume after interruption:
  python3 eval_primary_recall.py --start 1 --end 100 --resume --log-file recall_1_100.txt
"""

import os, json, re, sys, time, argparse, logging
from pathlib import Path
from typing import Optional
from collections import defaultdict

import requests
from dotenv import load_dotenv

# Load .env from project root (3 levels up: eval/ → ai-service/ → PenalLawChatbot/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)

_RESULTS_DIR = Path(__file__).resolve().parent / "results"

# ── Articles that are procedural/supporting — never the primary crime article ──
_PROCEDURAL = {
    "7",  "28", "32", "34", "42", "45", "46", "47", "48", "49", "50",
    "51", "52", "53", "54", "55", "56", "57", "58", "59", "60", "65",
}

# Known BLTTHS article numbers — must never be treated as BLHS crime articles
_BLTTHS_ARTICLES = {
    "298", "299", "300", "301", "302", "303", "304", "305",
    "306", "307", "308", "309", "310", "311", "312", "313",
    "314", "315", "316", "317", "318", "319", "320", "321",
    "322", "323", "324", "325", "326", "327", "328", "329", "330",
    "331", "332", "333", "334", "335", "336", "337", "338", "339",
    "340", "341", "342", "343", "344", "345", "346", "347", "348",
    "349", "350", "351", "352", "353", "354", "355", "356", "357",
    "358", "359", "360", "361", "362", "363", "364", "365",
    "155", "156", "157", "158", "159", "160", "161", "162", "163",
    "165", "170", "172", "176", "179", "185", "195",
    "248", "249", "250", "252", "255", "256", "258", "259", "260",
}

_BLTTHS_MARKERS = ["tố tụng hình sự", "bltths", "b.l.t.t.h.s", "luật tố tụng"]
_BLHS_MARKERS   = ["bộ luật hình sự", "blhs", "b.l.h.s", "luật hình sự"]


def _extract_blhs_articles(text: str):
    """Extract only BLHS (penal code) article numbers from verdict text,
    filtering out BLTTHS (procedural code) citations by context analysis.
    Window is ±300 chars to handle long multi-article citation lines like:
      'Điều 134; điểm b,e,s khoản 1,2 Điều 51; Điều 38 Bộ luật hình sự'"""
    t_low = text.lower()
    seen: dict = {}
    for m in re.finditer(r"(?:đi[eề]u|dieu)\s*(\d+[a-z]?)", t_low):
        num = m.group(1)
        win = t_low[max(0, m.start()-300):min(len(t_low), m.end()+300)]
        if any(mk in win for mk in _BLTTHS_MARKERS):
            continue
        if any(mk in win for mk in _BLHS_MARKERS):
            if num not in seen:
                seen[num] = None
            continue
        if num in _BLTTHS_ARTICLES or num in _PROCEDURAL:
            continue
        if num not in seen:
            seen[num] = None
    return list(seen.keys())


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
    log = logging.getLogger("primary_recall")
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


# ── Dataset helpers ───────────────────────────────────────────────────────────
def _article_num(s: str):
    """Extract numeric article identifier, e.g. '173' from 'Điều 173 - BLHS 2015...'"""
    m = re.search(r"(\d+[A-Za-z]?)", str(s))
    return m.group(1) if m else None


def _extract_nums_from_text(text: str) -> set:
    """Fallback: parse all Điều N mentions from free-text response."""
    return set(re.findall(
        r"(?i:điều|diều|điêu|đều)\s*(\d+[A-Za-z]?)", text
    ))



def load_cases(dataset_path: str, _unused: str = "") -> list:
    """
    Build ordered case list from case_eval_dataset.json.
    Uses BLHS-aware article extractor to filter out BLTTHS procedural citations.
    """
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)

    cases = []
    for entry in data:
        url         = entry.get("url", "")
        final_text  = entry.get("final_verdict", "")

        all_gt_nums = _extract_blhs_articles(final_text)

        # Primary = first article not in procedural/BLTTHS lists
        primary_num = next(
            (n for n in all_gt_nums if n not in _PROCEDURAL and n not in _BLTTHS_ARTICLES), None
        )
        if not primary_num:
            continue  # skip: no identifiable crime article in verdict

        cases.append({
            "case_url":          url,
            "crime_type":        entry.get("crime_type", ""),
            "case_description":  entry.get("case_description", ""),
            "final_verdict":     final_text,
            "primary_article":   f"Điều {primary_num}",
            "primary_num":       primary_num,
            "all_gt_articles":   [f"Điều {n}" for n in all_gt_nums],
        })
    return cases


# ── AI service call ───────────────────────────────────────────────────────────
def call_predict(ai_url: str, question: str, timeout: int,
                 log: logging.Logger) -> dict:
    try:
        r = requests.post(
            f"{ai_url.rstrip('/')}/predict",
            json={"case_content": question, "role": "neutral",
                  "conversation_history": []},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"  /predict failed: {e}")
        return {}


# ── Primary article check ─────────────────────────────────────────────────────
def check_primary_hit(primary_num: str, mapped_laws: list,
                      response_text: str) -> dict:
    """
    Returns dict with:
      hit             : bool — system correctly identified the primary article
      source          : 'mapped_laws' | 'text_fallback' | 'miss'
      cited_nums      : list of article numbers the system cited
      document_source : name of the law document the matching article came from
    """
    # Source 1: structured mapped_laws (preferred — reliable)
    mapped_nums = set()
    document_source: Optional[str] = None
    _DOC_FIELDS = ("law_name", "source_document", "law_title", "name",
                   "_law_name", "document", "source", "title")

    for law in mapped_laws:
        if law.get("_mapping_error"):
            continue
        num = _article_num(law.get("article", ""))
        if num:
            mapped_nums.add(num)
            # Capture document name from the entry that matches the primary article
            if num == primary_num and document_source is None:
                for field in _DOC_FIELDS:
                    val = law.get(field)
                    if val and isinstance(val, str) and val.strip():
                        document_source = val.strip()
                        break

    if primary_num in mapped_nums:
        return {
            "hit":             True,
            "source":          "mapped_laws",
            "cited_nums":      sorted(mapped_nums),
            "document_source": document_source or "(source field unavailable)",
        }

    # Source 2: text regex fallback (in case mapped_laws is empty/failed)
    text_nums = _extract_nums_from_text(response_text)
    if primary_num in text_nums:
        return {
            "hit":             True,
            "source":          "text_fallback",
            "cited_nums":      sorted(text_nums),
            "document_source": "(extracted from free text — no structured source)",
        }

    return {
        "hit":             False,
        "source":          "miss",
        "cited_nums":      sorted(mapped_nums or text_nums),
        "document_source": None,
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Primary Article Recall evaluation for VNPLaw AI service."
    )
    parser.add_argument("--dataset",
                        default=str(_PROJECT_ROOT / "ai-service/evaluation/thesis_eval_unique.json"),
                        help="Path to case_eval_dataset.json")
    parser.add_argument("--output",
                        default=str(_RESULTS_DIR / "primary_recall_results.jsonl"))
    parser.add_argument("--summary",
                        default=str(_RESULTS_DIR / "primary_recall_summary.json"))
    parser.add_argument("--ai-url",
                        default=os.getenv("AI_SERVICE_URL", "http://localhost:8000"))
    parser.add_argument("--timeout",  type=int, default=120,
                        help="Request timeout in seconds (60 on GPU, 300+ on CPU)")
    parser.add_argument("--start",    type=int, default=1,
                        help="First case to evaluate (1-indexed, inclusive)")
    parser.add_argument("--end",      type=int, default=0,
                        help="Last case to evaluate (0 = all)")
    parser.add_argument("--resume",   action="store_true",
                        help="Skip cases already written to the output file")
    parser.add_argument("--log-file", default=None,
                        help="Append log output to this .txt file")
    parser.add_argument("--delay",    type=float, default=0.3,
                        help="Seconds to wait between cases")
    args = parser.parse_args()

    log = setup_logging(args.log_file)
    log.info("=" * 70)
    log.info("VNPLaw — Primary Article Recall Evaluation")
    log.info(f"  AI service : {args.ai_url}")
    log.info(f"  Timeout    : {args.timeout}s")
    log.info(f"  Range      : cases {args.start}–{'END' if not args.end else args.end}")
    log.info(f"  Pass goal  : primary_recall ≥ 0.90")
    log.info("=" * 70)

    # Load datasets
    cases = load_cases(args.dataset)
    log.info(f"Total cases with identifiable primary article: {len(cases)}")

    # Apply range (1-indexed)
    s_idx = max(0, args.start - 1)
    e_idx = args.end if args.end else len(cases)
    cases = cases[s_idx:e_idx]
    log.info(f"After range filter: {len(cases)} cases")

    # Resume: read already-done URLs
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
        log.info(f"Resume mode: {len(done_urls)} cases already done.")

    hits          = []  # 1=hit, 0=miss per case
    mapped_hits   = []  # hits found via mapped_laws
    text_hits     = []  # hits found via text fallback
    misses_detail = []  # cases that were misses — for analysis

    processed = 0

    with open(out_path, "a", encoding="utf-8") as out_f:
        for i, case in enumerate(tqdm(cases, desc="Evaluating", unit="case")):
            url      = case["case_url"]
            cidx     = s_idx + i + 1

            if url in done_urls:
                continue

            log.info(f"[{cidx}/{s_idx + len(cases)}]  primary={case['primary_article']}")
            log.debug(f"  URL: {url[-60:]}")

            # Call AI — send full case facts as the prompt
            pred = call_predict(args.ai_url, case["case_description"], args.timeout, log)
            if not pred:
                log.warning("  Empty response — skipping")
                continue

            result_text = pred.get("result", "")
            mapped_laws = pred.get("mapped_laws") or []

            # Check hit
            result = check_primary_hit(
                case["primary_num"], mapped_laws, result_text
            )

            hit  = result["hit"]
            src  = result["source"]
            hits.append(int(hit))
            if hit and src == "mapped_laws":
                mapped_hits.append(1)
            elif hit and src == "text_fallback":
                text_hits.append(1)

            if hit:
                log.info(f"  ✅ HIT  (via {src})  cited={result['cited_nums']}")
            else:
                log.info(f"  ❌ MISS  primary={case['primary_num']}  "
                         f"cited={result['cited_nums']}")
                misses_detail.append({
                    "case_index":      cidx,
                    "case_url":        url,
                    "primary_article": case["primary_article"],
                    "cited_nums":      result["cited_nums"],
                })

            row = {
                "case_index":      cidx,
                "case_url":        url,
                "primary_article": case["primary_article"],
                "primary_num":     case["primary_num"],
                "all_gt_articles": case["all_gt_articles"],
                "system_cited":    result["cited_nums"],
                "hit":             hit,
                "hit_source":      src,
            }
            out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_f.flush()
            processed += 1
            time.sleep(args.delay)

    # ── Summary ───────────────────────────────────────────────────────────────
    n = len(hits)
    primary_recall = round(sum(hits) / n, 4) if n else None
    passed         = primary_recall is not None and primary_recall >= 0.90

    summary = {
        "meta": {
            "n_cases_evaluated": processed,
            "case_range": f"{args.start}–{'END' if not args.end else args.end}",
            "ai_url": args.ai_url,
        },
        "primary_recall": primary_recall,
        "target":         0.90,
        "pass":           passed,
        "breakdown": {
            "total_hits":         sum(hits),
            "total_misses":       n - sum(hits),
            "hits_via_mapped_laws":   len(mapped_hits),
            "hits_via_text_fallback": len(text_hits),
            "note": (
                "hits_via_text_fallback means mapped_laws was empty/failed — "
                "article found by regex in response text instead."
            ),
        },
        "misses": misses_detail,   # full list of missed cases for debugging
    }

    sum_path = Path(args.summary)
    sum_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    p = "✅ PASS" if passed else "❌ FAIL"
    log.info("=" * 70)
    log.info(f"PRIMARY ARTICLE RECALL RESULTS — {processed} cases")
    log.info("=" * 70)
    log.info(f"  Primary recall : {primary_recall}  target≥0.90  {p}")
    log.info(f"  Hits           : {sum(hits)} / {n}")
    log.info(f"    via mapped_laws   : {len(mapped_hits)}")
    log.info(f"    via text fallback : {len(text_hits)}")
    log.info(f"  Misses         : {n - sum(hits)}")
    log.info(f"  Details JSONL  → {out_path}")
    log.info(f"  Summary JSON   → {sum_path}")
    log.info("=" * 70)

    if misses_detail:
        log.info("MISSED CASES (primary article not found by system):")
        for m in misses_detail[:10]:  # show first 10
            log.info(f"  [{m['case_index']}] expected={m['primary_article']}  "
                     f"cited={m['cited_nums']}  {m['case_url'][-50:]}")
        if len(misses_detail) > 10:
            log.info(f"  ... and {len(misses_detail)-10} more (see summary JSON)")


if __name__ == "__main__":
    main()
