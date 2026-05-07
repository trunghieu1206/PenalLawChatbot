# RUBRIC 1 — Judge / Thẩm phán (Neutral Role)

> **Role:** `neutral` — The system responds as an impartial judge analyzing the case from both sides.  
> **Scorer:** `google/gemini-2.5-flash` via OpenRouter  
> **Ground truth:** Court's **Nhận định** section (from `scraped_texts.json`) — the actual legal reasoning applied, not just article numbers.  
> **Scale:** 6 × (1–5) → total max 30 → normalized **0–5**  
> **Pass:** normalized_system − normalized_baseline ≥ **+0.5**

---

## Why Nhận định as Ground Truth?

The article list only says *which* articles the court cited.  
The **Nhận định** says *why* — the full legal reasoning chain:
- Which facts were proven  
- How mitigating vs. aggravating circumstances were weighed  
- Why a specific khoản was applied  
- How the final sentence was calculated  

---

## 6 Dimensions

| # | Dimension | 1 | 3 | 5 |
|---|-----------|---|---|---|
| **D1** | **Legal Article Accuracy** | All articles differ from Nhận định | Primary article correct; supporting wrong/missing | All articles match Nhận định (number + edition) |
| **D2** ⭐ | **Reasoning Alignment** | Contradicts court (e.g., argues innocence when court convicted) | Same crime class; misses key reasoning steps court emphasized | Mirrors court's Nhận định: same facts weighted, same conclusions |
| **D3** | **Circumstance Coverage** | No mitigating/aggravating mentioned despite court citing several | Main ones covered; 1–2 important ones missing | All circumstances from Nhận định with matching articles (Điều 51/52) |
| **D4** | **Sentencing Consistency** | Opposite outcome (acquittal vs. imprisonment) | Correct article range; final recommendation diverges significantly | Matches court: correct khoản + range + detention deduction |
| **D5** | **Judicial Neutrality** | Strongly advocates for one side | Covers both sides with visible language bias | Perfectly neutral: "Hội đồng xét xử nhận thấy…"; both sides equally weighted |
| **D6** | **Clarity & Legal Precision** | Incoherent or self-contradictory | Clear but imprecise ("có thể áp dụng" without specific clause) | Article + clause + edition + logical paragraph structure |

> **D2 is the most important.** RAG adds the most value here — a baseline LLM cannot reason from the actual court's logic without retrieved law context.

---

## Scoring Formula

```
total      = D1 + D2 + D3 + D4 + D5 + D6   (max 30)
normalized = (total / 30) × 5               (range: 1.0–5.0)
```

---

## Judge Prompt (used in `eval_rubric_neutral.py`)

```
You are an expert Vietnamese criminal law professor evaluating an AI judge assistant.

## CASE FACTS (fed into the system)
{case_question}

## COURT'S ACTUAL NHẬN ĐỊNH (real judge's reasoning — PRIMARY ground truth)
{nhan_dinh_text}

## ALL ARTICLES APPLIED BY COURT
{ground_truth_articles}

## RESPONSE BEING EVALUATED (neutral/judge role)
{response}

## BASELINE RESPONSE (direct LLM, no RAG — context only, do NOT score)
{baseline}

---
Score the EVALUATED RESPONSE (1–5 each):

D1_legal_accuracy:          articles match Nhận định? (1=all wrong, 5=all correct+edition)
D2_reasoning_alignment:     reasoning follows court logic? (1=contradicts, 5=mirrors)
D3_circumstance_coverage:   mitigating+aggravating covered? (1=none, 5=all from Nhận định)
D4_sentencing_consistency:  matches court decision? (1=opposite, 5=exact khoản+range)
D5_judicial_neutrality:     neutral? (1=one-sided, 5=perfectly neutral)
D6_clarity_precision:       precise legal language? (1=incoherent, 5=professional)

Return ONLY:
{"D1_legal_accuracy":0,"D2_reasoning_alignment":0,"D3_circumstance_coverage":0,
 "D4_sentencing_consistency":0,"D5_judicial_neutrality":0,"D6_clarity_precision":0,
 "total":0,"normalized":0.0,"key_gaps":"<1 sentence>"}
```

---

## Sub-goals

| Dimension | Minimum acceptable |
|-----------|-------------------|
| D2 Reasoning Alignment | ≥ 3.5 / 5 |
| D5 Judicial Neutrality | ≥ 4.0 / 5 |
| D6 Clarity & Precision | ≥ 4.0 / 5 |
