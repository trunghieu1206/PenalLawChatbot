# VNPLaw — System Evaluation Tutorial

This guide explains how to evaluate the VNPLaw chatbot's final response quality using the full evaluation framework. All scripts run **on the machine hosting `ai-service`** (or against a remote GPU server via `--ai-url`).

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
├── rubric_neutral.md          ← RUBRIC 1 dimension definitions
├── rubric_defense.md          ← RUBRIC 2 dimension definitions
├── rubric_victim.md           ← RUBRIC 3 dimension definitions
└── results/                   ← auto-created output directory
```

---

## Datasets

| File | Purpose |
|------|---------|
| `scraped_datasets/toaan_gov_test_datasets.json` | **Questions** — case descriptions fed into the system. Contains `question`, `expected_article`, `article_content`. |
| `scraped_datasets/toaan_gov_datasets.json` | **Ground truth** — all articles the court actually applied (complete verdicts). Used for recall & hallucination checks. |
| `scraped_datasets/scraped_texts.json` | **Full case texts** — used only by `eval_rubric_neutral.py` to extract the court's **Nhận định** (legal reasoning section). |

---

## Prerequisites

### 1. Install dependencies

```bash
pip install -r ai-service/evaluation/requirements.txt
```

### 2. Set environment variables

Create `ai-service/.env` (or export directly):

```bash
OPENROUTER_API_KEY=sk-or-...           # Required for LLM judge + baseline calls
AI_SERVICE_URL=http://localhost:8000   # Default; change to GPU server IP if remote
LLM_MODEL=google/gemini-2.5-flash     # Baseline + judge model
```

### 3. Verify the AI service is running

```bash
curl http://localhost:8000/health
# or for remote:
curl http://<GPU-IP>:8000/health
```

---

## Metrics — How Each Is Evaluated

### Metric 1 — Hallucination Rate (target: ≤ 10%)

**Script:** `eval_hallucination.py`

Runs **4 deterministic/LLM layers** per case:

| Layer | What it checks | LLM? |
|-------|---------------|------|
| L1 — Article Existence | Cited article not in BLHS corpus or court verdict | No |
| L2 — Edition/Retroactivity | Wrong BLHS edition for the crime date (Article 7 violation) | No |
| L3 — Sentencing Range | Stated penalty contradicts actual article content | No |
| L4 — Factual Consistency | Response contradicts case facts (dates, amounts, counts) | Yes |

**Composite score** per case = `L1×0.30 + L2×0.30 + L3×0.25 + L4×0.15`  
**Hallucination rate** = fraction of cases with composite score > 0  
**Pass** = rate ≤ 0.10

```bash
# Fast (L1+L2+L3 only, free):
python3 ai-service/evaluation/eval_hallucination.py \
  --start 1 --end 100 --skip-l4 \
  --log-file ai-service/evaluation/results/hall.txt

# Full 4-layer (with L4 LLM judge):
python3 ai-service/evaluation/eval_hallucination.py \
  --start 1 --end 100 \
  --log-file ai-service/evaluation/results/hall_full.txt
```

**Outputs:** `hallucination_results.jsonl`, `hallucination_summary.json`

---

### Metric 2 — Primary Article Recall (target: ≥ 90%)

**Script:** `eval_primary_recall.py`

**Primary article** = first non-procedural article in the court's verdict (e.g., `Điều 173` for theft).

- Calls `/predict` (neutral role)
- Checks if the system's `mapped_laws` contains the primary article number
- Falls back to response text regex if `mapped_laws` is empty
- **No LLM calls — completely free to run**

```bash
python3 ai-service/evaluation/eval_primary_recall.py \
  --start 1 --end 100 \
  --log-file ai-service/evaluation/results/recall.txt
```

**Outputs:** `primary_recall_results.jsonl`, `primary_recall_summary.json`

The summary JSON includes a **full list of missed cases** — useful for diagnosing whether failures are retrieval or mapping problems.

---

### Metric 3 — Role Adherence Score (target: ≥ 85%)

**Script:** `eval_role_adherence.py`

Calls `/predict` for **all 3 roles** per case and scores each with a 2-layer approach:

**Layer A — Keyword Signal Analysis** (free, no LLM):
- `defense`: must find "giảm nhẹ", "án treo", "điều 51"... must NOT find "tăng nặng"
- `victim`: must find "tăng nặng", "bồi thường"... must NOT find "án treo", "miễn trách nhiệm"
- `neutral`: must find BOTH "giảm nhẹ" AND "tăng nặng" (balance check)

**Layer B — LLM Judge** (optional, 3 yes/no questions per role):
- Q1: Adopts the correct perspective?
- Q2: Cites role-appropriate articles?
- Q3: Avoids arguing against its own role?

**Final score** = `0.6 × signal_score + 0.4 × llm_score`

```bash
# Fast (Layer A only, free):
python3 ai-service/evaluation/eval_role_adherence.py \
  --start 1 --end 100 --skip-llm \
  --log-file ai-service/evaluation/results/role.txt

# Full 2-layer:
python3 ai-service/evaluation/eval_role_adherence.py \
  --start 1 --end 100 \
  --log-file ai-service/evaluation/results/role_full.txt
```

**Outputs:** `role_adherence_results.jsonl`, `role_adherence_summary.json`

---

### Metrics 4–6 — RUBRIC Score per Role (target: system − baseline Δ ≥ +0.5)

Three separate scripts evaluate the **quality of the system's final response** against a **baseline** (direct LLM API call with no RAG or role engineering). Each uses a 6-dimension rubric scored by an LLM judge.

**Baseline prompt** (what the baseline LLM receives — no context, no engineering):
```
Vai trò: {role_label}
Vụ án: {case_question}
Hãy phân tích pháp lý theo vai trò trên.
```

#### Metric 4 — RUBRIC Judge Role

**Script:** `eval_rubric_neutral.py`  
**Rubric reference:** `rubric_neutral.md`  
**Ground truth:** Court's actual **Nhận định** (extracted from `scraped_texts.json`)

| Dimension | What it measures |
|-----------|----------------|
| D1 Legal Article Accuracy | Articles match court's Nhận định |
| **D2 Reasoning Alignment** ⭐ | System reasoning mirrors court's logic chain |
| D3 Circumstance Coverage | All mitigating + aggravating from Nhận định covered |
| D4 Sentencing Consistency | Sentencing matches court's khoản + range |
| D5 Judicial Neutrality | Truly neutral — not one-sided |
| D6 Clarity & Precision | Professional judicial language |

```bash
python3 ai-service/evaluation/eval_rubric_neutral.py \
  --start 1 --end 100 \
  --log-file ai-service/evaluation/results/rubric_neutral.txt
```

**Outputs:** `rubric_neutral_results.jsonl`, `rubric_neutral_summary.json`

---

#### Metric 5 — RUBRIC Defense Role

**Script:** `eval_rubric_defense.py`  
**Rubric reference:** `rubric_defense.md`  
**Ground truth:** Case facts + mitigating articles (Điều 51/54/65)

| Dimension | What it measures |
|-----------|----------------|
| D1 Mitigating Article Accuracy | Correct mitigating articles cited |
| **D2 Defense Argument Strength** ⭐ | Compelling legal defense (facts → articles → reduction) |
| D3 Mitigating Circumstance ID | All mitigating factors from case found |
| D4 Sentencing Minimization | Argues for lightest outcome (án treo, below-minimum) |
| D5 Defense Perspective Consistency | Never accidentally harms own client |
| D6 Clarity & Precision | Professional defense brief language |

```bash
python3 ai-service/evaluation/eval_rubric_defense.py \
  --start 1 --end 100 \
  --log-file ai-service/evaluation/results/rubric_defense.txt
```

**Outputs:** `rubric_defense_results.jsonl`, `rubric_defense_summary.json`

---

#### Metric 6 — RUBRIC Victim Role

**Script:** `eval_rubric_victim.py`  
**Rubric reference:** `rubric_victim.md`  
**Ground truth:** Case facts + aggravating articles (Điều 52) + victim damages

| Dimension | What it measures |
|-----------|----------------|
| D1 Aggravating Article Accuracy | Correct aggravating articles cited |
| **D2 Victim Advocacy Strength** ⭐ | Compelling victim advocacy (facts → articles → max sentence + compensation) |
| D3 Aggravating Circumstance ID | All aggravating factors found |
| D4 Maximum Sentencing Argument | Argues for highest applicable khoản |
| D5 Victim Perspective Consistency | Never inadvertently defends the accused |
| D6 Civil Compensation Coverage | Quantified bồi thường with legal basis |

```bash
python3 ai-service/evaluation/eval_rubric_victim.py \
  --start 1 --end 100 \
  --log-file ai-service/evaluation/results/rubric_victim.txt
```

**Outputs:** `rubric_victim_results.jsonl`, `rubric_victim_summary.json`

---

## Common Arguments (all scripts)

| Argument | Default | Description |
|----------|---------|-------------|
| `--start N` | 1 | First case (1-indexed, inclusive) |
| `--end N` | 0 (all) | Last case (0 = run all remaining) |
| `--ai-url URL` | `http://localhost:8000` | AI service URL (change for remote GPU) |
| `--model MODEL` | `google/gemini-2.5-flash` | LLM model for judge/baseline calls |
| `--timeout N` | 120 | Request timeout in seconds |
| `--resume` | off | Skip already-written cases in output file |
| `--log-file PATH` | none | Append logs to this file (in addition to stdout) |
| `--delay N` | 0.5–0.8 | Seconds between cases (rate limiting) |

---

## Batch Processing on GPU Server

### Split into batches of 100

```bash
for start in $(seq 1 100 1000); do
    end=$((start + 99))
    python3 ai-service/evaluation/eval_hallucination.py \
      --start $start --end $end \
      --ai-url http://<GPU-IP>:8000 \
      --log-file ai-service/evaluation/results/hall_${start}_${end}.txt
    sleep 2
done
```

### Resume after interruption

All scripts write results incrementally (one line per case, flushed immediately). To resume:

```bash
python3 ai-service/evaluation/eval_primary_recall.py \
  --start 1 --end 500 --resume \
  --log-file ai-service/evaluation/results/recall.txt
```

---

## Full Run (Recommended Order)

```bash
# ── Step 1: Free metrics (no LLM cost) ──────────────────────────────────────
python3 ai-service/evaluation/eval_primary_recall.py \
  --start 1 --end 100 --log-file ai-service/evaluation/results/recall.txt

python3 ai-service/evaluation/eval_hallucination.py \
  --start 1 --end 100 --skip-l4 \
  --log-file ai-service/evaluation/results/hall.txt

python3 ai-service/evaluation/eval_role_adherence.py \
  --start 1 --end 100 --skip-llm \
  --log-file ai-service/evaluation/results/role.txt

# ── Step 2: LLM-graded metrics (API cost) ────────────────────────────────────
python3 ai-service/evaluation/eval_hallucination.py \
  --start 1 --end 100 --log-file ai-service/evaluation/results/hall_full.txt

python3 ai-service/evaluation/eval_role_adherence.py \
  --start 1 --end 100 --log-file ai-service/evaluation/results/role_full.txt

python3 ai-service/evaluation/eval_rubric_neutral.py \
  --start 1 --end 100 --log-file ai-service/evaluation/results/rubric_neutral.txt

python3 ai-service/evaluation/eval_rubric_defense.py \
  --start 1 --end 100 --log-file ai-service/evaluation/results/rubric_defense.txt

python3 ai-service/evaluation/eval_rubric_victim.py \
  --start 1 --end 100 --log-file ai-service/evaluation/results/rubric_victim.txt
```

---

## Pass/Fail Summary

| Metric | Script | Output Summary File | Pass Condition |
|--------|--------|--------------------|----|
| Hallucination Rate | `eval_hallucination.py` | `hallucination_summary.json` | `hallucination_rate_binary ≤ 0.10` |
| Primary Article Recall | `eval_primary_recall.py` | `primary_recall_summary.json` | `primary_recall ≥ 0.90` |
| Role Adherence Score | `eval_role_adherence.py` | `role_adherence_summary.json` | `overall.mean_score ≥ 0.85` |
| RUBRIC — Judge | `eval_rubric_neutral.py` | `rubric_neutral_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |
| RUBRIC — Defense | `eval_rubric_defense.py` | `rubric_defense_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |
| RUBRIC — Victim | `eval_rubric_victim.py` | `rubric_victim_summary.json` | `rubric_score_0_5.delta ≥ 0.5` |

Check each summary JSON's `"pass": true/false` field for the final verdict.
