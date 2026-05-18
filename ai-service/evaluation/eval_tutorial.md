# VNPLaw — System Evaluation Tutorial

This guide explains how to evaluate the VNPLaw chatbot's final response quality using the full evaluation framework. All scripts run **on the server hosting `ai-service`** from the project root `~/PenalLawChatbot/`.

---

## Evaluation Architecture

The system is evaluated across **6 metrics** using **6 independent scripts**. Each script calls `/predict` directly and writes its own output files.

```
ai-service/evaluation/
├── eval_hallucination.py      ← Metric 1: Hallucination Rate
├── eval_primary_recall.py     ← Metric 2: Primary Article Recall
├── eval_role_adherence.py     ← Metric 3: Role Adherence Score
├── eval_rubric_neutral.py     ← Metric 4: RUBRIC Score — Judge role
├── eval_rubric_defense.py     ← Metric 5: RUBRIC Score — Defense role
├── eval_rubric_victim.py      ← Metric 6: RUBRIC Score — Victim role
├── eval_rubric_common.py      ← Shared utilities (do NOT run directly)
└── results/                   ← auto-created output directory
```

---

## Dataset

All scripts use a single dataset file:

| File | Size | Cases | Crime Types |
|------|------|-------|-------------|
| `ai-service/scraped_datasets/thesis_eval_1000.json` | 26 MB | **1,000** | **338** |

Each entry has three fields the scripts use:

| Field | Used as |
|-------|---------|
| `case_description` | Input sent to `/predict` (full case facts) |
| `explanation` | Ground truth — real court's Nhận định (reasoning) |
| `final_verdict` | Ground truth — real court's Quyết định (decision) |

The dataset is sampled proportionally from `case_eval_dataset.json` (6,285 cases) with seed 42.
To run against all 6,285 cases, add `--dataset ai-service/scraped_datasets/case_eval_dataset.json`.

---

## Models Used

| Role | Model | API Key env var |
|------|-------|----------------|
| System response (RAG) | `google/gemini-2.5-flash` via `/predict` endpoint | — (internal) |
| Baseline response (no RAG) | `google/gemini-2.5-flash` | `OPENROUTER_API_KEY` |
| LLM Judge (scoring) | `google/gemini-2.5-pro` | `OPENROUTER_LLM_JUDGE_KEY` |

---

## Server Setup (one-time)

### 1. SSH into the server and activate the venv

```bash
ssh -p 1927 root@n3.ckey.vn
cd ~/PenalLawChatbot

# Activate the same venv the ai-service uses
source ai-service/venv/bin/activate
```

### 2. Install eval script dependencies

```bash
pip install openai requests python-dotenv
```

### 3. Ensure .env is in place

```bash
# Should already exist after deploy — verify these keys are set:
grep "OPENROUTER_API_KEY\|OPENROUTER_LLM_JUDGE_KEY" .env
```

Expected output:
```
OPENROUTER_API_KEY=sk-or-v1-...       # inference key (gemini-2.5-flash)
OPENROUTER_LLM_JUDGE_KEY=sk-or-v1-... # judge key (gemini-2.5-pro)
```

### 4. Verify the AI service is running

```bash
curl -s http://localhost:8000/health
# Expected: {"status":"ok"} or similar
```

### 5. Create a tmux session (survives disconnect)

```bash
tmux new -s eval
# Re-attach later: tmux attach -t eval
```

---

## Running the Evaluation Scripts

**All commands must be run from `~/PenalLawChatbot/`** (the project root).

### Metric 1 — Primary Article Recall (target: ≥ 90%)

No LLM cost — completely free. Extracts article numbers from the `final_verdict` text and checks if the system cited the primary crime article.

```bash
python3 ai-service/evaluation/eval_primary_recall.py \
  --log-file ai-service/logs/eval_primary_recall.txt
```

**Outputs:** `results/primary_recall_results.jsonl`, `results/primary_recall_summary.json`

---

### Metric 2 — Hallucination Rate (target: ≤ 10%)

Runs 4 layers per case (L1–L3 are free; L4 uses gemini-2.5-pro judge):

| Layer | What it checks | LLM? |
|-------|---------------|------|
| L1 — Article Existence | Cited article not in BLHS corpus or final_verdict | No |
| L2 — Edition/Retroactivity | Wrong BLHS edition for the crime date | No |
| L3 — Sentencing Range | Stated penalty contradicts actual article content | No |
| L4 — Factual Consistency | Response contradicts case_description or court verdict | Yes (gemini-2.5-pro) |

```bash
# Fast — L1+L2+L3 only (free)
python3 ai-service/evaluation/eval_hallucination.py \
  --skip-l4 \
  --log-file ai-service/logs/eval_hallucination.txt

# Full 4-layer (recommended for thesis)
python3 ai-service/evaluation/eval_hallucination.py \
  --log-file ai-service/logs/eval_hallucination.txt
```

**Outputs:** `results/hallucination_results.jsonl`, `results/hallucination_summary.json`

---

### Metric 3 — Role Adherence Score (target: ≥ 85%)

Calls `/predict` for all 3 roles per case. Two layers:

- **Layer A** (free): keyword signal analysis — checks for role-appropriate Vietnamese phrases
- **Layer B** (gemini-2.5-pro): 3 yes/no questions — perspective, appropriate articles, no self-contradiction

Final score = `0.6 × signal_score + 0.4 × llm_score`

```bash
# Layer A only (free, fast sanity check)
python3 ai-service/evaluation/eval_role_adherence.py \
  --skip-llm \
  --log-file ai-service/logs/eval_role_adherence.txt

# Full 2-layer (recommended for thesis)
python3 ai-service/evaluation/eval_role_adherence.py \
  --log-file ai-service/logs/eval_role_adherence.txt
```

**Outputs:** `results/role_adherence_results.jsonl`, `results/role_adherence_summary.json`

---

### Metrics 4–6 — RUBRIC Score per Role (target: system − baseline Δ ≥ +0.5)

Each script scores both the **system response** (RAG + gemini-2.5-flash) and a **baseline response** (plain gemini-2.5-flash, no RAG) using a 6-dimension rubric judged by gemini-2.5-pro.

**Score formula:** `normalized = (D1+D2+D3+D4+D5+D6) / 30 × 5` → scale 1.0–5.0  
**Pass condition:** `delta = system_score − baseline_score ≥ +0.5`

#### Metric 4 — RUBRIC Judge Role

Ground truth: `explanation` field (real court's Nhận định)

| Dimension | Key question |
|-----------|-------------|
| D1 Legal Article Accuracy | Articles match court's Nhận định? |
| **D2 Reasoning Alignment** ⭐ | Does reasoning mirror the court's logic chain? |
| D3 Circumstance Coverage | All mitigating + aggravating from Nhận định covered? |
| D4 Sentencing Consistency | Sentencing matches court's khoản + range? |
| D5 Judicial Neutrality | Truly neutral — not one-sided? |
| D6 Clarity & Precision | Professional judicial language? |

```bash
python3 ai-service/evaluation/eval_rubric_neutral.py \
  --log-file ai-service/logs/eval_rubric_neutral.txt
```

**Outputs:** `results/rubric_neutral_results.jsonl`, `results/rubric_neutral_summary.json`

---

#### Metric 5 — RUBRIC Defense Role

Ground truth: `case_description` + mitigating articles (Điều 51/54/65)

| Dimension | Key question |
|-----------|-------------|
| D1 Mitigating Article Accuracy | Correct mitigating articles cited? |
| **D2 Defense Argument Strength** ⭐ | Compelling legal defense: facts → articles → reduction? |
| D3 Mitigating Circumstance ID | All mitigating factors from case found? |
| D4 Sentencing Minimization | Argues for án treo / below-minimum if applicable? |
| D5 Defense Perspective Consistency | Never accidentally harms own client? |
| D6 Clarity & Precision | Professional defense brief language? |

```bash
python3 ai-service/evaluation/eval_rubric_defense.py \
  --log-file ai-service/logs/eval_rubric_defense.txt
```

**Outputs:** `results/rubric_defense_results.jsonl`, `results/rubric_defense_summary.json`

---

#### Metric 6 — RUBRIC Victim Role

Ground truth: `case_description` + aggravating articles (Điều 52) + victim damages

| Dimension | Key question |
|-----------|-------------|
| D1 Aggravating Article Accuracy | Correct aggravating articles cited? |
| **D2 Victim Advocacy Strength** ⭐ | Compelling advocacy: facts → articles → max sentence + compensation? |
| D3 Aggravating Circumstance ID | All aggravating factors from case found? |
| D4 Maximum Sentencing Argument | Argues for highest applicable khoản? |
| D5 Victim Perspective Consistency | Never inadvertently defends the accused? |
| D6 Civil Compensation Coverage | Quantified bồi thường with legal basis? |

```bash
python3 ai-service/evaluation/eval_rubric_victim.py \
  --log-file ai-service/logs/eval_rubric_victim.txt
```

**Outputs:** `results/rubric_victim_results.jsonl`, `results/rubric_victim_summary.json`

---

## Common Arguments (all scripts)

| Argument | Default | Description |
|----------|---------|-------------|
| `--dataset PATH` | `thesis_eval_1000.json` | Dataset file |
| `--start N` | 1 | First case (1-indexed, inclusive) |
| `--end N` | 0 (all) | Last case (0 = run all) |
| `--ai-url URL` | `http://localhost:8000` | AI service URL |
| `--model MODEL` | `google/gemini-2.5-pro` | LLM judge model |
| `--timeout N` | 120 | Request timeout in seconds |
| `--resume` | off | Skip already-written cases |
| `--log-file PATH` | none | Append logs to this file |
| `--delay N` | 0.5–0.8 | Seconds between cases |

---

## Recommended Full Run (thesis)

```bash
cd ~/PenalLawChatbot
source ai-service/venv/bin/activate

# ── Free metrics first (no API cost) ─────────────────────────────────────────
python3 ai-service/evaluation/eval_primary_recall.py \
  --log-file ai-service/logs/eval_primary_recall.txt

python3 ai-service/evaluation/eval_hallucination.py \
  --skip-l4 --log-file ai-service/logs/eval_hallucination_fast.txt

python3 ai-service/evaluation/eval_role_adherence.py \
  --skip-llm --log-file ai-service/logs/eval_role_adherence_fast.txt

# ── Full LLM-graded metrics (API cost ~$1–2 total) ────────────────────────────
python3 ai-service/evaluation/eval_hallucination.py \
  --log-file ai-service/logs/eval_hallucination.txt

python3 ai-service/evaluation/eval_role_adherence.py \
  --log-file ai-service/logs/eval_role_adherence.txt

python3 ai-service/evaluation/eval_rubric_neutral.py \
  --log-file ai-service/logs/eval_rubric_neutral.txt

python3 ai-service/evaluation/eval_rubric_defense.py \
  --log-file ai-service/logs/eval_rubric_defense.txt

python3 ai-service/evaluation/eval_rubric_victim.py \
  --log-file ai-service/logs/eval_rubric_victim.txt
```

### Resume after interruption

```bash
python3 ai-service/evaluation/eval_primary_recall.py \
  --resume --log-file ai-service/logs/eval_primary_recall.txt
```

---

## Downloading Results to Local Machine

Run from your **local machine** inside `~/Desktop/Projects/PenalLawChatbot/`:

```bash
# Download all result JSON/JSONL files
scp -P 1927 -r \
  'root@n3.ckey.vn:~/PenalLawChatbot/ai-service/evaluation/results/' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/evaluation/

# Download log .txt files
scp -P 1927 \
  'root@n3.ckey.vn:~/PenalLawChatbot/ai-service/logs/eval_*.txt' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/logs/
```

---

## Pass/Fail Summary

| Metric | Script | Summary File | Pass Condition |
|--------|--------|-------------|----------------|
| Hallucination Rate | `eval_hallucination.py` | `hallucination_summary.json` | `hallucination_rate_binary ≤ 0.10` |
| Primary Article Recall | `eval_primary_recall.py` | `primary_recall_summary.json` | `primary_recall ≥ 0.90` |
| Role Adherence | `eval_role_adherence.py` | `role_adherence_summary.json` | `overall.mean_score ≥ 0.85` |
| RUBRIC — Judge | `eval_rubric_neutral.py` | `rubric_neutral_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |
| RUBRIC — Defense | `eval_rubric_defense.py` | `rubric_defense_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |
| RUBRIC — Victim | `eval_rubric_victim.py` | `rubric_victim_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |

Check each summary JSON's `"pass": true/false` field for the final verdict.

---

### 🚀 The Combined Script (eval_combined.py)

To minimize the cost of calling the LLM APIs, you can run all 3 evaluations (Primary Recall, Hallucination L1-L3, and Role Adherence) in a single pass. 
This script also runs a side-by-side comparison with the raw baseline (`gemini-2.5-flash` without RAG) for all 3 metrics! L4 Hallucination is intentionally removed here to reduce costs.

```bash
python3 ai-service/evaluation/eval_combined.py \
  --log-file ai-service/logs/eval_combined.txt
```

**Outputs:** `results/combined_results.jsonl`, `results/combined_summary.json`
