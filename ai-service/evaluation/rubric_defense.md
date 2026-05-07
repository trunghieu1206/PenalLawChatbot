# RUBRIC 2 — Defense Lawyer / Luật sư bào chữa

> **Role:** `defense` — The system responds as a defense lawyer arguing for the best possible outcome for the defendant.  
> **Scorer:** `google/gemini-2.5-flash` via OpenRouter  
> **Ground truth:** Case facts + mitigating articles (Điều 51/54/65) + primary crime article.  
> **Scale:** 6 × (1–5) → total max 30 → normalized **0–5**  
> **Pass:** normalized_system − normalized_baseline ≥ **+0.5**

---

## What a Good Defense Response Must Do

1. Identify **every** mitigating circumstance in the case facts and link each to a specific Điều 51 clause
2. Cite Điều 54 if below-minimum sentencing is possible (multiple mitigating factors)
3. Argue for **án treo** (suspended sentence, Điều 65) if eligibility criteria are met
4. Never cite aggravating circumstances — or if mentioned, immediately reframe them
5. Use formal defense brief language: *"Kính đề nghị Hội đồng xét xử…"*

---

## 6 Dimensions

| # | Dimension | 1 | 3 | 5 |
|---|-----------|---|---|---|
| **D1** | **Mitigating Article Accuracy** | No mitigating articles, or entirely wrong ones | Some correct; misses key ones (e.g., Điều 65 when án treo eligible) | All applicable: Điều 51 (specific clause), 54 if multi-mitigating, 65 if eligible — correct edition |
| **D2** ⭐ | **Defense Argument Strength** | Weak or absent; could belong to prosecution | Argues for defendant without legal grounding (emotional only) | Compelling: specific mitigating fact → specific Điều 51 clause → concrete sentence reduction |
| **D3** | **Mitigating Circumstance ID** | No mitigating circumstances identified despite clear ones in case | Main ones found; 1–2 clear ones missed (e.g., "thành khẩn khai báo", "lần đầu phạm tội") | All mitigating facts from case found, each linked to the correct Điều 51 khoản |
| **D4** | **Sentencing Minimization** | Accepts or implies maximum sentence; no reduction argument | Argues for reduced sentence without citing eligibility criteria | Argues for minimum: Điều 65 (án treo) if eligible + Điều 54 (below-min) if applicable + alternative penalty |
| **D5** | **Defense Perspective Consistency** | Argues against own client; cites aggravating factors as prosecution | Mostly defense-oriented; 1–2 statements inadvertently harm defendant | Never harms client; reframes every case fact favorably; explicitly rebuts prosecution's strongest points |
| **D6** | **Clarity & Legal Precision** | Incoherent or contradictory | Understandable but generic; no specific clause references | Professional brief style: "Kính đề nghị…" + article + clause + edition; logical argument flow |

> **D2 is the most important.** A baseline LLM tends to give balanced analysis. RAG allows the system to find specific mitigating articles that directly apply to this defendant's profile.

---

## Scoring Formula

```
total      = D1 + D2 + D3 + D4 + D5 + D6   (max 30)
normalized = (total / 30) × 5               (range: 1.0–5.0)
```

---

## Judge Prompt (used in `eval_rubric_defense.py`)

```
You are an expert Vietnamese criminal law professor evaluating an AI defense lawyer.

## CASE FACTS
{case_question}

## PRIMARY CRIME ARTICLE (defendant charged under)
{primary_article}

## MITIGATING ARTICLES FROM COURT VERDICT (ground truth)
{mitigating_articles}

## ALL ARTICLES APPLIED BY COURT
{ground_truth_articles}

## RESPONSE BEING EVALUATED (defense/bào chữa role)
{response}

## BASELINE RESPONSE (direct LLM, no RAG — context only, do NOT score)
{baseline}

---
Score the EVALUATED RESPONSE (1–5 each):

D1_mitigating_article_accuracy:     correct mitigating articles? (1=none/wrong, 5=Điều 51/54/65 all correct+edition)
D2_defense_argument_strength:       compelling legal defense? (1=weak, 5=facts→articles→sentence reduction)
D3_mitigating_circumstance_id:      all mitigating factors found? (1=none, 5=all linked to Điều 51 clauses)
D4_sentencing_minimization:         lightest outcome argued? (1=accepts max, 5=án treo/below-min)
D5_defense_perspective_consistency: stays in defense role? (1=harms client, 5=fully advocates)
D6_clarity_precision:               professional defense brief? (1=incoherent, 5=clause+edition+formal)

Return ONLY:
{"D1_mitigating_article_accuracy":0,"D2_defense_argument_strength":0,
 "D3_mitigating_circumstance_id":0,"D4_sentencing_minimization":0,
 "D5_defense_perspective_consistency":0,"D6_clarity_precision":0,
 "total":0,"normalized":0.0,"key_gaps":"<1 sentence>"}
```

---

## Sub-goals

| Dimension | Minimum acceptable |
|-----------|-------------------|
| D2 Defense Argument Strength | ≥ 3.5 / 5 |
| D4 Sentencing Minimization | ≥ 3.0 / 5 |
| D5 Defense Perspective Consistency | ≥ 4.0 / 5 |
