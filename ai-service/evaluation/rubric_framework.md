# RUBRIC Framework — VNPLaw Three-Perspective Evaluation

> **Purpose:** LLM-as-a-Judge scoring rubric for evaluating the VNPLaw chatbot across all 3 legal perspectives.  
> **Scorer model:** `google/gemini-2.5-flash` via OpenRouter  
> **Scale:** 6 dimensions × 1–5 each → total (max 30) → normalized to **0–5**  
> **Usage:** Apply the same rubric to **System response** (VNPLaw RAG) AND **Baseline response** (direct LLM call, no engineering). Report delta = system − baseline. Pass: Δ ≥ +0.5

---

## Ground Truth per Role

| Role | Primary Ground Truth | Secondary Ground Truth |
|------|---------------------|----------------------|
| `neutral` (Judge) | Court's **Nhận định** from `scraped_texts.json` | All articles applied (`toaan_gov_datasets.json`) |
| `defense` (Luật sư bào chữa) | Case facts (`question`) + mitigating articles (Điều 51/54/65) | Primary crime article + defendant profile |
| `victim` (Luật sư bảo vệ bị hại) | Case facts (`question`) + aggravating articles (Điều 52) | Victim's damages + court's civil compensation order |

---

## RUBRIC 1 — Neutral / Judge (Thẩm phán)

> Uses court's **Nhận định** as primary ground truth. The system is expected to reason the same way a judge would — neutral, evidence-based, citing both sides.

| Dim | Name | 1 | 3 | 5 |
|-----|------|---|---|---|
| **D1** | Legal Article Accuracy | All articles differ from Nhận định | Primary article correct; supporting articles wrong/missing | All articles match Nhận định (number + edition) |
| **D2** | Reasoning Alignment ⭐ | Contradicts court logic (e.g., argues innocence when court convicted) | Same crime class; misses key reasoning steps the court emphasized | Mirrors court's Nhận định: same facts weighted, same legal conclusions |
| **D3** | Circumstance Coverage | No mitigating/aggravating mentioned despite court citing several | Main circumstances covered; 1–2 important ones missing | All circumstances from Nhận định identified with matching articles (Điều 51/52) |
| **D4** | Sentencing Consistency | Opposite outcome (e.g., acquittal vs. imprisonment) | Correct range from article; final recommendation diverges significantly | Matches court: correct khoản + range + detention deduction |
| **D5** | Judicial Neutrality | Strongly advocates for one side (reads like defense brief or prosecution) | Covers both sides with visible bias in language | Perfectly neutral: "Hội đồng xét xử nhận thấy…" language; both sides equally weighted |
| **D6** | Clarity & Legal Precision | Incoherent or self-contradictory | Clear but imprecise ("có thể áp dụng" without specific clause) | Professional: article + clause + edition + logical paragraph structure |

### RUBRIC 1 — Judge Prompt

```text
You are an expert Vietnamese criminal law professor evaluating an AI judge assistant.

## CASE FACTS (fed into the system)
{case_question}

## COURT'S ACTUAL NHẬN ĐỊNH (real judge's reasoning — PRIMARY ground truth)
{nhan_dinh_text}

## ALL ARTICLES APPLIED BY COURT (secondary ground truth)
{ground_truth_articles}

## RESPONSE BEING EVALUATED (neutral/judge role)
{response}

---
Using the COURT'S NHẬN ĐỊNH as your primary benchmark, score the response (1–5 each):

D1_legal_accuracy:         articles match Nhận định? (1=all wrong, 5=all correct+edition)
D2_reasoning_alignment:    reasoning follows court logic? (1=contradicts, 5=mirrors)
D3_circumstance_coverage:  mitigating+aggravating from Nhận định covered? (1=none, 5=all)
D4_sentencing_consistency: sentencing matches court decision? (1=opposite, 5=exact khoản+range)
D5_judicial_neutrality:    neutral judge perspective? (1=one-sided, 5=perfectly neutral)
D6_clarity_precision:      clear + precise legal language? (1=incoherent, 5=professional)

Return ONLY this JSON:
{"D1_legal_accuracy":0,"D2_reasoning_alignment":0,"D3_circumstance_coverage":0,"D4_sentencing_consistency":0,"D5_judicial_neutrality":0,"D6_clarity_precision":0,"total":0,"normalized":0.0,"key_gaps":"<1 sentence>"}
```

---

## RUBRIC 2 — Defense Lawyer (Luật sư bào chữa)

> Ground truth: case facts + mitigating articles. The system should argue **effectively for the defendant**, citing every available mitigating factor and arguing for the lightest possible outcome.

| Dim | Name | 1 | 3 | 5 |
|-----|------|---|---|---|
| **D1** | Mitigating Article Accuracy | Cites no mitigating articles OR cites wrong articles for this crime type | Cites some correct mitigating articles; misses 1–2 key ones (e.g., Điều 65 when án treo eligible) | All applicable mitigating articles cited correctly (Điều 51, 54, 65, etc.) with correct edition |
| **D2** | Defense Argument Strength ⭐ | Weak or absent advocacy; response could belong to prosecution | Argues for defendant but without legal grounding; emotional only | Compelling legal defense: specific mitigating facts → specific articles → specific sentencing reduction |
| **D3** | Mitigating Circumstance Identification | No mitigating circumstances identified despite clear ones in case facts | Main circumstances identified; 1–2 clear ones (e.g., "thành khẩn khai báo") missed | All mitigating circumstances from case facts found and linked to corresponding Điều 51/54 clauses |
| **D4** | Sentencing Minimization | Accepts or implies maximum sentence; no argument for reduction | Argues for reduced sentence but without citing eligibility criteria | Argues for minimum: suspended sentence (Điều 65) if eligible; below-minimum (Điều 54) if applicable; alternative penalty if available |
| **D5** | Defense Perspective Consistency | Argues against own client; cites aggravating factors as if for prosecution | Mostly defense-oriented; 1–2 statements accidentally harm the defendant | Stays fully in role: never cites evidence against defendant; reframes every fact favorably |
| **D6** | Clarity & Legal Precision | Incoherent or contradictory | Understandable advocacy but generic without specific clause references | Professional defense brief style: "Kính đề nghị Hội đồng xét xử…" + specific article+clause+edition |

### RUBRIC 2 — Defense Prompt

```text
You are an expert Vietnamese criminal law professor evaluating an AI defense lawyer assistant.

## CASE FACTS (the criminal case being analyzed)
{case_question}

## DEFENDANT PROFILE (extracted from case)
{defendant_profile}

## PRIMARY CRIME ARTICLE (what the defendant is charged under)
{primary_crime_article}

## ALL ARTICLES APPLIED BY COURT (for reference)
{ground_truth_articles}

## RESPONSE BEING EVALUATED (defense/bào chữa role)
{response}

---
Evaluate how effectively this response defends the accused. Score (1–5 each):

D1_mitigating_article_accuracy:  correct mitigating articles cited? (1=none/wrong, 5=all correct+edition)
D2_defense_argument_strength:    compelling legal defense? (1=weak/absent, 5=specific facts→articles→reduction)
D3_mitigating_circumstance_id:   all mitigating factors from case found? (1=none, 5=all linked to Điều 51/54 clauses)
D4_sentencing_minimization:      argues for lightest outcome? (1=accepts max, 5=án treo/below-min if applicable)
D5_defense_perspective_consistency: stays in defense role? (1=argues against client, 5=fully advocates)
D6_clarity_precision:            professional defense brief? (1=incoherent, 5=clause+edition+formal language)

Return ONLY this JSON:
{"D1_mitigating_article_accuracy":0,"D2_defense_argument_strength":0,"D3_mitigating_circumstance_id":0,"D4_sentencing_minimization":0,"D5_defense_perspective_consistency":0,"D6_clarity_precision":0,"total":0,"normalized":0.0,"key_gaps":"<1 sentence>"}
```

---

## RUBRIC 3 — Victim's Lawyer (Luật sư bảo vệ bị hại)

> Ground truth: case facts + aggravating articles + victim's damages. The system should advocate **effectively for the victim** — maximum sentence, full civil compensation, and recognition of all harm caused.

| Dim | Name | 1 | 3 | 5 |
|-----|------|---|---|---|
| **D1** | Aggravating Article Accuracy | Cites no aggravating articles OR cites wrong ones for this crime type | Cites some correct aggravating articles; misses key ones (e.g., Điều 52 specific clauses for crime method) | All applicable aggravating articles cited (Điều 52 + crime-specific aggravating khoản) with correct edition |
| **D2** | Victim Advocacy Strength ⭐ | No meaningful advocacy for victim; response is generic or neutral | Argues for victim but without legal grounding; relies only on emotional appeal | Compelling legal advocacy: specific aggravating facts → specific articles → maximum sentence + full compensation |
| **D3** | Aggravating Circumstance Identification | No aggravating circumstances identified despite clear ones in case facts | Main aggravating factors found; misses 1–2 key ones (e.g., organized crime, vulnerable victim) | All aggravating factors from case facts identified and linked to Điều 52 clauses or specific crime khoản |
| **D4** | Maximum Sentencing Argument | Implies lenient outcome; agrees with suspended sentence or minimum | Argues for heavier sentence but without citing which clause/khoản justifies it | Argues for highest applicable khoản + cites specific aggravating articles + argues against án treo if court applied it |
| **D5** | Victim Perspective Consistency | Argues for defendant's interests; never mentions victim rights | Mostly victim-oriented; 1–2 statements favor the defendant inadvertently | Stays fully in role: advocates for victim throughout; cites bồi thường thiệt hại; argues against any leniency |
| **D6** | Civil Compensation Coverage | No mention of victim's civil rights or compensation | Mentions compensation vaguely ("bị hại có quyền yêu cầu") without amounts or legal basis | Specific: quantifies damages from case facts + cites BLDS (Civil Code) + "đề nghị buộc bị cáo bồi thường X đồng" |

### RUBRIC 3 — Victim Prompt

```text
You are an expert Vietnamese criminal law professor evaluating an AI victim's lawyer assistant.

## CASE FACTS (the criminal case being analyzed)
{case_question}

## VICTIM INFORMATION (extracted from case)
{victim_profile}

## PRIMARY CRIME ARTICLE (what the defendant was convicted under)
{primary_crime_article}

## ALL ARTICLES APPLIED BY COURT (for reference)
{ground_truth_articles}

## RESPONSE BEING EVALUATED (victim/bị hại role)
{response}

---
Evaluate how effectively this response advocates for the victim. Score (1–5 each):

D1_aggravating_article_accuracy:  correct aggravating articles cited? (1=none/wrong, 5=all Điều 52 clauses correct)
D2_victim_advocacy_strength:      compelling advocacy for victim? (1=none/neutral, 5=facts→articles→max sentence+compensation)
D3_aggravating_circumstance_id:   all aggravating factors found? (1=none, 5=all linked to correct Điều 52 clauses)
D4_maximum_sentencing_argument:   argues for harshest applicable outcome? (1=implies leniency, 5=highest khoản+against án treo)
D5_victim_perspective_consistency: stays in victim advocate role? (1=defends accused, 5=fully advocates for victim)
D6_civil_compensation_coverage:   covers bồi thường thiệt hại? (1=not mentioned, 5=quantified+legal basis cited)

Return ONLY this JSON:
{"D1_aggravating_article_accuracy":0,"D2_victim_advocacy_strength":0,"D3_aggravating_circumstance_id":0,"D4_maximum_sentencing_argument":0,"D5_victim_perspective_consistency":0,"D6_civil_compensation_coverage":0,"total":0,"normalized":0.0,"key_gaps":"<1 sentence>"}
```

---

## Scoring Formula (All 3 RUBRICs)

```
total      = D1 + D2 + D3 + D4 + D5 + D6    (range: 6–30)
normalized = (total / 30) × 5                (range: 1.0–5.0)
```

---

## System vs. Baseline Comparison

Apply **the same RUBRIC** to both:

| Input | Response evaluated |
|-------|--------------------|
| **System** | VNPLaw `/predict` endpoint (RAG + role routing + law mapping) |
| **Baseline** | Direct LLM API call with minimal prompt (no RAG, no engineering) |

**Baseline prompt template** (no engineering):
```text
Bạn là chuyên gia pháp lý Việt Nam.
Vai trò: {role_label}
Vụ án: {case_question}
Hãy phân tích pháp lý theo vai trò trên.
```

**Delta calculation:**
```
rubric_delta = normalized_system - normalized_baseline
pass: rubric_delta >= +0.5 (across all 3 roles averaged)
```

**Per-role sub-goals:**

| Role | Most important dimension | Minimum acceptable score |
|------|-------------------------|------------------------|
| Judge | D2 Reasoning Alignment | ≥ 3.5 / 5 |
| Defense | D2 Defense Argument Strength | ≥ 3.5 / 5 |
| Victim | D2 Victim Advocacy Strength | ≥ 3.5 / 5 |
| All roles | D6 Clarity & Precision | ≥ 4.0 / 5 |

---

## Key Differences Between Role RUBRICs

| Aspect | Judge | Defense | Victim |
|--------|-------|---------|--------|
| Primary ground truth | Court's Nhận định | Case facts + mitigating articles | Case facts + aggravating articles |
| D⭐ (most important) | Reasoning Alignment | Defense Argument Strength | Victim Advocacy Strength |
| Role-specific dim | D5 Judicial Neutrality | D4 Sentencing Minimization | D6 Civil Compensation |
| Fails if... | Takes sides | Cites aggravating factors | Argues for reduced sentence |
| Passes if... | Mirrors court logic | Gets client lightest outcome | Maximizes sentence + compensation |
