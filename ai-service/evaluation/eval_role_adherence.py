#!/usr/bin/env python3
"""
eval_role_adherence.py — Role Adherence evaluation for VNPLaw AI service.

TWO-LAYER SCORING PER CASE:
  Layer A — Keyword Signal Analysis (deterministic, free, no LLM)
    Each role has positive signals (should appear) and negative signals (should NOT appear).
    signal_score = positive_hit_rate - 0.5 * negative_hit_rate   (clamped to 0–1)

  Layer B — LLM Judge (3 targeted yes/no questions, ~60 tokens per call)
    Q1: Does it clearly adopt the requested role perspective?
    Q2: Does it cite role-appropriate articles?
    Q3: Does it avoid arguing against its role?
    llm_score = yes_count / 3

  final_score = 0.6 * signal_score + 0.4 * llm_score
  Pass: mean(final_score) >= 0.85

RUN ON GPU SERVER:
  python3 eval_role_adherence.py --start 1 --end 100 --log-file role_1_100.txt
  python3 eval_role_adherence.py --start 1 --end 100 --skip-llm --log-file role_fast.txt
"""

import os, json, re, sys, time, argparse, logging
from pathlib import Path
from typing import Optional
from collections import defaultdict

import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

ROLES = ["neutral", "defense", "victim"]

# ── Role signals ─────────────────────────────────────────────────────────────
# Each entry: list of lowercase Vietnamese phrases to search in response text.
ROLE_SIGNALS = {
    "defense": {
        "positive": [
            "giảm nhẹ", "tình tiết giảm nhẹ", "án treo",
            "cải tạo không giam giữ", "thành khẩn", "ăn năn", "hối cải",
            "lần đầu phạm tội", "phạm tội lần đầu", "nhân thân tốt",
            "bồi thường", "khắc phục hậu quả", "hoàn cảnh khó khăn",
            "đề nghị giảm", "xin giảm nhẹ",
            "điều 51", "điều 54", "điều 65",
        ],
        "negative": [
            "tình tiết tăng nặng", "tăng nặng trách nhiệm",
            "mức án cao nhất", "đề nghị mức án cao",
            "không có khả năng cải tạo", "nguy hiểm cho xã hội",
        ],
    },
    "victim": {
        "positive": [
            "tình tiết tăng nặng", "tăng nặng", "hậu quả nghiêm trọng",
            "bồi thường thiệt hại", "bồi thường dân sự", "thiệt hại",
            "mức án cao nhất", "hình phạt nghiêm khắc", "đề nghị phạt",
            "tiền án", "tái phạm", "có tổ chức", "dùng hung khí",
            "điều 52", "điều 48",
        ],
        "negative": [
            "đề nghị án treo", "xin miễn", "giảm nhẹ hình phạt",
            "không đáng bị phạt", "nên áp dụng án treo",
            "cải tạo không giam giữ",
        ],
    },
    "neutral": {
        "positive": [
            "giảm nhẹ", "tăng nặng",          # must mention BOTH sides
            "nhận định", "xem xét", "cân nhắc",
            "theo quy định", "căn cứ",
        ],
        "negative": [
            # One-sided language — neutral should not exclusively advocate
            "kiên quyết đề nghị", "nhất định phải phạt",
            "bảo vệ bị cáo bằng mọi giá",
        ],
    },
}

# For neutral role, BOTH "giảm nhẹ" AND "tăng nặng" must appear
NEUTRAL_BALANCE_TERMS = ["giảm nhẹ", "tăng nặng"]

# ── LLM judge prompt ──────────────────────────────────────────────────────────
_JUDGE_PROMPT = """\
You are evaluating whether an AI legal assistant correctly adopted the requested role.

ROLE REQUESTED: {role_label}
CASE SUMMARY: {question}
AI RESPONSE: {response}

Answer ONLY these 3 questions with yes or no:
Q1: Does the response clearly advocate from the {role_label} perspective \
(not just mention the role but actually argue from that viewpoint)?
Q2: Does the response cite articles appropriate for the {role_label} role \
(e.g., mitigating articles Điều 51/65 for defense; aggravating Điều 52 for victim; \
both sides for neutral judge)?
Q3: Does the response avoid arguing against its own role \
(defense not arguing harshly against defendant; victim not arguing for reduced sentence)?

Return ONLY this JSON with no other text:
{{"q1": "yes", "q2": "yes", "q3": "yes"}}
"""

ROLE_LABELS = {
    "neutral": "Thẩm phán trung lập (neutral judge)",
    "defense": "Luật sư bào chữa (defense lawyer)",
    "victim":  "Luật sư bảo vệ bị hại (victim's lawyer)",
}


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
    log = logging.getLogger("role_adherence")
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
def load_cases(dataset_path: str, _unused: str = "") -> list:
    """Load from case_eval_dataset.json."""
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for entry in data:
        cases.append({
            "case_url":          entry.get("url", ""),
            "crime_type":        entry.get("crime_type", ""),
            "case_description":  entry.get("case_description", ""),
            # backward-compat alias — signal scoring uses case["question"]
            "question":          entry.get("case_description", ""),
            "explanation":       entry.get("explanation", ""),
            "final_verdict":     entry.get("final_verdict", ""),
            "ground_truth_articles": [],
        })
    return cases


# ── AI service call ───────────────────────────────────────────────────────────
def call_predict(ai_url: str, question: str, role: str,
                 timeout: int, log: logging.Logger) -> str:
    try:
        r = requests.post(
            f"{ai_url.rstrip('/')}/predict",
            json={"case_content": question, "role": role,
                  "conversation_history": []},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("result", "")
    except Exception as e:
        log.warning(f"  /predict [{role}] failed: {e}")
        return ""


# ── Layer A: Signal scoring ───────────────────────────────────────────────────
def signal_score(response: str, role: str) -> dict:
    t = response.lower()
    sigs = ROLE_SIGNALS[role]
    pos_hits = [p for p in sigs["positive"] if p in t]
    neg_hits = [n for n in sigs["negative"] if n in t]

    pos_rate = len(pos_hits) / len(sigs["positive"]) if sigs["positive"] else 1.0
    neg_rate = len(neg_hits) / len(sigs["negative"]) if sigs["negative"] else 0.0

    # Extra check for neutral: must mention BOTH sides
    balance_bonus = 0.0
    if role == "neutral":
        both_present = all(term in t for term in NEUTRAL_BALANCE_TERMS)
        balance_bonus = 0.2 if both_present else -0.2

    score = max(0.0, min(1.0, pos_rate - 0.5 * neg_rate + balance_bonus))
    return {
        "score":    round(score, 4),
        "pos_hits": pos_hits,
        "neg_hits": neg_hits,
        "pos_rate": round(pos_rate, 4),
        "neg_rate": round(neg_rate, 4),
    }


# ── Layer B: LLM judge ────────────────────────────────────────────────────────
def llm_score(client: OpenAI, model: str, question: str,
              response: str, role: str, log: logging.Logger) -> dict:
    prompt = _JUDGE_PROMPT.format(
        role_label=ROLE_LABELS[role],
        question=question[:800],
        response=response[:1500],
    )
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=80,
            )
            raw = r.choices[0].message.content.strip()
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
            data = json.loads(raw)
            answers = [data.get(f"q{i}", "no").lower().strip() for i in range(1, 4)]
            yes_count = sum(1 for a in answers if a == "yes")
            return {
                "score":   round(yes_count / 3, 4),
                "answers": {"q1": answers[0], "q2": answers[1], "q3": answers[2]},
            }
        except Exception as e:
            log.warning(f"    LLM judge attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return {"score": None, "answers": {}, "note": "failed_after_retries"}


# ── Combined score ────────────────────────────────────────────────────────────
def combined_score(sig: float, llm: Optional[float],
                   w_sig: float = 0.6, w_llm: float = 0.4) -> float:
    if llm is None:
        return sig  # fallback to signal only
    return round(w_sig * sig + w_llm * llm, 4)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Role Adherence evaluation for VNPLaw AI service."
    )
    parser.add_argument("--dataset",
                        default="ai-service/scraped_datasets/thesis_eval_1000.json",
                        help="Path to case_eval_dataset.json")
    parser.add_argument("--output",
                        default="ai-service/evaluation/results/role_adherence_results.jsonl")
    parser.add_argument("--summary",
                        default="ai-service/evaluation/results/role_adherence_summary.json")
    parser.add_argument("--ai-url",
                        default=os.getenv("AI_SERVICE_URL", "http://localhost:8000"))
    parser.add_argument("--model",
                        default=os.getenv("LLM_JUDGE_MODEL", "google/gemini-2.5-pro"))
    parser.add_argument("--timeout",  type=int, default=120)
    parser.add_argument("--start",    type=int, default=1,
                        help="First case (1-indexed, inclusive)")
    parser.add_argument("--end",      type=int, default=0,
                        help="Last case (0 = all)")
    parser.add_argument("--resume",   action="store_true")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Layer A (signals) only — no LLM calls, free to run")
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--delay",    type=float, default=0.5)
    args = parser.parse_args()

    log = setup_logging(args.log_file)
    log.info("=" * 70)
    log.info("VNPLaw — Role Adherence Evaluation")
    log.info(f"  AI service : {args.ai_url}")
    log.info(f"  Layer B    : {'DISABLED (--skip-llm)' if args.skip_llm else args.model}")
    log.info(f"  Range      : cases {args.start}–{'END' if not args.end else args.end}")
    log.info(f"  Pass goal  : mean score >= 0.85")
    log.info("=" * 70)

    cases = load_cases(args.dataset)
    log.info(f"Total unique cases loaded: {len(cases)}")

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
                try:
                    done_urls.add(json.loads(line)["case_url"])
                except Exception:
                    pass
        log.info(f"Resume: {len(done_urls)} cases already done.")

    oai = OpenAI(api_key=os.getenv("OPENROUTER_LLM_JUDGE_KEY") or os.getenv("OPENROUTER_API_KEY") or "missing",
                 base_url="https://openrouter.ai/api/v1")

    # Per-role accumulators
    all_scores: dict = {r: [] for r in ROLES}
    sig_scores: dict = {r: [] for r in ROLES}
    llm_scores: dict = {r: [] for r in ROLES}
    processed = 0

    with open(out_path, "a", encoding="utf-8") as out_f:
        for i, case in enumerate(tqdm(cases, desc="Evaluating", unit="case")):
            url  = case["case_url"]
            cidx = s_idx + i + 1
            if url in done_urls:
                continue

            log.info(f"[{cidx}/{s_idx + len(cases)}] {url[-55:]}")

            row = {
                "case_index": cidx,
                "case_url":   url,
                "roles":      {},
            }
            case_combined = []

            for role in ROLES:
                response = call_predict(args.ai_url, case["case_description"],
                                        role, args.timeout, log)
                if not response:
                    log.debug(f"  [{role}] empty response — skipping role")
                    continue

                # Layer A
                la = signal_score(response, role)

                # Layer B (optional)
                if not args.skip_llm:
                    lb = llm_score(oai, args.model, case["question"],
                                   response, role, log)
                    time.sleep(args.delay)
                else:
                    lb = {"score": None, "note": "skipped"}

                final = combined_score(la["score"], lb.get("score"))
                case_combined.append(final)
                all_scores[role].append(final)
                sig_scores[role].append(la["score"])
                if lb.get("score") is not None:
                    llm_scores[role].append(lb["score"])

                log.debug(
                    f"  [{role}]  signal={la['score']:.3f}  "
                    f"llm={lb.get('score')}  final={final:.3f}  "
                    f"pos={la['pos_hits'][:3]}  neg={la['neg_hits'][:2]}"
                )

                row["roles"][role] = {
                    "response_snippet": response[:200],
                    "layer_a_signal":   la,
                    "layer_b_llm":      lb,
                    "final_score":      final,
                }

            if case_combined:
                row["case_mean_score"] = round(
                    sum(case_combined) / len(case_combined), 4)

            out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_f.flush()
            processed += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    def _avg(lst): return round(sum(lst) / len(lst), 4) if lst else None

    overall_scores = [s for r in ROLES for s in all_scores[r]]
    overall_mean   = _avg(overall_scores)
    passed         = overall_mean is not None and overall_mean >= 0.85

    summary = {
        "meta": {
            "n_cases":    processed,
            "case_range": f"{args.start}–{'END' if not args.end else args.end}",
            "llm_used":   not args.skip_llm,
            "weights":    {"layer_a_signal": 0.6, "layer_b_llm": 0.4},
            "scoring": {
                "layer_a": "signal_rate - 0.5*negative_rate (+ balance bonus for neutral)",
                "layer_b": "yes_count/3 across 3 targeted yes/no questions",
                "final":   "0.6 * layer_a + 0.4 * layer_b",
            },
        },
        "overall": {
            "mean_score": overall_mean,
            "target":     0.85,
            "pass":       passed,
        },
        "per_role": {
            role: {
                "mean_final":  _avg(all_scores[role]),
                "mean_signal": _avg(sig_scores[role]),
                "mean_llm":    _avg(llm_scores[role]),
                "n_cases":     len(all_scores[role]),
            }
            for role in ROLES
        },
    }

    sum_path = Path(args.summary)
    sum_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    p = "✅ PASS" if passed else "❌ FAIL"
    log.info("=" * 70)
    log.info(f"ROLE ADHERENCE RESULTS — {processed} cases")
    log.info("=" * 70)
    log.info(f"  Overall mean score : {overall_mean}  target≥0.85  {p}")
    for role in ROLES:
        log.info(
            f"  {role:8s}  final={_avg(all_scores[role])}  "
            f"signal={_avg(sig_scores[role])}  llm={_avg(llm_scores[role])}"
        )
    log.info(f"  Details JSONL → {out_path}")
    log.info(f"  Summary JSON  → {sum_path}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
