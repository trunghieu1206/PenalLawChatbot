# VNPLaw — System Evaluation Tutorial

This guide explains how to evaluate the VNPLaw chatbot's final response quality.
All scripts run **on the server hosting `ai-service`** from the project root `~/PenalLawChatbot/`.

---

## Evaluation Architecture

The evaluation is split into **two stages** with different cost profiles:

```
Stage 1 — Core Metrics  (system only, no baseline, near-zero API cost)
  eval_combined_hallucination_recall_role_adherence.py
    ├── Metric 1: Primary Article Recall     (deterministic, free)
    ├── Metric 2: Hallucination Rate L1–L3   (deterministic, free)
    └── Metric 3: Role Adherence Score       (4-dim rule-based, free)

Stage 2 — Rubric Quality  (system vs baseline, LLM judge required)
  eval_rubric_neutral.py   ← Metric 4: RUBRIC — Judge role
  eval_rubric_defense.py   ← Metric 5: RUBRIC — Defense role
  eval_rubric_victim.py    ← Metric 6: RUBRIC — Victim role
```

> **Key design principle:** Recall, Hallucination, and Role Adherence evaluate the RAG system alone.
> The baseline (`gemini-2.5-flash` without RAG) is only used for rubric comparison.

---

## Dataset

| File | Cases | Unique crime types |
|------|-------|--------------------|
| `ai-service/evaluation/thesis_eval_unique.json` | **338** | **338** (1 per crime type) |

Each entry has three fields:

| Field | Used as |
|-------|---------| 
| `case_description` | Input sent to `/predict` (full case facts) |
| `explanation` | Ground truth — real court's Nhận định (reasoning) |
| `final_verdict` | Ground truth — real court's Quyết định (decision) |

---

## Models Used

| Role | Model | API Key env var | When used |
|------|-------|----------------|-----------|
| System response (RAG) | `google/gemini-2.5-flash` via `/predict` | — (internal) | Stage 1 + Stage 2 |
| Baseline (no RAG) | `google/gemini-2.5-flash` | `OPENROUTER_API_KEY` | Stage 2 only |
| LLM Judge | `google/gemini-2.5-pro` | `OPENROUTER_LLM_JUDGE_KEY` | Stage 2 only |

---

## API Cost Breakdown

### Stage 1 — Combined (with `--skip-rubric`, default)

| Call | Model | Calls per case | Total (338 cases) |
|------|-------|---------------|-------------------|
| `/predict` × 3 roles | Internal (free) | 3 | 1,014 |
| Baseline response | ❌ Not called | 0 | 0 |
| LLM judge | ❌ Not called | 0 | **0** |

**Total API cost: ~$0** (only local inference)

### Stage 2 — Rubric (per script, e.g. `eval_rubric_neutral.py`)

| Call | Model | Calls per case | Total (338 cases) |
|------|-------|---------------|-------------------|
| `/predict` | Internal (free) | 1 | 338 |
| Baseline response | Gemini 2.5 Flash | 1 | 338 |
| Judge (sys rubric) | Gemini 2.5 Pro | 1 | 338 |
| Judge (base rubric) | Gemini 2.5 Pro | 1 | 338 |

**Per rubric script: ~676 API calls** (338 Flash + 338 Pro)
**All 3 rubric scripts: ~2,028 API calls total**

---

## Server Setup (one-time)

```bash
ssh root@<server>
cd ~/PenalLawChatbot

# Activate venv
source ai-service/venv/bin/activate

# Install eval dependencies
pip install openai requests python-dotenv httpx

# Verify API keys
grep "OPENROUTER_API_KEY\|OPENROUTER_LLM_JUDGE_KEY" .env

# Verify AI service is running
curl -s http://localhost:8000/health

# Start tmux to survive disconnect
tmux new -s eval
# Re-attach later: tmux attach -t eval
```

---

## Stage 1 — Core Metrics (Run This First)

This script evaluates **Recall + Hallucination + Role Adherence** for the RAG system only.
No baseline calls. No LLM judge. Runs very fast.

```bash
cd ~/PenalLawChatbot

python3 ai-service/evaluation/eval_combined_hallucination_recall_role_adherence.py \
  --skip-rubric \
  --log-file ai-service/logs/eval_combined.txt
```

**Outputs:**
- `results/combined_results.jsonl` — per-case raw data
- `results/combined_summary.json` — final aggregated scores
- `results/combined_report.txt` — human-readable report ← download this

### Run in chunks (recommended for reliability)

```bash
# Chunk 1
python3 ai-service/evaluation/eval_combined_hallucination_recall_role_adherence.py \
  --start 1 --end 100 --skip-rubric \
  --log-file ai-service/logs/eval_combined_1_100.txt

# Chunk 2
python3 ai-service/evaluation/eval_combined_hallucination_recall_role_adherence.py \
  --start 101 --end 200 --skip-rubric \
  --log-file ai-service/logs/eval_combined_101_200.txt

# Chunk 3
python3 ai-service/evaluation/eval_combined_hallucination_recall_role_adherence.py \
  --start 201 --end 338 --skip-rubric \
  --log-file ai-service/logs/eval_combined_201_338.txt
```

### Resume after interruption

```bash
python3 ai-service/evaluation/eval_combined_hallucination_recall_role_adherence.py \
  --start 1 --end 100 --skip-rubric --resume \
  --log-file ai-service/logs/eval_combined_1_100.txt
```

### Pass/fail targets

| Metric | Target | How scored |
|--------|--------|------------|
| Primary Recall | ≥ 90% | Deterministic article number matching |
| Hallucination Rate | ≤ 10% | 3-layer rule-based (L1 existence, L2 edition, L3 sentencing) |
| Role Adherence | ≥ 85% | 4-dim rule-based (D1 article align, D2 sentencing dir, D3 vocab, D4 citation structure) |

---

## Stage 2 — Rubric Quality (Run After Stage 1)

Each script evaluates **system vs baseline** using a 6-dimension LLM rubric (gemini-2.5-pro judge).

**Score formula:** `normalized = (D1+D2+D3+D4+D5+D6) / 30 × 5` → scale 0–5
**Pass condition:** `Δ = system_score − baseline_score ≥ +0.5`

### Metric 4 — RUBRIC Judge Role (Neutral)

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

---

### Metric 5 — RUBRIC Defense Role

| Dimension | Key question |
|-----------|-------------|
| D1 Mitigating Article Accuracy | Correct mitigating articles (Điều 51/54/65) cited? |
| **D2 Defense Argument Strength** ⭐ | Compelling defense: facts → articles → sentence reduction? |
| D3 Mitigating Circumstance ID | All mitigating factors from case found? |
| D4 Sentencing Minimization | Argues for án treo / below-minimum if applicable? |
| D5 Defense Perspective Consistency | Never accidentally harms own client? |
| D6 Clarity & Precision | Professional defense brief language? |

```bash
python3 ai-service/evaluation/eval_rubric_defense.py \
  --log-file ai-service/logs/eval_rubric_defense.txt
```

---

### Metric 6 — RUBRIC Victim Role

| Dimension | Key question |
|-----------|-------------|
| D1 Aggravating Article Accuracy | Correct aggravating articles (Điều 52) cited? |
| **D2 Victim Advocacy Strength** ⭐ | Compelling advocacy: facts → articles → max sentence + compensation? |
| D3 Aggravating Circumstance ID | All aggravating factors from case found? |
| D4 Maximum Sentencing Argument | Argues for highest applicable khoản? |
| D5 Victim Perspective Consistency | Never inadvertently defends the accused? |
| D6 Civil Compensation Coverage | Quantified bồi thường with legal basis? |

```bash
python3 ai-service/evaluation/eval_rubric_victim.py \
  --log-file ai-service/logs/eval_rubric_victim.txt
```

---

## Common Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--dataset PATH` | `thesis_eval_unique.json` | Dataset file |
| `--start N` | 1 | First case (1-indexed, inclusive) |
| `--end N` | 0 (all) | Last case (0 = run all) |
| `--ai-url URL` | `http://localhost:8000` | AI service URL |
| `--judge-model MODEL` | `google/gemini-2.5-pro` | LLM judge model (rubric only) |
| `--baseline-model MODEL` | `google/gemini-2.5-flash` | Baseline model (rubric only) |
| `--timeout N` | 300 | `/predict` timeout in seconds |
| `--resume` | off | Skip already-written cases |
| `--log-file PATH` | none | Append logs to this file |
| `--delay N` | 0.5 | Seconds between cases |
| `--skip-rubric` | off | Skip rubric LLM calls (Stage 1 mode) |

---

## Recommended Full Evaluation Sequence

```bash
cd ~/PenalLawChatbot
source ai-service/venv/bin/activate

# ── STAGE 1: Core metrics (free, fast) ───────────────────────────────────────
python3 ai-service/evaluation/eval_combined_hallucination_recall_role_adherence.py \
  --skip-rubric \
  --log-file ai-service/logs/eval_stage1.txt

# ── STAGE 2: Rubric quality (LLM judge, ~$3–5 total) ─────────────────────────
python3 ai-service/evaluation/eval_rubric_neutral.py \
  --log-file ai-service/logs/eval_rubric_neutral.txt

python3 ai-service/evaluation/eval_rubric_defense.py \
  --log-file ai-service/logs/eval_rubric_defense.txt

python3 ai-service/evaluation/eval_rubric_victim.py \
  --log-file ai-service/logs/eval_rubric_victim.txt
```

---

## Downloading Results to Local Machine

Run from your **local machine**:

```bash
# Download all result JSON/JSONL files
scp -P <port> -r \
  'root@<server>:~/PenalLawChatbot/ai-service/evaluation/results/' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/evaluation/

# Download the human-readable combined report
scp -P <port> \
  root@<server>:~/PenalLawChatbot/ai-service/evaluation/results/combined_report.txt .

# Download log files
scp -P <port> \
  'root@<server>:~/PenalLawChatbot/ai-service/logs/eval_*.txt' \
  ~/Desktop/Projects/PenalLawChatbot/ai-service/logs/
```

---

## Pass/Fail Summary

| Metric | Script | Summary File | Pass Condition |
|--------|--------|-------------|----------------|
| Primary Article Recall | `eval_combined_*.py` | `combined_summary.json` | `system.primary_recall ≥ 0.90` |
| Hallucination Rate | `eval_combined_*.py` | `combined_summary.json` | `system.hallucination_rate ≤ 0.10` |
| Role Adherence | `eval_combined_*.py` | `combined_summary.json` | `system.role_adherence ≥ 0.85` |
| RUBRIC — Judge | `eval_rubric_neutral.py` | `rubric_neutral_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |
| RUBRIC — Defense | `eval_rubric_defense.py` | `rubric_defense_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |
| RUBRIC — Victim | `eval_rubric_victim.py` | `rubric_victim_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |
