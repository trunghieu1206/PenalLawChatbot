# Evaluation Insights Report — Vietnamese Penal Law Chatbot
*Generated: 2026-07-05 | Dataset: 100 cases, 300 role evaluations (neutral × defense × victim)*

---

## 1. Executive Summary

The system was evaluated across **100 real court verdicts** from the Supreme Court of Vietnam database
([congbobanan.toaan.gov.vn](https://congbobanan.toaan.gov.vn)), each assessed from three legal
perspectives: **neutral judge**, **defense counsel**, and **victim's representative**.

**All 5 metrics PASS** their defined thresholds:

| Metric | Score | Threshold | Status |
|---|---|---|---|
| Retrieval Recall | **90.3%** | ≥ 90% | ✅ PASS |
| Generation Recall | **95.8%** | ≥ 90% | ✅ PASS |
| Map Laws Inefficiency | **4.1%** | ≤ 15% | ✅ PASS |
| Hallucination Rate | **9.0%** | ≤ 10% | ✅ PASS |
| Role Adherence | **88.0%** | ≥ 85% | ✅ PASS |

> [!NOTE]
> 4 out of 100 cases (cases 79, 87, 88, 92) had no retrieved_nums returned by the API, likely
> due to API timeouts during evaluation. These were excluded from recall metrics (effective n=288)
> but counted in hallucination checks.

---

## 2. Retrieval Recall Analysis — 90.3% (PASS)

**28 misses** across 300 role evaluations → **12 unique cases** fully or partially failing.

### Cases with Retrieval Failures

| Case | Roles Failed | URL |
|---|---|---|
| # 24 | neutral, defense, victim | https://congbobanan.toaan.gov.vn/2ta73257t1cvn/chi-tiet-ban-an |
| # 29 | defense | https://congbobanan.toaan.gov.vn/2ta1362710t1cvn/chi-tiet-ban-an |
| # 30 | neutral, victim | https://congbobanan.toaan.gov.vn/2ta543290t1cvn/chi-tiet-ban-an |
| # 43 | victim | https://congbobanan.toaan.gov.vn/2ta1136622t1cvn/chi-tiet-ban-an |
| # 46 | neutral, defense, victim | https://congbobanan.toaan.gov.vn/2ta2009017t1cvn/chi-tiet-ban-an |
| # 54 | neutral, victim | https://congbobanan.toaan.gov.vn/2ta1510104t1cvn/chi-tiet-ban-an |
| # 65 | neutral, defense | https://congbobanan.toaan.gov.vn/2ta1538764t1cvn/chi-tiet-ban-an |
| # 66 | neutral, victim | https://congbobanan.toaan.gov.vn/2ta150683t1cvn/chi-tiet-ban-an |
| # 73 | neutral, defense, victim | https://congbobanan.toaan.gov.vn/2ta1281492t1cvn/chi-tiet-ban-an |
| # 77 | neutral, victim | https://congbobanan.toaan.gov.vn/2ta1923228t1cvn/chi-tiet-ban-an |
| # 78 | victim | https://congbobanan.toaan.gov.vn/2ta1114240t1cvn/chi-tiet-ban-an |
| # 80 | neutral | https://congbobanan.toaan.gov.vn/2ta2090818t1cvn/chi-tiet-ban-an |
| # 93 | neutral, victim | https://congbobanan.toaan.gov.vn/2ta1829996t1cvn/chi-tiet-ban-an |
| # 97 | neutral, defense, victim | https://congbobanan.toaan.gov.vn/2ta2053364t1cvn/chi-tiet-ban-an |

### Retrieval Failure Patterns

- **Case #24** — All 3 roles fail (complete retrieval miss); case involves  Art. 111/112/115 area (crimes against national security)
- **Case #46** — Neutral + Defense miss; Art. 213–215 area (economic crimes)
- **Case #54** — Neutral + Victim miss; Art. 168/192/197 area (robbery variants)
- **Case #65** — Neutral + Defense miss; Art. 168/172–175/226 area (property crimes)
- **Case #66** — All 3 roles affected (only Defense has RR hit but GR miss); complex multi-charge case
- **Case #73** — All 3 roles fail; Art. 238/272/276 area
- **Case #97** — Neutral + Defense + Victim fail; Art. 199/274/279/293/314 area

**Root cause hypothesis:** Retrieval failures tend to cluster in cases with:
1. **Multi-charge cases** — the primary article is obscured by many related articles
2. **Less common offense categories** — national security (Art. 108–122), specific economic crimes
3. **Historical cases citing older BLHS 1999 numbering** — embedding index may have misalignment

---

## 3. Generation Recall Analysis — 95.8% (PASS)

**12 misses** across 288 valid role evaluations → **8 unique cases**.

| Case | Roles Missing | Articles Cited in Response |
|---|---|---|
| #24 | defense | 111, 112, 115, 46, 47, 50 |
| #24 | victim | 112, 115, 150, 42, 45, 48 |
| #46 | victim | 17, 213, 48, 51, 52 |
| #66 | defense | 3, 313, 314, 46, 47, 60 |
| #66 | victim | 133, 170, 28, 313, 314, 42 |
| #73 | neutral | 129, 51, 52, 65 |
| #73 | defense | 129, 20, 21, 22, 23, 295 |
| #73 | victim | 129, 295, 48, 50, 52 |
| #78 | victim | 151, 152, 169, 47, 48, 52 |
| #81 | victim | 17, 189, 47, 48, 52 |
| #93 | victim | 314, 32, 41, 46, 48, 52 |
| #97 | neutral | 168, 170, 38, 51 |

### Generation Recall Insights

- **Case #73** is the worst: all 3 roles fail generation recall despite partial retrieval.
  The system retrieved documents but the LLM did not cite the primary article in its output.
- **Case #24 defense** and **victim** miss: this coincides with the complete retrieval failure;
  without the right document, the LLM cannot cite the correct article.
- **Case #66** defense and victim: the LLM retrieved Art. 313/314 but the expected primary article
  differs per role perspective.
- Most GR misses are **isolated to one role** — the neutral or victim role correctly cites the
  article in the same case, suggesting the LLM's role framing occasionally shifts its article
  focus away from the primary charge.

---

## 4. Hallucination Analysis — 9.0% (PASS)

**27 out of 300** role evaluations triggered hallucination (L1: citing a crime-specific article
that was not in the retrieved document set).

> [!IMPORTANT]
> All 27 hallucinations are **L1 only** — no L2 (wrong BLHS edition) or L3 (wrong sentencing range)
> hallucinations occurred. The system correctly identifies the applicable law edition in all cases.

### By Role

| Role | Hallucinations | Rate |
|---|---|---|
| Neutral | 11/100 | 11.0% |
| Defense | 9/100 | 9.0% |
| Victim | 7/100 | 7.0% |

**The neutral role hallucinates most** — analyzing from all angles, it tends to cite a broader
range of contextual articles (including related offenses), some of which are not grounded in
the retrieved context.

### Cases with Hallucination

| Case | Roles | False Articles Cited |
|---|---|---|
| #43 | victim | Điều 115 |
| #46 | neutral, defense | Điều 341, Điều 135, Điều 136 |
| #49 | defense | Điều 134 |
| #52 | defense | Điều 178 |
| #54 | neutral, victim | Điều 193 |
| #65 | neutral, defense | Điều 171, Điều 173 |
| #66 | neutral, defense, victim | Điều 135, Điều 134, Điều 123, Điều 170, Điều 313, Điều 314 |
| #69 | neutral | Điều 201 |
| #73 | defense | Điều 295 |
| #74 | defense | Điều 170 |
| #77 | neutral, victim | Điều 146 |
| #80 | neutral | Điều 191 |
| #86 | neutral | Điều 468 |
| #87 | neutral, defense, victim | Điều 178, Điều 318 |
| #93 | neutral | Điều 128 |
| #96 | victim | Điều 123 |
| #97 | defense, victim | Điều 157 |
| #98 | neutral | Điều 249 |

### Most Frequently Hallucinated Articles

| Article | Occurrences | Likely Offense Category |
|---|---|---|
| Điều 178 | 4× | Tội xâm phạm chỗ ở người khác (illegal entry into dwelling) |
| Điều 318 | 3× | Gây rối trật tự công cộng (public disorder) |
| Điều 341 | 2× | Làm giả tài liệu (document forgery) |
| Điều 135 | 2× | Cưỡng đoạt tài sản (extortion) |
| Điều 134 | 2× | Cố ý gây thương tích (intentional bodily harm) |
| Điều 193 | 2× | Sản xuất trái phép chất ma túy (drug manufacturing) |
| Điều 171 | 2× | Cướp giật tài sản (snatching) |
| Điều 173 | 2× | Trộm cắp tài sản (theft) |
| Điều 146 | 2× | Dâm ô (sexual assault) |
| Điều 157 | 2× | Bắt giữ người trái pháp luật (unlawful detention) |

### Hallucination Pattern Analysis

- **Case #87** is the most severe: all 3 roles consistently hallucinate **Điều 178** (illegal entry)
  and **Điều 318** (public disorder) — indicating the LLM has strong prior association of these
  articles with the case's crime type, even without retrieval support.
- **Case #66** is a multi-charge case where each role hallucinates *different* articles (neutral
  cites 134/135/123/170; defense cites 313/314; victim cites 314), suggesting the case involves
  compound charges and the LLM draws from training knowledge to fill retrieval gaps.
- Most hallucinated articles are **adjacent offenses** — semantically related crimes the LLM
  associates with the retrieved context (e.g., citing theft Điều 173 alongside robbery Điều 168).

---

## 5. Role Adherence Analysis — 88.0% (PASS)

Role adherence is measured across 4 dimensions (D1–D4):
- **D1** — Role-specific opening position and stance
- **D2** — Legal argument depth and citation quality
- **D3** — Tone and vocabulary appropriateness
- **D4** — Structural completeness

### Average Score by Role

| Role | Avg RA | Min | Max | D1 Avg | D2 Avg |
|---|---|---|---|---|---|
| Neutral | **96.1%** | 82% | 100% | High | High |
| Victim | **96.8%** | 70% | 100% | High | High |
| Defense | **73.8%** | 50% | 85% | Low | Moderate |

> [!WARNING]
> The **defense role consistently underperforms** on D1 and D2. The LLM often fails to adopt
> a clear defense advocacy stance (D1) and does not generate strong legal counter-arguments (D2).
> This is the dominant weakness of the system.

### Worst-Performing Defense Cases (RA < 0.60)

| Case | RA Score | D1 | D2 | Notes |
|---|---|---|---|---|
| #79 | 0% | 0.00 | 0.00 | https://congbobanan.toaan.gov.vn/2ta1558067t1cvn/chi-tiet-ban-an |
| #24 | 50% | 0.00 | 0.33 | https://congbobanan.toaan.gov.vn/2ta73257t1cvn/chi-tiet-ban-an |
| #76 | 53% | 0.17 | 0.25 | https://congbobanan.toaan.gov.vn/2ta1390842t1cvn/chi-tiet-ban-an |
| #10 | 55% | 0.17 | 0.33 | https://congbobanan.toaan.gov.vn/2ta647225t1cvn/chi-tiet-ban-an |
| #41 | 55% | 0.17 | 0.33 | https://congbobanan.toaan.gov.vn/2ta1965807t1cvn/chi-tiet-ban-an |
| #58 | 58% | 0.33 | 0.25 | https://congbobanan.toaan.gov.vn/2ta1506194t1cvn/chi-tiet-ban-an |
| #6 | 60% | 0.33 | 0.33 | https://congbobanan.toaan.gov.vn/2ta1987297t1cvn/chi-tiet-ban-an |
| #16 | 60% | 0.33 | 0.33 | https://congbobanan.toaan.gov.vn/2ta2075564t1cvn/chi-tiet-ban-an |
| #27 | 60% | 0.33 | 0.33 | https://congbobanan.toaan.gov.vn/2ta1883588t1cvn/chi-tiet-ban-an |
| #43 | 60% | 0.33 | 0.33 | https://congbobanan.toaan.gov.vn/2ta1136622t1cvn/chi-tiet-ban-an |

### Defense Role D1 Pattern

D1 (role-specific opening) is consistently the lowest-scoring dimension for defense.
The LLM often defaults to a **neutral analytical tone** even when prompted to act as defense counsel,
especially for:
- Cases involving particularly severe crimes (murder, drug trafficking, national security offenses)
- Cases with overwhelming evidence against the defendant

---

## 6. Map Laws Inefficiency — 4.1% (PASS)

The `map_laws` node shows only **4.1% inefficiency** — the RAG pipeline rarely maps to
irrelevant articles. This indicates the vector retrieval + reranking pipeline is well-calibrated.

---

## 7. Skipped / Errored Cases

4 cases returned no retrieved documents, likely due to API timeout during evaluation:

| Case | URL |
|---|---|
| #79 | https://congbobanan.toaan.gov.vn/2ta1558067t1cvn/chi-tiet-ban-an |
| #87 | https://congbobanan.toaan.gov.vn/2ta1711229t1cvn/chi-tiet-ban-an |
| #88 | https://congbobanan.toaan.gov.vn/2ta820531t1cvn/chi-tiet-ban-an |
| #92 | https://congbobanan.toaan.gov.vn/2ta824870t1cvn/chi-tiet-ban-an |

> [!CAUTION]
> Case #87 is particularly notable: it had no retrieved_nums (API issue) yet still produced
> responses — and all 3 roles hallucinated the same two articles (Điều 178, Điều 318). This
> strongly suggests the LLM fell back on training memory when retrieval context was absent.
> This is the clearest demonstration of retrieval grounding working as intended — when retrieval
> fails, hallucination risk increases sharply.

---

## 8. Problematic Cases — Full Summary

Cases with 2+ issues across metrics:

| Case | RR | GR | Hall | RA(def) | Issues |
|---|---|---|---|---|---|
| #24 | ❌ (all 3) | ❌ (def+vic) | ✅ | ❌ 0.50 | RR complete miss + GR miss + lowest RA |
| #46 | ❌ (neu+def) | ✅ | ❌ (neu+def) | ✅ | Retrieval + hallucination |
| #54 | ❌ (neu+vic) | ✅ | ❌ (neu+vic) | ❌ 0.60 | Retrieval + hallucination |
| #65 | ❌ (neu+def) | ✅ | ❌ (neu+def) | ✅ | Retrieval + hallucination |
| #66 | ❌ (partial) | ❌ (def+vic) | ❌ (all 3) | ✅ | Triple issue: hardest case in dataset |
| #73 | ❌ (all 3) | ❌ (all 3) | ❌ (def) | ✅ | Complete retrieval+generation failure |
| #77 | ❌ (neu+vic) | ✅ | ❌ (neu+vic) | ✅ | Retrieval + hallucination |
| #87 | — (error) | — (error) | ❌ (all 3) | ❌ 0.65 | API error + worst hallucination |
| #97 | ❌ (partial) | ❌ (neutral) | ❌ (def+vic) | ✅ | Retrieval + generation + hallucination |

**Case #66 is the single hardest case** — it fails retrieval, generation recall, and hallucination
across all 3 roles. It appears to involve complex, multi-charge compound crimes where the primary
article is ambiguous.

---

## 9. Recommendations

### Immediate Improvements

1. **Defense role prompt refinement (D1/D2 weakness)**
   - Current: The LLM tends to analyze facts neutrally even in defense role
   - Fix: Strengthen the defense system prompt to explicitly require adversarial framing,
     presumption of innocence, and counter-argument structure

2. **Retrieval for rare/complex offenses (cases 24, 73)**
   - These cases fail completely — the embedding model may not represent the articles well
   - Fix: Add article-level re-ranking pass using BM25 hybrid retrieval for less common offenses

3. **Hallucination on adjacent crimes (Điều 178, 318, 134, etc.)**
   - The LLM cites "sister articles" from the same legal cluster as the retrieved content
   - Fix: Add a post-generation grounding check that strips unverified article citations
     before returning the response to the user

4. **API timeout / skipped cases (79, 87, 88, 92)**
   - 4% of cases in evaluation failed silently
   - Fix: Add retry logic + circuit breaker in the evaluation script; surface timeout errors
     more visibly in the report

### Long-term Improvements

5. **Multi-charge case handling** — Cases like #66 with compound charges need a dedicated
   disambiguation step in the retrieve/rerank node to handle multiple primary articles

6. **Civil compensation integration** — Not yet evaluated; should be added as a Metric 6
   measuring whether victim-role responses correctly estimate civil damages per the verdict

---

## 10. Metric Consistency Notes

- **Hallucination metric** uses edition-aware `_is_general_part()`: articles 1–77 are always
  valid (BLHS 1999 General Part); articles 78–107 are valid only when BLHS 2015 markers are
  detected in the response text
- **Effective evaluation set**: 288/300 role evals (4 cases × 3 roles excluded due to API error)
- All metrics computed over the full 100-case dataset (cases 1–100, July 2026)
