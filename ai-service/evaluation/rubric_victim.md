# RUBRIC 3 — Victim's Lawyer / Luật sư bảo vệ bị hại

> **Role:** `victim` — The system responds as a victim's lawyer advocating for maximum sentence and full compensation.  
> **Scorer:** `google/gemini-2.5-flash` via OpenRouter  
> **Ground truth:** Case facts + aggravating articles (Điều 52 + crime-specific khoản) + victim's damages.  
> **Scale:** 6 × (1–5) → total max 30 → normalized **0–5**  
> **Pass:** normalized_system − normalized_baseline ≥ **+0.5**

---

## What a Good Victim Advocacy Response Must Do

1. Identify **every** aggravating circumstance and link to Điều 52 specific khoản
2. Argue for the **highest applicable khoản** of the primary crime article
3. Explicitly argue **against án treo** if the defendant received or is eligible for it
4. Quantify civil compensation (bồi thường thiệt hại) with amounts from case facts
5. Use formal victim advocacy language: *"Đề nghị buộc bị cáo bồi thường…"*, *"Đề nghị áp dụng mức án nghiêm khắc nhất…"*

---

## 6 Dimensions

| # | Dimension | 1 | 3 | 5 |
|---|-----------|---|---|---|
| **D1** | **Aggravating Article Accuracy** | No aggravating articles, or entirely wrong ones | Some correct; misses key Điều 52 clauses for this crime method | All applicable: Điều 52 (specific clause for method/role/victim type) + crime khoản justifying higher penalty |
| **D2** ⭐ | **Victim Advocacy Strength** | No meaningful advocacy; response is generic or neutral | Argues for victim without legal grounding (emotional only) | Compelling: specific aggravating fact → specific Điều 52 clause → maximum sentence + full compensation |
| **D3** | **Aggravating Circumstance ID** | No aggravating circumstances identified despite clear ones in case | Main ones found; 1–2 key ones missed (e.g., organized crime, vulnerable victim, dangerous recidivism) | All aggravating facts from case found, each linked to the correct Điều 52 khoản |
| **D4** | **Maximum Sentencing Argument** | Implies lenient outcome; agrees with suspended sentence or minimum | Argues for heavier sentence without citing which clause justifies it | Argues for highest khoản + cites specific aggravating articles + explicitly argues against any leniency |
| **D5** | **Victim Perspective Consistency** | Argues for defendant's interests; never mentions victim rights | Mostly victim-oriented; 1–2 statements inadvertently favor the defendant | Never softens toward defendant; explicitly advocates for victim throughout; cites victim's specific damages |
| **D6** | **Civil Compensation Coverage** | No mention of victim's civil rights or compensation | Mentions compensation vaguely ("bị hại có quyền yêu cầu") without amounts or legal basis | Quantifies damages from case facts + cites legal basis + "Đề nghị buộc bị cáo bồi thường [X] đồng…" |

> **D2 is the most important.** A baseline LLM tends to be balanced. RAG allows the system to find specific aggravating articles and argue with legal precision for the victim's maximum compensation.

---

## Scoring Formula

```
total      = D1 + D2 + D3 + D4 + D5 + D6   (max 30)
normalized = (total / 30) × 5               (range: 1.0–5.0)
```

---

## Judge Prompt (used in `eval_rubric_victim.py`)

```
You are an expert Vietnamese criminal law professor evaluating an AI victim's lawyer.

## CASE FACTS
{case_question}

## PRIMARY CRIME ARTICLE (defendant convicted under)
{primary_article}

## AGGRAVATING ARTICLES FROM COURT VERDICT (ground truth)
{aggravating_articles}

## ALL ARTICLES APPLIED BY COURT
{ground_truth_articles}

## RESPONSE BEING EVALUATED (victim/bị hại role)
{response}

## BASELINE RESPONSE (direct LLM, no RAG — context only, do NOT score)
{baseline}

---
Score the EVALUATED RESPONSE (1–5 each):

D1_aggravating_article_accuracy:  correct aggravating articles? (1=none/wrong, 5=all Điều 52 clauses correct)
D2_victim_advocacy_strength:      compelling victim advocacy? (1=neutral, 5=facts→articles→max+compensation)
D3_aggravating_circumstance_id:   all aggravating factors? (1=none, 5=all linked to Điều 52 clauses)
D4_maximum_sentencing_argument:   harshest outcome argued? (1=leniency, 5=highest khoản+against án treo)
D5_victim_perspective_consistency: stays in victim role? (1=defends accused, 5=fully advocates)
D6_civil_compensation_coverage:   covers bồi thường? (1=not mentioned, 5=quantified+legal basis)

Return ONLY:
{"D1_aggravating_article_accuracy":0,"D2_victim_advocacy_strength":0,
 "D3_aggravating_circumstance_id":0,"D4_maximum_sentencing_argument":0,
 "D5_victim_perspective_consistency":0,"D6_civil_compensation_coverage":0,
 "total":0,"normalized":0.0,"key_gaps":"<1 sentence>"}
```

---

## Sub-goals

| Dimension | Minimum acceptable |
|-----------|-------------------|
| D2 Victim Advocacy Strength | ≥ 3.5 / 5 |
| D4 Maximum Sentencing Argument | ≥ 3.0 / 5 |
| D6 Civil Compensation Coverage | ≥ 3.5 / 5 |
