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

load_dotenv()

# ── Articles that are procedural/supporting — never the primary crime article ──
_PROCEDURAL = {
    "7",  "28", "32", "34", "42", "45", "46", "47", "48", "49", "50",
    "51", "52", "53", "54", "55", "56", "57", "58", "59", "60", "65",
}


# ── Logging ───────────────────────────────────────────────────────────────────
def setup_logging(log_file: Optional[str]) -> logging.Logger:
    log = logging.getLogger("primary_recall")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    log.addHandler(ch)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


# ── Dataset helpers ───────────────────────────────────────────────────────────
def _article_num(s: str) -> Optional[str]:
    """Extract numeric article identifier, e.g. '173' from 'Điều 173 - BLHS 2015...'"""
    m = re.search(r"(\d+[A-Za-z]?)", str(s))
    return m.group(1) if m else None


def _extract_nums_from_text(text: str) -> set:
    """Fallback: parse all Điều N mentions from free-text response."""
    return set(re.findall(
        r"[Ðđd][iíI][eêE][uU]\s*(\d+[A-Za-z]?)", text, re.IGNORECASE
    ))


def load_cases(test_path: str, full_path: str) -> list:
    """
    Build ordered case list. Each case has:
      - case_url
      - question          (longest question from test dataset)
      - primary_article   (first non-procedural article in full verdict)
      - all_gt_articles   (every article the court applied)
    """
    with open(test_path, encoding="utf-8") as f:
        test_data = json.load(f)
    with open(full_path, encoding="utf-8") as f:
        full_data = json.load(f)

    # Group by URL
    test_by_url: dict = defaultdict(list)
    for e in test_data:
        test_by_url[e["link_to_case"]].append(e)

    full_by_url: dict = defaultdict(list)
    for e in full_data:
        full_by_url[e["link_to_case"]].append(e)

    # Stable order from test dataset
    seen: dict = {}
    for e in test_data:
        url = e["link_to_case"]
        if url not in seen:
            seen[url] = len(seen)

    cases = []
    for url, _ in sorted(seen.items(), key=lambda x: x[1]):
        te = test_by_url[url]
        fe = full_by_url.get(url, te)

        all_gt = [e["expected_article"] for e in fe]

        # Primary = first non-procedural article in the verdict
        primary = next(
            (a for a in all_gt
             if _article_num(a) and _article_num(a) not in _PROCEDURAL),
            None
        )

        if not primary:
            # Edge case: verdict only has procedural articles — skip
            continue

        question = max(te, key=lambda e: len(e["question"]))["question"]

        cases.append({
            "case_url":        url,
            "question":        question,
            "primary_article": primary,         # e.g. "Điều 173 - BLHS 2015 (sửa đổi 2017)"
            "primary_num":     _article_num(primary),  # e.g. "173"
            "all_gt_articles": all_gt,
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
      hit         : bool — system correctly identified the primary article
      source      : 'mapped_laws' | 'text_fallback' | 'miss'
      cited_nums  : list of article numbers the system cited
    """
    # Source 1: structured mapped_laws (preferred — reliable)
    mapped_nums = set()
    for law in mapped_laws:
        if law.get("_mapping_error"):
            continue
        num = _article_num(law.get("article", ""))
        if num:
            mapped_nums.add(num)

    if primary_num in mapped_nums:
        return {
            "hit":        True,
            "source":     "mapped_laws",
            "cited_nums": sorted(mapped_nums),
        }

    # Source 2: text regex fallback (in case mapped_laws is empty/failed)
    text_nums = _extract_nums_from_text(response_text)
    if primary_num in text_nums:
        return {
            "hit":        True,
            "source":     "text_fallback",
            "cited_nums": sorted(text_nums),
        }

    return {
        "hit":        False,
        "source":     "miss",
        "cited_nums": sorted(mapped_nums or text_nums),
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Primary Article Recall evaluation for VNPLaw AI service."
    )
    parser.add_argument("--test-dataset",
                        default="ai-service/scraped_datasets/toaan_gov_test_datasets.json")
    parser.add_argument("--full-dataset",
                        default="ai-service/scraped_datasets/toaan_gov_datasets.json")
    parser.add_argument("--output",
                        default="ai-service/evaluation/results/primary_recall_results.jsonl")
    parser.add_argument("--summary",
                        default="ai-service/evaluation/results/primary_recall_summary.json")
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
    cases = load_cases(args.test_dataset, args.full_dataset)
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
        for i, case in enumerate(cases):
            url      = case["case_url"]
            cidx     = s_idx + i + 1

            if url in done_urls:
                continue

            log.info(f"[{cidx}/{s_idx + len(cases)}]  primary={case['primary_article']}")
            log.debug(f"  URL: {url[-60:]}")

            # Call AI service
            pred = call_predict(args.ai_url, case["question"], args.timeout, log)
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
