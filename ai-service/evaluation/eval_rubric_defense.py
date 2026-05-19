#!/usr/bin/env python3
"""
eval_rubric_defense.py — RUBRIC evaluation for Defense Lawyer (Luật sư bào chữa) role.

Ground truth: case facts + mitigating articles (Điều 51/54/65) + primary crime article.
6 dimensions: D1 Mitigating Article Accuracy, D2 Defense Argument Strength,
              D3 Mitigating Circumstance ID, D4 Sentencing Minimization,
              D5 Defense Perspective Consistency, D6 Clarity & Precision.

RUN:
  python3 eval_rubric_defense.py --start 1 --end 100 --log-file defense_1_100.txt
  python3 eval_rubric_defense.py --start 1 --end 100 --skip-judge  # responses only
"""
import sys, os, logging
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))
from eval_rubric_common import setup_logging, run_rubric_eval, base_arg_parser

ROLE       = "defense"
ROLE_LABEL = "Luật sư bào chữa (defense lawyer — bảo vệ quyền lợi bị cáo)"

DIMENSIONS = [
    "D1_mitigating_article_accuracy",
    "D2_defense_argument_strength",
    "D3_mitigating_circumstance_id",
    "D4_sentencing_minimization",
    "D5_defense_perspective_consistency",
    "D6_clarity_precision",
]

_PROMPT = """\
You are an expert Vietnamese criminal law professor evaluating an AI defense lawyer.

## CASE FACTS
{question}

## PRIMARY CRIME ARTICLE (what the defendant is charged under)
{primary_article}

## MITIGATING ARTICLES FROM COURT VERDICT (ground truth)
{mitigating_arts}

## ALL ARTICLES APPLIED BY COURT
{gt_articles}

## RESPONSE BEING EVALUATED (defense/bào chữa role)
{response}

## BASELINE RESPONSE (direct LLM, no RAG — context only, do NOT score)
{baseline}

---
Evaluate how effectively the EVALUATED RESPONSE defends the accused. Score (1–5 each):

D1_mitigating_article_accuracy:     correct mitigating articles cited? (1=none/wrong, 5=all Điều 51/54/65 correct)
D2_defense_argument_strength:       compelling legal defense? (1=weak/absent, 5=facts→articles→sentence reduction)
D3_mitigating_circumstance_id:      all mitigating factors from case found? (1=none, 5=all linked to Điều 51 clauses)
D4_sentencing_minimization:         argues for lightest outcome? (1=accepts max, 5=án treo/below-min if applicable)
D5_defense_perspective_consistency: stays in defense role? (1=argues against client, 5=fully advocates)
D6_clarity_precision:               professional defense brief? (1=incoherent, 5=clause+edition+formal language)

Return ONLY this JSON:
{{"D1_mitigating_article_accuracy":0,"D2_defense_argument_strength":0,\
"D3_mitigating_circumstance_id":0,"D4_sentencing_minimization":0,\
"D5_defense_perspective_consistency":0,"D6_clarity_precision":0,\
"total":0,"normalized":0.0,"key_gaps":""}}
"""

def build_context(case: dict) -> dict:
    return {}  # no extra context needed for defense

def build_prompt(question: str, case: dict, ctx: dict,
                 response: str, baseline: str) -> str:
    gt = "\n".join(f"  • {a}" for a in case["all_gt_articles"])
    mit = "\n".join(f"  • {a}" for a in case["mitigating_arts"]) or "  (none recorded)"
    return _PROMPT.format(
        question=question[:1200],
        primary_article=case["primary_article"] or "(unknown)",
        mitigating_arts=mit,
        gt_articles=gt,
        response=response[:2000],
        baseline=baseline[:1200],
    )

def main():
    parser = base_arg_parser("RUBRIC evaluation — Defense lawyer (bào chữa) role.")
    args = parser.parse_args()
    _results_dir = Path(__file__).resolve().parent / "results"
    if not args.output:
        args.output  = str(_results_dir / "rubric_defense_results.jsonl")
    if not args.summary:
        args.summary = str(_results_dir / "rubric_defense_summary.json")

    log = setup_logging(args.log_file, "rubric_defense")
    log.info("=" * 70)
    log.info("VNPLaw — RUBRIC Evaluation: Defense Lawyer Role")
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
