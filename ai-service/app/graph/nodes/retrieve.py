"""
graph/nodes/retrieve.py — VNPLaw AI Service
Nodes: multi_query_rewrite, parallel_retrieve, temporal_priority_tagger, rerank_node
"""
import re
import json
from typing import List

from langchain_core.messages import HumanMessage
from langchain_core.documents import Document

from app.core.config import (
    COLLECTION_NAME, _EDITION_RANGES, _ALWAYS_KEEP_BY_EDITION,
    _PINNED_MAP, _PINNED_PURPOSES, _ROLE_CIRCUMSTANCE_INSTRUCTION, _MAX_SEMANTIC_DOCS,
)
from app.core.schemas import AgentState
from app.utils.dates import _edition_for_date
from app.utils.legal import _extract_json
from app.utils.text import _sanitize_msgs, sanitize_text


def make_retrieve_nodes(llm, retriever, milvus_client, bm25_index, bm25_docs,
                        output_fields, rerank_scores_fn, measure_time):
    """Factory: returns all retrieval-related node callables."""

    @measure_time('multi_query_rewrite')
    def multi_query_rewrite(state: AgentState) -> dict:
        print("[NODE: multi_query_rewrite]")
        facts     = state.get("extracted_facts") or {}
        role      = state.get("user_role", "neutral")
        case_text = state.get("full_case_content", state["question"])
        circumstance_instruction = _ROLE_CIRCUMSTANCE_INSTRUCTION.get(
            role, _ROLE_CIRCUMSTANCE_INSTRUCTION["neutral"]
        )

        prompt = f"""Ban la chuyen gia phan tich ho so phap ly hinh su Viet Nam.
Dua vao noi dung vu an va cac su kien da trich xuat, hay tao 3 cau truy van
de tim kiem dieu luat phu hop trong co so du lieu phap luat.

NOI DUNG VU AN (nguon du lieu duy nhat):
{case_text}

SU KIEN DA TRICH XUAT:
{json.dumps(facts, ensure_ascii=False, indent=2)}

QUY TAC BAT BUOC:
1. CHI su dung thong tin co trong "NOI DUNG VU AN" hoac "SU KIEN DA TRICH XUAT".
2. TUYET DOI KHONG them thong tin, suy luan, hoac bia dat bat ky chi tiet nao.
3. KHONG duoc viet ten dieu luat, so dieu khoan (vi du "Dieu 168", "Dieu 51").
4. KHONG dung ngon ngu toa an nhu "Toa an ap dung", "can cu vao", "bi truy to ve toi".
5. Viet bang tieng Viet, van phong ban an thuc te (ngoi thu ba, thi qua khu).
6. Neu khong co thong tin cho mot truy van to ve null.

YEU CAU:
- behavior_query: Tom tat toan bo su kien vu an bang van phong chuyen nghiep (150-300 tu).
  Cau truc BAT BUOC phai bao gom: ai pham toi, lam gi cu the, voi ai/doi tuong nao,
  bang phuong tien/cong cu gi, thoi diem/hoan canh, hau qua thuc te xay ra.
- circumstance_query: {circumstance_instruction}
- evidence_query: Mo ta tang vat, cong cu pham toi, so luong, trong luong,
  gia tri tai san cu the co trong vu an. Neu khong co tang vat -> null.

TRA VE JSON (null neu khong co thong tin):
{{"behavior_query": "...", "circumstance_query": "...", "evidence_query": "..."}}
OUTPUT: CHI JSON hop le, khong markdown, khong giai thich."""

        try:
            response = llm.invoke(_sanitize_msgs([HumanMessage(content=prompt)]))
            queries = _extract_json(response.content)
            raw_q_list = [
                queries.get("behavior_query"),
                queries.get("circumstance_query"),
                queries.get("evidence_query"),
            ]
            q_list = [
                q for q in raw_q_list if q and str(q).strip().lower() != "null"
            ]
            if not q_list:
                raise ValueError("All queries null")
        except Exception:
            hanh_vi  = facts.get("hanh_vi", "")
            hau_qua  = facts.get("hau_qua", "")
            tang_vat = facts.get("tang_vat_loai", "")
            giam_nhe = ", ".join(facts.get("tinh_tiet_giam_nhe") or [])
            tang_nang = ", ".join(facts.get("tinh_tiet_tang_nang") or [])
            q1 = f"{hanh_vi}. {hau_qua}".strip(". ") or case_text[:300]
            q2 = (
                f"{giam_nhe}. {tang_nang}".strip(". ")
                or hanh_vi
                or case_text[:300]
            )
            raw_q_list = [q1, q2, tang_vat or "null"]
            q_list = [q for q in [q1, q2, tang_vat or None] if q]

        print(f"  [REWRITE] Generated {len(raw_q_list)} queries for role={role!r}")
        for i, q in enumerate(raw_q_list):
            display_q = str(q).strip() if q else "null"
            print(f"  \u250c\u2500 Q{i+1} {'\u2500'*60}")
            print(f"  \u2502 {display_q}")
            print(f"  \u2514{'\u2500'*63}")
        print(f"  [REWRITE] Optimized to {len(q_list)} valid queries for execution.")
        return {"retrieval_queries": q_list}

    @measure_time('parallel_retrieve')
    def parallel_retrieve(state: AgentState) -> dict:
        print("[NODE: parallel_retrieve]")
        queries       = state.get("retrieval_queries") or [state["question"]]
        role          = state.get("user_role", "neutral")
        facts         = state.get("extracted_facts") or {}
        per_defendant = state.get("per_defendant_dates") or []
        seen_ids: set = set()
        all_docs: List[Document] = []

        Q1_SEMANTIC_K    = 20
        Q2_Q3_SEMANTIC_K = 10
        for q_idx, q in enumerate(queries, start=1):
            if not q:
                continue
            sem_k = Q1_SEMANTIC_K if q_idx == 1 else Q2_Q3_SEMANTIC_K
            print(f"  [SEMANTIC Q{q_idx}] (k={sem_k}) {q[:100]!r}...")
            try:
                docs = retriever.invoke(q, top_k_override=sem_k)
                added_sem = 0
                for d in docs:
                    key = (d.metadata.get("article_number", ""), d.metadata.get("source", ""))
                    if key not in seen_ids:
                        seen_ids.add(key)
                        all_docs.append(d)
                        added_sem += 1
                print(f"    \u2192 {len(docs)} retrieved, {added_sem} new unique")
            except Exception as e:
                print(f"  [SEMANTIC ERROR Q{q_idx}] {type(e).__name__}: {e}")

        BM25_TOP_K = 5
        if bm25_index is not None and bm25_docs:
            for q_idx, q in enumerate(queries, start=1):
                if not q:
                    continue
                try:
                    tokenized_q = q.lower().split()
                    scores      = bm25_index.get_scores(tokenized_q)
                    top_indices = sorted(
                        range(len(scores)),
                        key=lambda i: scores[i],
                        reverse=True,
                    )[:BM25_TOP_K]
                    added_bm25 = 0
                    skipped    = 0
                    print(f"  [BM25 Q{q_idx}] Scoring {len(scores)} docs, top {BM25_TOP_K} candidates:")
                    for idx in top_indices:
                        if scores[idx] <= 0:
                            break
                        d   = bm25_docs[idx]
                        art = d.metadata.get("article_number", "?")
                        src = d.metadata.get("source", "?")
                        key = (d.metadata.get("article_number", ""), d.metadata.get("source", ""))
                        if key not in seen_ids:
                            seen_ids.add(key)
                            all_docs.append(Document(
                                page_content=d.page_content,
                                metadata={**d.metadata, "_retrieval_source": "bm25"},
                            ))
                            added_bm25 += 1
                            print(f"    [BM25] score={scores[idx]:.4f}  Dieu {art} | {src}")
                        else:
                            skipped += 1
                            print(f"    [BM25 DUP] score={scores[idx]:.4f}  Dieu {art} | {src} -- already in pool")
                    print(f"    \u2192 {added_bm25} new unique, {skipped} duplicate(s) skipped")
                except Exception as _bm25_q_err:
                    print(f"  [BM25 ERROR Q{q_idx}] {type(_bm25_q_err).__name__}: {_bm25_q_err} -- skipping")
        else:
            if bm25_index is None:
                print("  [BM25] Index not available -- keyword retrieval skipped")

        if per_defendant:
            crime_editions = [
                _edition_for_date(d.get("ngay_pham_toi", ""))
                for d in per_defendant
            ]
            crime_editions = [e for e in crime_editions if e]
        else:
            single = _edition_for_date(facts.get("ngay_pham_toi", ""))
            crime_editions = [single] if single else []

        pinned_purposes = _PINNED_PURPOSES.get(role, _PINNED_PURPOSES["neutral"])
        has_exclusion_indicators = bool(
            (state.get("extracted_facts") or {}).get("co_dau_hieu_loai_tru_tnhs")
        )
        if has_exclusion_indicators:
            conditional_purposes = list(pinned_purposes) + ["self_defense", "necessity"]
            print("  [COND-PIN] Exclusion indicators found -- adding self_defense/necessity to pin list")
        else:
            conditional_purposes = list(pinned_purposes)

        for edition in crime_editions:
            for purpose in conditional_purposes:
                art_no = _PINNED_MAP.get((purpose, edition))
                if not art_no:
                    print(f"  [PINNED] No mapping for ({purpose}, {edition!r}) -- skip")
                    continue
                key = (art_no, edition)
                if key in seen_ids:
                    print(f"  [PINNED] Dieu {art_no} ({purpose}) from {edition!r} -- already in pool")
                    continue
                try:
                    hits = milvus_client.query(
                        collection_name=COLLECTION_NAME,
                        filter=f'article_number == "{art_no}" and source == "{edition}"',
                        output_fields=output_fields,
                        limit=1,
                    )
                    for h in hits:
                        doc = Document(
                            page_content=sanitize_text(h.get("content", "")),
                            metadata={
                                k: sanitize_text(h.get(k, "")) if isinstance(h.get(k, ""), str) else h.get(k, "")
                                for k in output_fields if k != "content"
                            },
                        )
                        doc.metadata["_pinned"]  = True
                        doc.metadata["_purpose"] = purpose
                        all_docs.append(doc)
                        seen_ids.add(key)
                        print(f"  [PINNED] Dieu {art_no} ({purpose}) from {edition!r}")
                except Exception as e:
                    print(f"  [PINNED] Failed to fetch Dieu {art_no} ({purpose}): {e}")

        n_pinned   = sum(1 for d in all_docs if d.metadata.get("_pinned"))
        n_bm25     = sum(1 for d in all_docs if d.metadata.get("_retrieval_source") == "bm25")
        n_semantic = len(all_docs) - n_pinned - n_bm25
        print(f"  [RETRIEVE] Total: {len(all_docs)} docs "
              f"(semantic={n_semantic}, bm25={n_bm25}, pinned={n_pinned})")
        return {"documents": all_docs}

    @measure_time('temporal_priority_tagger')
    def temporal_priority_tagger(state: AgentState) -> dict:
        print("[NODE: temporal_priority_tagger]")
        docs  = state.get("documents", [])
        facts = state.get("extracted_facts") or {}
        trial_edition = _edition_for_date(facts.get("ngay_xet_xu", ""))

        per_defendant = state.get("per_defendant_dates") or []
        if per_defendant:
            updated_per_defendant = []
            for d_info in per_defendant:
                edition = _edition_for_date(d_info.get("ngay_pham_toi", "")) or ""
                updated_per_defendant.append({**d_info, "crime_edition": edition})
            crime_editions = {d["crime_edition"] for d in updated_per_defendant if d["crime_edition"]}
            per_defendant  = updated_per_defendant
            print(f"  [TEMPORAL] Multi-defendant mode: editions={crime_editions}")
        else:
            single = _edition_for_date(facts.get("ngay_pham_toi", ""))
            crime_editions = {single} if single else set()

        if not crime_editions:
            print("  [TEMPORAL] Cannot determine any crime edition -- passing all docs")
            return {"documents": docs}

        needs_comparison = any(e != trial_edition for e in crime_editions)
        print(f"  [TEMPORAL] Crime editions: {crime_editions} | Trial: {trial_edition}")
        print(f"  [TEMPORAL] Retroactivity comparison needed: {needs_comparison}")

        tagged = []
        newer  = []
        always = []

        all_known_editions = {e[0] for e in _EDITION_RANGES}
        relevant_editions  = set(crime_editions)
        if trial_edition:
            relevant_editions.add(trial_edition)

        for d in docs:
            art_no = str(d.metadata.get("article_number", ""))
            src    = d.metadata.get("source", "")

            if src in all_known_editions and src not in relevant_editions:
                continue

            try:
                art_num_match = re.search(r"\d+", str(art_no))
                art_val = int(art_num_match.group(0)) if art_num_match else 9999
            except Exception:
                art_val = 9999

            is_general_part = False
            if "1999" in src and art_val <= 77:
                is_general_part = True
            elif "2015" in src and art_val <= 107:
                is_general_part = True

            always_keep = _ALWAYS_KEEP_BY_EDITION.get(src, set())

            if art_no in always_keep or is_general_part:
                d.metadata["_temporal_role"] = "adjustment"
                always.append(d)
            elif src in crime_editions:
                d.metadata["_temporal_role"] = "primary"
                d.metadata["_primary_for"] = [
                    di["name"] for di in per_defendant
                    if di.get("crime_edition") == src
                ] or ["all"]
                tagged.append(d)
            elif needs_comparison and src == trial_edition:
                d.metadata["_temporal_role"] = "comparison"
                newer.append(d)

        ordered = tagged + newer + always
        result  = ordered if ordered else docs

        stats = {
            "primary":    {"sem": 0, "bm25": 0, "pin": 0},
            "comparison": {"sem": 0, "bm25": 0, "pin": 0},
            "adjustment": {"sem": 0, "bm25": 0, "pin": 0},
        }
        def _tally(dl, rk):
            for d in dl:
                if d.metadata.get("_pinned"):                         stats[rk]["pin"]  += 1
                elif d.metadata.get("_retrieval_source") == "bm25":   stats[rk]["bm25"] += 1
                else:                                                  stats[rk]["sem"]  += 1
        _tally(tagged, "primary")
        _tally(newer,  "comparison")
        _tally(always, "adjustment")

        print(
            f"  [TEMPORAL] primary={len(tagged)} "
            f"(sem:{stats['primary']['sem']} bm25:{stats['primary']['bm25']} pin:{stats['primary']['pin']}), "
            f"comparison={len(newer)} "
            f"(sem:{stats['comparison']['sem']} bm25:{stats['comparison']['bm25']} pin:{stats['comparison']['pin']}), "
            f"adjustment={len(always)} "
            f"(sem:{stats['adjustment']['sem']} bm25:{stats['adjustment']['bm25']} pin:{stats['adjustment']['pin']})"
        )
        return {"documents": result, "per_defendant_dates": per_defendant}

    @measure_time('rerank')
    def rerank_node(state: AgentState) -> dict:
        print("[NODE: rerank]")
        docs = state.get("documents", [])

        if not docs:
            return {"documents": []}

        retrieval_queries = state.get("retrieval_queries") or []
        query = (
            retrieval_queries[0]
            if retrieval_queries
            else (state.get("full_case_content") or state["question"])
        )

        pinned_docs     = [d for d in docs if d.metadata.get("_pinned")]
        adjustment_docs = [
            d for d in docs
            if not d.metadata.get("_pinned") and d.metadata.get("_temporal_role") == "adjustment"
        ]
        semantic_docs   = [
            d for d in docs
            if not d.metadata.get("_pinned") and d.metadata.get("_temporal_role") != "adjustment"
        ]

        if semantic_docs:
            _q     = query[:1024]
            pairs  = [(_q, d.page_content) for d in semantic_docs]
            scores = rerank_scores_fn(pairs)
            ranked_semantic = sorted(zip(scores, semantic_docs), key=lambda x: x[0], reverse=True)
            top_semantic    = [doc for _, doc in ranked_semantic[:_MAX_SEMANTIC_DOCS]]
        else:
            ranked_semantic = []
            top_semantic    = []

        top_docs = top_semantic + adjustment_docs + pinned_docs

        if not top_docs:
            return {"documents": []}

        print(f"  [RERANK] query[:80]: {query[:80]!r}")
        print(
            f"  [RERANK] {len(docs)} \u2192 {len(top_docs)} docs "
            f"(semantic_kept={len(top_semantic)}/{len(semantic_docs)}, "
            f"adjustment_kept={len(adjustment_docs)}, pinned={len(pinned_docs)})"
        )
        for score, doc in ranked_semantic[:_MAX_SEMANTIC_DOCS]:
            art  = doc.metadata.get("article_number", "?")
            src  = doc.metadata.get("source", "?")
            role = doc.metadata.get("_temporal_role", "?")
            print(f"    [sem] score={score:.4f}  Dieu {art} | {src} | {role}")
        for doc in adjustment_docs:
            art = doc.metadata.get("article_number", "?")
            src = doc.metadata.get("source", "?")
            print(f"    [adj] Dieu {art} | {src} | always-keep")
        for doc in pinned_docs:
            art     = doc.metadata.get("article_number", "?")
            src     = doc.metadata.get("source", "?")
            purpose = doc.metadata.get("_purpose", "?")
            print(f"    [pin] Dieu {art} | {src} | purpose={purpose}")

        return {"documents": top_docs}

    return {
        "multi_query_rewrite":      multi_query_rewrite,
        "parallel_retrieve":        parallel_retrieve,
        "temporal_priority_tagger": temporal_priority_tagger,
        "rerank":                   rerank_node,
    }
