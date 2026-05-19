#!/usr/bin/env python3
"""
eval_rubric_neutral.py — RUBRIC evaluation for Judge / Thẩm phán (neutral) role.

Ground truth: Court's Nhận định (from scraped_texts.json) + all articles applied.
6 dimensions: D1 Legal Accuracy, D2 Reasoning Alignment, D3 Circumstance Coverage,
              D4 Sentencing Consistency, D5 Judicial Neutrality, D6 Clarity & Precision.

RUN:
  python3 eval_rubric_neutral.py --start 1 --end 100 --log-file neutral_1_100.txt
  python3 eval_rubric_neutral.py --start 1 --end 100 --skip-judge  # responses only
"""
import sys, os, logging
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))
from eval_rubric_common import (
    setup_logging, run_rubric_eval, base_arg_parser, get_nhan_dinh
)

ROLE       = "neutral"
ROLE_LABEL = "Thẩm phán trung lập (neutral judge)"

DIMENSIONS = [
    "D1_legal_accuracy",
    "D2_reasoning_alignment",
    "D3_circumstance_coverage",
    "D4_sentencing_consistency",
    "D5_judicial_neutrality",
    "D6_clarity_precision",
]

_PROMPT = """\
You are an expert Vietnamese criminal law professor evaluating an AI judge assistant.

## CASE FACTS (fed into the system)
{question}

## COURT'S ACTUAL NHẬN ĐỊNH (real judge's reasoning — PRIMARY ground truth)
{nhan_dinh}

## ALL ARTICLES APPLIED BY COURT
{gt_articles}

## RESPONSE BEING EVALUATED (neutral/judge role)
{response}

## BASELINE RESPONSE (direct LLM, no RAG — context only, do NOT score)
{baseline}

---
Using the COURT'S NHẬN ĐỊNH as your primary benchmark, score the EVALUATED RESPONSE (1–5 each):

D1_legal_accuracy:          articles match Nhận định? (1=all wrong, 5=all correct+edition)
D2_reasoning_alignment:     reasoning follows court logic? (1=contradicts, 5=mirrors)
D3_circumstance_coverage:   mitigating+aggravating from Nhận định covered? (1=none, 5=all)
D4_sentencing_consistency:  sentencing matches court decision? (1=opposite, 5=exact khoản+range)
D5_judicial_neutrality:     neutral judge perspective? (1=one-sided, 5=perfectly neutral)
D6_clarity_precision:       clear + precise legal language? (1=incoherent, 5=professional)

Return ONLY this JSON:
{{"D1_legal_accuracy":0,"D2_reasoning_alignment":0,"D3_circumstance_coverage":0,\
"D4_sentencing_consistency":0,"D5_judicial_neutrality":0,"D6_clarity_precision":0,\
"total":0,"normalized":0.0,"key_gaps":""}}
"""

def build_context(case: dict) -> dict:
    return {
        "nhan_dinh":    case.get("explanation") or get_nhan_dinh(case["case_url"]),
        "final_verdict": case.get("final_verdict", ""),
    }

def build_prompt(question: str, case: dict, ctx: dict,
                 response: str, baseline: str) -> str:
    return _PROMPT.format(
        question=question[:1200],
        nhan_dinh=ctx["nhan_dinh"][:2000],
        gt_articles=ctx["final_verdict"][:800],
        response=response[:2000],
        baseline=baseline[:1200],
    )

def main():
    parser = base_arg_parser("RUBRIC evaluation — Judge (neutral) role.")
    args = parser.parse_args()
    _results_dir = Path(__file__).resolve().parent / "results"
    if not args.output:
        args.output  = str(_results_dir / "rubric_neutral_results.jsonl")
    if not args.summary:
        args.summary = str(_results_dir / "rubric_neutral_summary.json")

    log = setup_logging(args.log_file, "rubric_neutral")
    log.info("=" * 70)
    log.info("VNPLaw — RUBRIC Evaluation: Judge / Neutral Role")
    log.info(f"  AI service : {args.ai_url}  |  Judge model: {args.model}")
    log.info(f"  Range      : cases {args.start}–{'END' if not args.end else args.end}")
    log.info("=" * 70)

    run_rubric_eval(
        role=ROLE,
        role_label=ROLE_LABEL,
        dimensions=DIMENSIONS,
        build_prompt_fn=build_prompt,
        build_context_fn=build_context,
        args=args,
        log=log,
    )

if __name__ == "__main__":
    main()
