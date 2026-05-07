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
_NHAN_DINH_INDEX: dict = {}
_ND_KW = ["nhận định của tòa án", "nhận định của hội đồng", "nhận định", "xét thấy"]
_QD_KW = ["quyết định", "nay quyết"]

def _find_kw(text: str, kws: list) -> int:
    lc = text.lower()
    for kw in kws:
        i = lc.find(kw)
        if i != -1:
            return i
    return -1

def build_nhan_dinh_index(path: str) -> None:
    global _NHAN_DINH_INDEX
    if not Path(path).exists():
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for crime in data:
        for case in crime.get("cases", []):
            url  = case.get("url", "")
            text = case.get("case_text", "")
            ns   = _find_kw(text, _ND_KW)
            qs   = _find_kw(text, _QD_KW)
            if ns != -1:
                end = qs if qs > ns else ns + 3000
                _NHAN_DINH_INDEX[url] = text[ns:end][:3000].strip()

def get_nhan_dinh(url: str) -> str:
    return _NHAN_DINH_INDEX.get(url, "(Nhận định không có trong dữ liệu)")

# ── Logging ───────────────────────────────────────────────────────────────────
def setup_logging(log_file: Optional[str], name: str) -> logging.Logger:
    log = logging.getLogger(name)
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

# ── Dataset loader ────────────────────────────────────────────────────────────
_PROCEDURAL = {
    "7","28","32","34","42","45","46","47","48","49","50",
    "51","52","53","54","55","56","57","58","59","60","65",
}
_MITIGATING  = {"51", "54", "65"}
_AGGRAVATING = {"52"}

def _art_num(s: str) -> Optional[str]:
    m = re.search(r"(\d+[A-Za-z]?)", str(s))
    return m.group(1) if m else None

def load_cases(test_path: str, full_path: str) -> list:
    with open(test_path, encoding="utf-8") as f:
        test_data = json.load(f)
    with open(full_path, encoding="utf-8") as f:
        full_data = json.load(f)

    test_by_url: dict = defaultdict(list)
    for e in test_data:
        test_by_url[e["link_to_case"]].append(e)
    full_by_url: dict = defaultdict(list)
    for e in full_data:
        full_by_url[e["link_to_case"]].append(e)

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
        primary = next((a for a in all_gt if _art_num(a) not in _PROCEDURAL), None)
        mitigating  = [a for a in all_gt if _art_num(a) in _MITIGATING]
        aggravating = [a for a in all_gt if _art_num(a) in _AGGRAVATING]
        contents = {e["expected_article"]: e.get("article_content", "")
                    for e in te if e.get("article_content")}

        cases.append({
            "case_url":          url,
            "question":          max(te, key=lambda e: len(e["question"]))["question"],
            "all_gt_articles":   all_gt,
            "primary_article":   primary or "",
            "mitigating_arts":   mitigating,
            "aggravating_arts":  aggravating,
            "article_contents":  contents,
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
    cases = load_cases(args.test_dataset, args.full_dataset)
    log.info(f"Cases loaded: {len(cases)}")

    if role == "neutral" and not args.skip_judge:
        log.info(f"Building Nhận định index from {args.scraped_texts} ...")
        build_nhan_dinh_index(args.scraped_texts)
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

    oai = OpenAI(api_key=os.getenv("OPENROUTER_API_KEY") or "missing",
                 base_url="https://openrouter.ai/api/v1")

    sys_scores, base_scores_all = [], []
    dim_sys_acc:  dict = {d: [] for d in dimensions}
    dim_base_acc: dict = {d: [] for d in dimensions}
    processed = 0

    with open(out_path, "a", encoding="utf-8") as out_f:
        for i, case in enumerate(cases):
            url  = case["case_url"]
            cidx = s_idx + i + 1
            if url in done_urls:
                continue

            log.info(f"[{cidx}/{s_idx + len(cases)}] {url[-55:]}")

            ctx = build_context_fn(case)

            # System response
            sys_resp = call_predict(args.ai_url, case["question"], role,
                                    args.timeout, log)
            # Baseline response
            base_resp = call_baseline(oai, args.model, case["question"],
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
                s = call_judge(oai, args.model, sys_prompt, dimensions, log)
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
                b = call_judge(oai, args.model, base_prompt, dimensions, log)
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
    p.add_argument("--test-dataset",  default="ai-service/scraped_datasets/toaan_gov_test_datasets.json")
    p.add_argument("--full-dataset",  default="ai-service/scraped_datasets/toaan_gov_datasets.json")
    p.add_argument("--scraped-texts", default="ai-service/scraped_datasets/scraped_texts.json")
    p.add_argument("--output",  default=None)
    p.add_argument("--summary", default=None)
    p.add_argument("--ai-url",  default=os.getenv("AI_SERVICE_URL", "http://localhost:8000"))
    p.add_argument("--model",   default=os.getenv("LLM_MODEL", "google/gemini-2.5-flash"))
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--start",   type=int, default=1)
    p.add_argument("--end",     type=int, default=0)
    p.add_argument("--resume",  action="store_true")
    p.add_argument("--skip-judge", action="store_true",
                   help="Only generate responses, skip LLM judge scoring")
    p.add_argument("--log-file", default=None)
    p.add_argument("--delay",  type=float, default=0.8)
    return p
