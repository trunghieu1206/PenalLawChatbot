#!/usr/bin/env python3
"""
eval_combined_hallucination_recall_role_adherence.py — Combined Evaluation Script
Combines Primary Recall, Hallucination (L1-L3), and Role Adherence metrics.
Compares System (RAG) vs Baseline (gemini-2.5-flash) to reduce LLM calls threefold.
L4 Hallucination is removed per request to minimize LLM judge cost.

HOW TO RUN EXAMPLES:
  # Run from anywhere — paths are resolved automatically
  python3 /root/PenalLawChatbot/ai-service/evaluation/eval_combined_hallucination_recall_role_adherence.py \
    --start 1 \
    --end 1 \
    --log-file /root/PenalLawChatbot/ai-service/logs/eval_chunk_1_10.txt

  # Resume a chunk if it was interrupted (skips already finished cases)
  python3 /root/PenalLawChatbot/ai-service/evaluation/eval_combined_hallucination_recall_role_adherence.py \
    --start 1 --end 50 --resume --log-file /root/PenalLawChatbot/ai-service/logs/eval_chunk_1_50.txt

OUTPUTS:
  combined_results.jsonl  — full raw data per case (for programmatic analysis)
  combined_summary.json   — final aggregated scores (JSON)
  combined_report.txt     — human-readable report with per-case details + final %
                            (download this file to review results offline)
"""

import os, json, sys, time, argparse, logging
from pathlib import Path
from tqdm import tqdm
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Project root = 3 levels up from this file:
# eval_combined...py  →  evaluation/  →  ai-service/  →  PenalLawChatbot/
_HERE = Path(__file__).resolve().parent          # .../ai-service/evaluation/
_AI_SERVICE = _HERE.parent                        # .../ai-service/
PROJECT_ROOT = _AI_SERVICE.parent                 # .../PenalLawChatbot/

# --- Tqdm Logging ---
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

# --- Imports from existing scripts ---
from eval_primary_recall import check_primary_hit, _extract_nums_from_text
from eval_hallucination import _valid_article_set, layer1_article_existence, layer2_edition, layer3_sentencing, _gt_nums
from eval_role_adherence import ROLE_SIGNALS, ROLE_LABELS, signal_score, llm_score, combined_score
from eval_rubric_common import call_baseline

# Procedural articles that are never the primary crime article
_PROCEDURAL = {
    "7", "28", "32", "34", "42", "45", "46", "47", "48", "49", "50",
    "51", "52", "53", "54", "55", "56", "57", "58", "59", "60", "65",
}

def load_all_cases(dataset_path: str) -> list:
    """
    Permissive loader — loads ALL cases from the dataset.
    Attempts to extract primary_article from final_verdict,
    but does NOT skip cases where extraction fails.
    (Unlike eval_primary_recall.load_cases which skips them.)
    """
    import re
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)

    cases = []
    for entry in data:
        final_text = entry.get("final_verdict", "")
        all_nums_raw = re.findall(
            r"(?i:điều|dieu|điêu|đều)\s*(\d+[A-Za-z]?)", final_text
        )
        # De-duplicate while preserving order
        seen: dict = {}
        for n in all_nums_raw:
            if n not in seen:
                seen[n] = None
        all_gt_nums = list(seen.keys())

        # Primary = first non-procedural article (may be None if verdict is missing/empty)
        primary_num = next(
            (n for n in all_gt_nums if n not in _PROCEDURAL), None
        )

        cases.append({
            "case_url":         entry.get("url", ""),
            "crime_type":       entry.get("crime_type", ""),
            "case_description": entry.get("case_description", ""),
            "question":         entry.get("case_description", ""),
            "final_verdict":    final_text,
            "primary_article":  f"Điều {primary_num}" if primary_num else "N/A",
            "primary_num":      primary_num,  # may be None — recall skipped for these
            "all_gt_articles":  [f"Điều {n}" for n in all_gt_nums],
        })
    return cases


def call_system(ai_url, question, role, timeout, log):
    try:
        r = requests.post(
            f"{ai_url.rstrip('/')}/predict",
            json={"case_content": question, "role": role, "conversation_history": []},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"  /predict failed: {e}")
        return {}

def composite_hallucination(l1, l2, l3):
    score = 0.0
    if l1 and l1.get("triggered"): score += 0.40
    if l2 and l2.get("triggered"): score += 0.30
    if l3 and l3.get("triggered"): score += 0.30
    return round(score, 4)

def evaluate_metrics(response_dict, case, gt_nums, valid_corpus, role,
                     oai_judge, judge_model, is_baseline, log):
    """Computes all 3 metrics for a given response. Returns a detailed result dict."""
    result_text = response_dict.get("result", response_dict.get("text", ""))

    if is_baseline:
        text_nums   = _extract_nums_from_text(result_text)
        mapped_laws = [{"article": f"Điều {n}"} for n in text_nums]
        extracted_facts = {}
    else:
        mapped_laws     = response_dict.get("mapped_laws") or []
        extracted_facts = response_dict.get("extracted_facts") or {}

    # 1. Primary Recall (skip if dataset has no primary article for this case)
    if case.get("primary_num"):
        recall = check_primary_hit(case["primary_num"], mapped_laws, result_text)
        recall_hit    = recall["hit"]
        recall_source = recall["source"]
        recall_cited  = recall["cited_nums"]
    else:
        recall_hit    = None  # N/A — no ground truth article in verdict
        recall_source = "n/a"
        recall_cited  = []

    # 2. Hallucination (L1-L3)
    l1 = layer1_article_existence(mapped_laws, gt_nums, valid_corpus)
    l2 = layer2_edition(mapped_laws, extracted_facts)
    l3 = layer3_sentencing(result_text, case["all_gt_articles"], {})
    hall_score = composite_hallucination(l1, l2, l3)

    # 3. Role Adherence
    sig        = signal_score(result_text, role)
    llm        = llm_score(oai_judge, judge_model, case["case_description"], result_text, role, log)
    role_score = combined_score(sig["score"], llm.get("score"))

    return {
        "recall":          recall_hit,    # None = N/A (no ground truth article)
        "recall_source":   recall_source,
        "recall_cited":    recall_cited,
        "hallucination":   hall_score,
        "hall_l1":         l1.get("triggered", False) if l1 else False,
        "hall_l2":         l2.get("triggered", False) if l2 else False,
        "hall_l3":         l3.get("triggered", False) if l3 else False,
        "role_adherence":  role_score,
        "role_sig_score":  sig["score"],
        "role_llm_score":  llm.get("score"),
        "text_preview":    result_text[:300],
    }

def _pct(val):
    return f"{val * 100:.1f}%"

def _print_case_report(report, cidx, total, case, role, sys_eval, base_eval):
    """Write a nicely formatted block for one case+role to both terminal and report file."""
    r_icon  = ("✅" if sys_eval["recall"] else "❌") if sys_eval["recall"] is not None else "➖"
    h_icon  = "✅" if sys_eval["hallucination"] == 0        else "⚠️ "
    ro_icon = "✅" if (sys_eval["role_adherence"] or 0) >= 0.7 else "⚠️ "

    br_icon  = ("✅" if base_eval["recall"] else "❌") if base_eval["recall"] is not None else "➖"
    bh_icon  = "✅" if base_eval["hallucination"] == 0        else "⚠️ "
    bro_icon = "✅" if (base_eval["role_adherence"] or 0) >= 0.7 else "⚠️ "

    report(f"  ┌─ [{cidx}/{total}]  Role: {role.upper()}  ──────────────────────────────────────")
    report(f"  │  Crime  : {case.get('crime_type', 'N/A')}")
    report(f"  │")
    report(f"  │  ── SYSTEM (RAG) ─────────────────────────────────────────────")
    
    # Show recall details explicitly
    sys_recall_text = "N/A (No GT)" if sys_eval['recall'] is None else ('HIT' if sys_eval['recall'] else 'MISS')
    report(f"  │  {r_icon} Recall        : {sys_recall_text}")
    report(f"  │       What was extracted in the final verdict in the origin law case: {case.get('primary_article', 'N/A')}")
    report(f"  │       The primary law article that the system used to generate the response: {', '.join(sys_eval.get('recall_cited', [])) or 'None'}")
    report(f"  │       Source of law document cited: {sys_eval.get('recall_source', 'N/A')}")
    
    report(f"  │  {h_icon} Hallucination : score={sys_eval['hallucination']}"
           f"  L1={sys_eval['hall_l1']}  L2={sys_eval['hall_l2']}  L3={sys_eval['hall_l3']}")
    report(f"  │  {ro_icon} Role Adherence: {sys_eval['role_adherence']}"
           f"  (signal={sys_eval['role_sig_score']}  llm={sys_eval['role_llm_score']})")
    report(f"  │  Response: {repr(sys_eval['text_preview'][:120])}")
    report(f"  │")
    report(f"  │  ── BASELINE (no RAG) ────────────────────────────────────────")
    bas_recall_text = "N/A (No GT)" if base_eval['recall'] is None else ('HIT' if base_eval['recall'] else 'MISS')
    report(f"  │  {br_icon} Recall        : {bas_recall_text}")
    report(f"  │       What was extracted in the final verdict in the origin law case: {case.get('primary_article', 'N/A')}")
    report(f"  │       The primary law article that the baseline used to generate the response: {', '.join(base_eval.get('recall_cited', [])) or 'None'}")
    report(f"  │       Source of law document cited: {base_eval.get('recall_source', 'N/A')}")
    report(f"  │  {bh_icon} Hallucination : score={base_eval['hallucination']}")
    report(f"  │  {bro_icon} Role Adherence: {base_eval['role_adherence']}")
    report(f"  └──────────────────────────────────────────────────────────────")

def _print_running_totals(report, metrics, processed):
    """Print running % after every case."""
    n = metrics["total_evals"]
    if n == 0:
        return
    s = metrics["system"]
    b = metrics["baseline"]
    def _avg(lst): return sum(lst)/len(lst) if lst else 0.0

    sys_recall = s["recall_hits"] / n
    sys_hall   = _avg(s["hallucination_scores"])
    sys_role   = _avg(s["role_scores"])
    bas_recall = b["recall_hits"] / n
    bas_hall   = _avg(b["hallucination_scores"])
    bas_role   = _avg(b["role_scores"])

    report(f"  📊 Running totals after {processed} case(s)  ({n} role evals)")
    report(f"     {'Metric':<22} {'System':>9} {'Baseline':>9}  Target")
    report(f"     {'-'*55}")
    report(f"     {'Primary Recall':<22} {_pct(sys_recall):>9} {_pct(bas_recall):>9}  ≥90%  "
           f"{'✅' if sys_recall >= 0.90 else '❌'}")
    report(f"     {'Hallucination Rate':<22} {_pct(sys_hall):>9} {_pct(bas_hall):>9}  ≤10%  "
           f"{'✅' if sys_hall <= 0.10 else '❌'}")
    report(f"     {'Role Adherence':<22} {_pct(sys_role):>9} {_pct(bas_role):>9}  ≥85%  "
           f"{'✅' if sys_role >= 0.85 else '❌'}")

def main():
    parser = argparse.ArgumentParser(description="Combined Evaluation Script")
    parser.add_argument("--dataset",        default=str(PROJECT_ROOT / "ai-service/evaluation/thesis_eval_1000.json"))
    parser.add_argument("--output",         default=str(PROJECT_ROOT / "ai-service/evaluation/results/combined_results.jsonl"))
    parser.add_argument("--summary",        default=str(PROJECT_ROOT / "ai-service/evaluation/results/combined_summary.json"))
    parser.add_argument("--report",         default=str(PROJECT_ROOT / "ai-service/evaluation/results/combined_report.txt"),
                        help="Human-readable report file — download this to view results offline.")
    parser.add_argument("--ai-url",         default=os.getenv("AI_SERVICE_URL", "http://localhost:8000"))
    parser.add_argument("--judge-model",    default=os.getenv("LLM_JUDGE_MODEL", "google/gemini-2.5-pro"))
    parser.add_argument("--baseline-model", default=os.getenv("LLM_MODEL", "google/gemini-2.5-flash"))
    parser.add_argument("--timeout",        type=int,   default=120)
    parser.add_argument("--start",          type=int,   default=1)
    parser.add_argument("--end",            type=int,   default=0)
    parser.add_argument("--resume",         action="store_true")
    parser.add_argument("--log-file",       default=None)
    parser.add_argument("--delay",          type=float, default=0.5)
    args = parser.parse_args()

    log = setup_logging(args.log_file)

    # Human-readable report file (downloadable)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_fh = open(report_path, "a", encoding="utf-8")

    def report(line=""):
        """Write to terminal (via log.info) AND to the report file simultaneously."""
        log.info(line)
        report_fh.write(line + "\n")
        report_fh.flush()

    report("=" * 70)
    report("VNPLaw Combined Evaluation  (Recall · Hallucination L1-L3 · Role Adherence)")
    report(f"  AI service    : {args.ai_url}")
    report(f"  Judge model   : {args.judge_model}")
    report(f"  Baseline model: {args.baseline_model}")
    report(f"  Case range    : {args.start} – {'END' if not args.end else args.end}")
    report("=" * 70)

    cases = load_all_cases(args.dataset)
    valid_corpus = _valid_article_set(args.dataset)

    s_idx = max(0, args.start - 1)
    e_idx = args.end if args.end else len(cases)
    cases = cases[s_idx:e_idx]
    n_with_primary = sum(1 for c in cases[max(0, args.start-1):(args.end if args.end else len(cases))] if c["primary_num"])
    report(f"Cases to evaluate: {len(cases[max(0, args.start-1):(args.end if args.end else len(cases))])}  ({n_with_primary} have primary article for recall)")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done_urls = set()
    if args.resume and out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try: done_urls.add(json.loads(line)["case_url"])
                except: pass
        report(f"Resume mode: {len(done_urls)} cases already done — skipping.")

    # OpenRouter requires HTTP-Referer and X-Title headers, otherwise some models silently return empty content.
    or_headers = {
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "VNPLaw Eval"
    }

    oai_judge    = OpenAI(
        api_key=os.getenv("OPENROUTER_LLM_JUDGE_KEY") or os.getenv("OPENROUTER_API_KEY") or "missing",
        base_url="https://openrouter.ai/api/v1",
        default_headers=or_headers,
    )
    oai_baseline = OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY") or "missing",
        base_url="https://openrouter.ai/api/v1",
        default_headers=or_headers,
    )

    metrics = {
        "system":   {"recall_hits": 0, "recall_total": 0, "hallucination_scores": [], "role_scores": []},
        "baseline": {"recall_hits": 0, "recall_total": 0, "hallucination_scores": [], "role_scores": []},
        "total_evals": 0,
    }
    processed = 0

    with open(out_path, "a", encoding="utf-8") as out_f:
        for i, case in enumerate(tqdm(cases, desc="Evaluating", unit="case")):
            url = case["case_url"]
            if url in done_urls:
                continue

            cidx    = s_idx + i + 1
            total   = s_idx + len(cases)
            gt_nums = _gt_nums(case["all_gt_articles"])

            report("")
            report(f"━━━ CASE {cidx}/{total} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            report(f"  URL: {url}")

            row = {"case_index": cidx, "case_url": url, "evaluations": {}}

            for role in ["neutral", "defense", "victim"]:
                sys_pred  = call_system(args.ai_url, case["case_description"], role, args.timeout, log)
                base_text = call_baseline(oai_baseline, args.baseline_model,
                                          case["case_description"], ROLE_LABELS[role], log)
                base_pred = {"text": base_text}

                sys_eval  = evaluate_metrics(sys_pred,  case, gt_nums, valid_corpus, role,
                                             oai_judge, args.judge_model, is_baseline=False, log=log)
                base_eval = evaluate_metrics(base_pred, case, gt_nums, valid_corpus, role,
                                             oai_judge, args.judge_model, is_baseline=True,  log=log)

                _print_case_report(report, cidx, total, case, role, sys_eval, base_eval)

                row["evaluations"][role] = {"system": sys_eval, "baseline": base_eval}

                metrics["total_evals"] += 1
                if sys_eval["recall"] is not None:
                    metrics["system"]["recall_hits"] += int(sys_eval["recall"])
                    metrics["system"]["recall_total"] += 1

                metrics["system"]["hallucination_scores"].append(sys_eval["hallucination"])
                metrics["system"]["role_scores"].append(sys_eval["role_adherence"])

                if base_eval["recall"] is not None:
                    metrics["baseline"]["recall_hits"] += int(base_eval["recall"])
                    metrics["baseline"]["recall_total"] += 1
                metrics["baseline"]["hallucination_scores"].append(base_eval["hallucination"])
                metrics["baseline"]["role_scores"].append(base_eval["role_adherence"])

                time.sleep(args.delay)

            out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_f.flush()
            processed += 1

            # Print running % after every case
            _print_running_totals(report, metrics, processed)

    # ── Final Summary ──────────────────────────────────────────────────────────
    def _avg(lst): return round(sum(lst) / len(lst), 4) if lst else 0.0

    n_evals    = metrics["total_evals"]
    sys_recall = round(metrics["system"]["recall_hits"]   / metrics["system"]["recall_total"],   4) if metrics["system"]["recall_total"]   else None
    sys_hall   = _avg(metrics["system"]["hallucination_scores"])
    sys_role   = _avg(metrics["system"]["role_scores"])
    bas_recall = round(metrics["baseline"]["recall_hits"] / metrics["baseline"]["recall_total"], 4) if metrics["baseline"]["recall_total"] else None
    bas_hall   = _avg(metrics["baseline"]["hallucination_scores"])
    bas_role   = _avg(metrics["baseline"]["role_scores"])

    summary = {
        "meta": {
            "n_cases_evaluated":    processed,
            "total_role_evaluations": n_evals,
            "case_range": f"{args.start}–{'END' if not args.end else args.end}",
        },
        "system":   {"primary_recall": sys_recall, "hallucination_rate": sys_hall, "role_adherence": sys_role},
        "baseline": {"primary_recall": bas_recall, "hallucination_rate": bas_hall, "role_adherence": bas_role},
        "pass": {
            "recall":        (sys_recall >= 0.90) if sys_recall is not None else None,
            "hallucination": sys_hall   <= 0.10,
            "role":          sys_role   >= 0.85,
        },
    }

    sum_path = Path(args.summary)
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    def _recall_str(v):
        return "N/A" if v is None else _pct(v)

    def _pass_str(key):
        v = summary["pass"][key]
        if v is None: return "➖ N/A"
        return "✅ PASS" if v else "❌ FAIL"

    # Rich final report block
    report("")
    report("=" * 70)
    report(f"  FINAL RESULTS — {processed} cases  ({n_evals} role evaluations)")
    report("=" * 70)
    report(f"  {'Metric':<22} {'System':>9} {'Baseline':>9}  {'Target':>8}  Pass?")
    report(f"  {'-'*58}")
    report(f"  {'Primary Recall':<22} {_recall_str(sys_recall):>9} {_recall_str(bas_recall):>9}  {'≥90%':>8}  {_pass_str('recall')}")
    report(f"  {'Hallucination Rate':<22} {_pct(sys_hall):>9} {_pct(bas_hall):>9}  {'≤10%':>8}  {_pass_str('hallucination')}")
    report(f"  {'Role Adherence':<22} {_pct(sys_role):>9} {_pct(bas_role):>9}  {'≥85%':>8}  {_pass_str('role')}")
    report(f"  {'-'*58}")
    report(f"  Detailed JSONL : {out_path}")
    report(f"  Summary JSON   : {sum_path}")
    report(f"  Human report   : {report_path}  ← download this file for offline review")
    report("=" * 70)

    report_fh.close()

if __name__ == "__main__":
    main()
