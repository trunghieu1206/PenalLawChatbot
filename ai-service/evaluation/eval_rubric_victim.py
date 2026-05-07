#!/usr/bin/env python3
"""
eval_rubric_victim.py — RUBRIC evaluation for Victim's Lawyer (Luật sư bảo vệ bị hại) role.

Ground truth: case facts + aggravating articles (Điều 52) + primary crime article.
6 dimensions: D1 Aggravating Article Accuracy, D2 Victim Advocacy Strength,
              D3 Aggravating Circumstance ID, D4 Maximum Sentencing Argument,
              D5 Victim Perspective Consistency, D6 Civil Compensation Coverage.

RUN:
  python3 eval_rubric_victim.py --start 1 --end 100 --log-file victim_1_100.txt
  python3 eval_rubric_victim.py --start 1 --end 100 --skip-judge  # responses only
"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(__file__))
from eval_rubric_common import setup_logging, run_rubric_eval, base_arg_parser

ROLE       = "victim"
ROLE_LABEL = "Luật sư bảo vệ bị hại (victim's lawyer — bảo vệ quyền lợi người bị hại)"

DIMENSIONS = [
    "D1_aggravating_article_accuracy",
    "D2_victim_advocacy_strength",
    "D3_aggravating_circumstance_id",
    "D4_maximum_sentencing_argument",
    "D5_victim_perspective_consistency",
    "D6_civil_compensation_coverage",
]

_PROMPT = """\
You are an expert Vietnamese criminal law professor evaluating an AI victim's lawyer.

## CASE FACTS
{question}

## PRIMARY CRIME ARTICLE (what the defendant was convicted under)
{primary_article}

## AGGRAVATING ARTICLES FROM COURT VERDICT (ground truth)
{aggravating_arts}

## ALL ARTICLES APPLIED BY COURT
{gt_articles}

## RESPONSE BEING EVALUATED (victim/bị hại role)
{response}

## BASELINE RESPONSE (direct LLM, no RAG — context only, do NOT score)
{baseline}

---
Evaluate how effectively the EVALUATED RESPONSE advocates for the victim. Score (1–5 each):

D1_aggravating_article_accuracy:  correct aggravating articles cited? (1=none/wrong, 5=all Điều 52 clauses correct)
D2_victim_advocacy_strength:      compelling victim advocacy? (1=neutral/absent, 5=facts→articles→max sentence+compensation)
D3_aggravating_circumstance_id:   all aggravating factors found? (1=none, 5=all linked to Điều 52 clauses)
D4_maximum_sentencing_argument:   argues for harshest applicable outcome? (1=implies leniency, 5=highest khoản+against án treo)
D5_victim_perspective_consistency: stays in victim advocate role? (1=defends accused, 5=fully advocates for victim)
D6_civil_compensation_coverage:   covers bồi thường thiệt hại? (1=not mentioned, 5=quantified+legal basis cited)

Return ONLY this JSON:
{{"D1_aggravating_article_accuracy":0,"D2_victim_advocacy_strength":0,\
"D3_aggravating_circumstance_id":0,"D4_maximum_sentencing_argument":0,\
"D5_victim_perspective_consistency":0,"D6_civil_compensation_coverage":0,\
"total":0,"normalized":0.0,"key_gaps":""}}
"""

def build_context(case: dict) -> dict:
    return {}  # no extra context needed for victim

def build_prompt(question: str, case: dict, ctx: dict,
                 response: str, baseline: str) -> str:
    gt  = "\n".join(f"  • {a}" for a in case["all_gt_articles"])
    agg = "\n".join(f"  • {a}" for a in case["aggravating_arts"]) or "  (none recorded)"
    return _PROMPT.format(
        question=question[:1200],
        primary_article=case["primary_article"] or "(unknown)",
        aggravating_arts=agg,
        gt_articles=gt,
        response=response[:2000],
        baseline=baseline[:1200],
    )

def main():
    parser = base_arg_parser("RUBRIC evaluation — Victim's lawyer (bị hại) role.")
    args = parser.parse_args()
    if not args.output:
        args.output  = "ai-service/evaluation/results/rubric_victim_results.jsonl"
    if not args.summary:
        args.summary = "ai-service/evaluation/results/rubric_victim_summary.json"

    log = setup_logging(args.log_file, "rubric_victim")
    log.info("=" * 70)
    log.info("VNPLaw — RUBRIC Evaluation: Victim's Lawyer Role")
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
