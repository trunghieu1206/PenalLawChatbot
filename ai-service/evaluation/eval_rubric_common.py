#!/usr/bin/env python3
"""
eval_rubric_common.py — Shared utilities for all 3 RUBRIC evaluation scripts.

DO NOT run this file directly. Import it from:
  eval_rubric_neutral.py  (Judge / Thẩm phán)
  eval_rubric_defense.py  (Defense / Luật sư bào chữa)
  eval_rubric_victim.py   (Victim / Luật sư bảo vệ bị hại)
"""

import os, json, re, sys, time, argparse, logging
from pathlib import Path
from typing import Optional, List, Callable
from collections import defaultdict

import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── Nhận định index (neutral role only) ──────────────────────────────────────
# Now reads directly from case_eval_dataset.json — the `explanation` field IS
# the court's Nhận định, already extracted by build_case_eval_dataset.py.
_NHAN_DINH_INDEX: dict = {}

def build_nhan_dinh_index(path: str) -> None:
    """Populate URL→explanation map from case_eval_dataset.json."""
    global _NHAN_DINH_INDEX
    if not Path(path).exists():
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for case in data:
        url = case.get("url", "")
        if url:
            _NHAN_DINH_INDEX[url] = case.get("explanation", "")[:3000].strip()

def get_nhan_dinh(url: str) -> str:
    return _NHAN_DINH_INDEX.get(url, "(Nhận định không có trong dữ liệu)")

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

def setup_logging(log_file: Optional[str], name: str) -> logging.Logger:
    log = logging.getLogger(name)
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

# ── Dataset loader ────────────────────────────────────────────────────────────
# Reads the new case_eval_dataset.json produced by build_case_eval_dataset.py.
# Each entry has: crime_type, url, case_description, explanation, final_verdict.
#
# Fields used by eval scripts:
#   case_url        ← entry["url"]
#   case_description← entry["case_description"]  → sent to /predict as input
#   explanation     ← entry["explanation"]        → court's Nhận định (ground truth)
#   final_verdict   ← entry["final_verdict"]      → court's Quyết định (ground truth)
#   crime_type      ← entry["crime_type"]         → metadata / logging

def _art_num(s: str) -> Optional[str]:
    m = re.search(r"(\d+[A-Za-z]?)", str(s))
    return m.group(1) if m else None

def load_cases(dataset_path: str, _unused: str = "") -> list:
    """Load cases from case_eval_dataset.json.

    The second argument (_unused) is kept for backward-compatible call sites
    that still pass two paths; it is ignored.
    """
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)

    cases = []
    for entry in data:
        url = entry.get("url", "")
        cases.append({
            "case_url":          url,
            "crime_type":        entry.get("crime_type", ""),
            # Input to /predict — the full case facts up to Nhận định
            "case_description":  entry.get("case_description", ""),
            # Ground truth — what the real court said
            "explanation":       entry.get("explanation", ""),
            "final_verdict":     entry.get("final_verdict", ""),
            # Backward-compat aliases used by rubric prompts
            "question":          entry.get("case_description", ""),
            "all_gt_articles":   [],   # not available in new dataset
            "primary_article":   "",
            "mitigating_arts":   [],
            "aggravating_arts":  [],
            "article_contents":  {},
        })
    return cases

# ── AI service call ───────────────────────────────────────────────────────────
def call_predict(ai_url: str, question: str, role: str,
                 timeout: int, log: logging.Logger) -> str:
    try:
        r = requests.post(
            f"{ai_url.rstrip('/')}/predict",
            json={"case_content": question, "role": role, "conversation_history": []},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("result", "")
    except Exception as e:
        log.warning(f"  /predict [{role}] failed: {e}")
        return ""

# ── Baseline LLM call ─────────────────────────────────────────────────────────
_BASELINE_SYSTEM = "Bạn là chuyên gia pháp lý Việt Nam, am hiểu Bộ luật Hình sự."

def call_baseline(client: OpenAI, model: str, question: str,
                  role_label: str, log: logging.Logger) -> str:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _BASELINE_SYSTEM},
                {"role": "user",
                 "content": f"Vai trò: {role_label}\n\nVụ án:\n{question}"},
            ],
            temperature=0,
            max_tokens=1200,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        log.warning(f"  baseline call failed: {e}")
        return ""

# ── LLM judge call ────────────────────────────────────────────────────────────
def call_judge(client: OpenAI, model: str, prompt: str,
               dimensions: list, log: logging.Logger,
               retries: int = 3) -> Optional[dict]:
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=300,
            )
            raw = r.choices[0].message.content.strip()
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
            data = json.loads(raw)
            total = sum(data.get(d, 0) for d in dimensions)
            data["total"]      = total
            data["normalized"] = round(total / 30 * 5, 2)
            return data
        except Exception as e:
            log.warning(f"    Judge attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return None

# ── Generic runner ────────────────────────────────────────────────────────────
def run_rubric_eval(
    role: str,
    role_label: str,
    dimensions: list,
    build_prompt_fn: Callable,   # (question, case, context) -> str
    build_context_fn: Callable,  # (case) -> dict of extra context for prompt
    args: argparse.Namespace,
    log: logging.Logger,
) -> None:
    """
    Generic evaluation runner used by all 3 RUBRIC scripts.
    Calls system + baseline, scores both with LLM judge, writes results.
    """
    cases = load_cases(args.dataset)
    log.info(f"Cases loaded: {len(cases)}")

    if role == "neutral" and not args.skip_judge:
        log.info(f"Building Nhận định index from {args.dataset} ...")
        build_nhan_dinh_index(args.dataset)
        log.info(f"Nhận định index: {len(_NHAN_DINH_INDEX)} entries")

    s_idx = max(0, args.start - 1)
    e_idx = args.end if args.end else len(cases)
    cases = cases[s_idx:e_idx]
    log.info(f"After range filter: {len(cases)} cases")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done_urls: set = set()
    if args.resume and out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try: done_urls.add(json.loads(line)["case_url"])
                except Exception: pass
        log.info(f"Resume: {len(done_urls)} cases already done.")

    # Two separate OpenRouter clients:
    #   oai_judge    — gemini-2.5-pro via OPENROUTER_LLM_JUDGE_KEY  (scores responses)
    #   oai_baseline — gemini-2.5-flash via OPENROUTER_API_KEY       (plain LLM, no RAG)
    oai_judge = OpenAI(
        api_key=os.getenv("OPENROUTER_LLM_JUDGE_KEY") or os.getenv("OPENROUTER_API_KEY") or "missing",
        base_url="https://openrouter.ai/api/v1",
    )
    oai_baseline = OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY") or "missing",
        base_url="https://openrouter.ai/api/v1",
    )
    baseline_model = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")

    log.info(f"  Inference model  (system + baseline) : {baseline_model}")
    log.info(f"  Judge model      (rubric scoring)    : {args.model}")

    sys_scores, base_scores_all = [], []
    dim_sys_acc:  dict = {d: [] for d in dimensions}
    dim_base_acc: dict = {d: [] for d in dimensions}
    processed = 0

    with open(out_path, "a", encoding="utf-8") as out_f:
        for i, case in enumerate(tqdm(cases, desc="Evaluating", unit="case")):
            url  = case["case_url"]
            cidx = s_idx + i + 1
            if url in done_urls:
                continue

            log.info(f"[{cidx}/{s_idx + len(cases)}] {url[-55:]}")

            ctx = build_context_fn(case)

            # System response — via /predict (RAG + gemini-2.5-flash)
            sys_resp = call_predict(args.ai_url, case["question"], role,
                                    args.timeout, log)
            # Baseline response — direct gemini-2.5-flash call, no RAG
            base_resp = call_baseline(oai_baseline, baseline_model, case["question"],
                                      role_label, log)

            row = {
                "case_index":       cidx,
                "case_url":         url,
                "primary_article":  case["primary_article"],
                "system_response":  sys_resp[:500],
                "baseline_response":base_resp[:500],
                "system_rubric":    None,
                "baseline_rubric":  None,
            }

            if not args.skip_judge:
                # Score system
                sys_prompt = build_prompt_fn(case["question"], case, ctx,
                                             sys_resp, base_resp)
                s = call_judge(oai_judge, args.model, sys_prompt, dimensions, log)
                if s:
                    sys_scores.append(s["normalized"])
                    for d in dimensions:
                        if d in s: dim_sys_acc[d].append(s[d])
                    row["system_rubric"] = s
                    log.info(f"  sys  normalized={s['normalized']}  gaps={s.get('key_gaps','')[:50]}")
                time.sleep(args.delay)

                # Score baseline
                base_prompt = build_prompt_fn(case["question"], case, ctx,
                                              base_resp, sys_resp)
                b = call_judge(oai_judge, args.model, base_prompt, dimensions, log)
                if b:
                    base_scores_all.append(b["normalized"])
                    for d in dimensions:
                        if d in b: dim_base_acc[d].append(b[d])
                    row["baseline_rubric"] = b
                    log.info(f"  base normalized={b['normalized']}")
                time.sleep(args.delay)

            out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_f.flush()
            processed += 1

    # Summary
    def _avg(lst): return round(sum(lst) / len(lst), 4) if lst else None

    sys_avg  = _avg(sys_scores)
    base_avg = _avg(base_scores_all)
    delta    = round((sys_avg or 0) - (base_avg or 0), 4) if (sys_avg and base_avg) else None
    passed   = (delta or 0) >= 0.5

    summary = {
        "meta": {
            "role": role, "n_cases": processed,
            "case_range": f"{args.start}–{'END' if not args.end else args.end}",
            "judge_model": args.model if not args.skip_judge else "skipped",
        },
        "rubric_score_0_5": {
            "system":   sys_avg,
            "baseline": base_avg,
            "delta":    delta,
            "target_delta": 0.5,
            "pass":     passed,
        },
        "per_dimension_system":   {d: _avg(dim_sys_acc[d])  for d in dimensions},
        "per_dimension_baseline": {d: _avg(dim_base_acc[d]) for d in dimensions},
    }

    sum_path = Path(args.summary)
    sum_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    p = "✅ PASS" if passed else "❌ FAIL"
    log.info("=" * 70)
    log.info(f"RUBRIC [{role.upper()}] — {processed} cases")
    log.info("=" * 70)
    log.info(f"  System   : {sys_avg}")
    log.info(f"  Baseline : {base_avg}")
    log.info(f"  Delta    : {delta}  target≥+0.5  {p}")
    for d in dimensions:
        log.info(f"  {d:40s} sys={_avg(dim_sys_acc[d])}  base={_avg(dim_base_acc[d])}")
    log.info(f"  Details → {out_path}")
    log.info(f"  Summary → {sum_path}")
    log.info("=" * 70)

# ── Shared arg parser ─────────────────────────────────────────────────────────
def base_arg_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument(
        "--dataset",
        default="ai-service/evaluation/thesis_eval_1000.json",
        help="Path to case_eval_dataset.json (case_description / explanation / final_verdict)",
    )
    p.add_argument("--output",  default=None)
    p.add_argument("--summary", default=None)
    p.add_argument("--ai-url",  default=os.getenv("AI_SERVICE_URL", "http://localhost:8000"))
    p.add_argument("--model",   default=os.getenv("LLM_JUDGE_MODEL", "google/gemini-2.5-pro"))
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--start",   type=int, default=1)
    p.add_argument("--end",     type=int, default=0)
    p.add_argument("--resume",  action="store_true")
    p.add_argument("--skip-judge", action="store_true",
                   help="Only generate responses, skip LLM judge scoring")
    p.add_argument("--log-file", default=None)
    p.add_argument("--delay",  type=float, default=0.8)
    return p
