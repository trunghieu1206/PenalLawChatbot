"""
graph/builder.py — VNPLaw AI Service
Assembles the compiled LangGraph from node factories and service dependencies.
"""

import time
from functools import wraps
from typing import Any, List

from langchain_core.documents import Document
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.core.schemas import AgentState
from app.core.config import OUTPUT_FIELDS

# Node factories
from app.graph.nodes.extract import make_extract_nodes
from app.graph.nodes.retrieve import make_retrieve_nodes
from app.graph.nodes.law_mapping import make_map_laws_node
from app.graph.nodes.routing import classify_intent, application_mode_router
from app.graph.nodes.generation import make_generation_nodes


def _make_measure_time():
    """Return a @measure_time decorator that prints elapsed seconds per node."""
    def measure_time(name: str):
        def decorator(func):
            @wraps(func)
            def wrapper(state):
                start_t = time.time()
                result = func(state)
                elapsed = time.time() - start_t
                print(f"  ⏱️  [NODE: {name}] finished in {elapsed:.2f}s")
                return result
            return wrapper
        return decorator
    return measure_time


def build_graph(
    llm,
    retriever,
    milvus_client,
    reranker_fn,
    bm25_index,
    bm25_docs: List[Document],
) -> Any:
    """
    Build and compile the LangGraph workflow.

    Parameters
    ----------
    llm           : ChatOpenAI-compatible LLM instance (OpenRouter).
    retriever     : MilvusRetriever instance from lifespan.
    milvus_client : Raw MilvusClient — used by pinned-article lookup.
    reranker_fn   : Callable[[List[tuple]], List[float]] — scores (query, doc) pairs.
    bm25_index    : BM25Okapi instance or None if rank_bm25 not installed.
    bm25_docs     : List[Document] — corpus used for BM25 lookup.

    Returns
    -------
    Compiled LangGraph app with MemorySaver checkpointing.
    """
    measure_time = _make_measure_time()

    # ── Instantiate node functions from factories ─────────────────────────────
    extract_nodes  = make_extract_nodes(llm, measure_time)
    retrieve_nodes = make_retrieve_nodes(
        llm=llm,
        retriever=retriever,
        milvus_client=milvus_client,
        bm25_index=bm25_index,
        bm25_docs=bm25_docs,
        output_fields=list(OUTPUT_FIELDS),
        rerank_scores_fn=reranker_fn,
        measure_time=measure_time,
    )
    map_laws_node = make_map_laws_node(llm, measure_time)
    gen_nodes     = make_generation_nodes(
        llm=llm,
        bm25_index=bm25_index,
        bm25_docs=bm25_docs,
        retriever=retriever,
        measure_time=measure_time,
    )

    # ── Unpack named nodes ────────────────────────────────────────────────────
    extract_facts_node       = extract_nodes["extract_facts"]
    clarification_check_node = extract_nodes["clarification_check"]
    clarification_router     = extract_nodes["clarification_router"]
    clarification_node       = extract_nodes["clarification"]

    multi_query_rewrite_node      = retrieve_nodes["multi_query_rewrite"]
    parallel_retrieve_node        = retrieve_nodes["parallel_retrieve"]
    temporal_priority_tagger_node = retrieve_nodes["temporal_priority_tagger"]
    rerank_node                   = retrieve_nodes["rerank"]

    generate_node          = gen_nodes["generate"]
    answer_verify_node     = gen_nodes["answer_verify"]
    practice_evaluate_node = gen_nodes["practice_evaluate"]
    casual_respond_node    = gen_nodes["casual_respond"]
    followup_generate_node = gen_nodes["followup_generate"]

    # ── Build StateGraph ──────────────────────────────────────────────────────
    workflow = StateGraph(AgentState)

    workflow.add_node("extract_facts",            extract_facts_node)
    workflow.add_node("clarification_check",      clarification_check_node)
    workflow.add_node("clarification",            clarification_node)
    workflow.add_node("multi_query_rewrite",      multi_query_rewrite_node)
    workflow.add_node("parallel_retrieve",        parallel_retrieve_node)
    workflow.add_node("temporal_priority_tagger", temporal_priority_tagger_node)
    workflow.add_node("rerank",                   rerank_node)
    workflow.add_node("map_laws",                 map_laws_node)
    workflow.add_node("generate",                 generate_node)
    workflow.add_node("answer_verify",            answer_verify_node)
    workflow.add_node("practice_evaluate",        practice_evaluate_node)
    workflow.add_node("followup",                 followup_generate_node)
    workflow.add_node("casual",                   casual_respond_node)

    # START → 3-way intent router
    # NOTE: Practice Mode also enters via 'new_case' — is_practice_mode flag in state
    # ensures the application_mode_router sends it to 'practice_evaluate' at the end.
    workflow.add_conditional_edges(
        START,
        classify_intent,
        {"new_case": "extract_facts", "followup": "followup", "casual": "casual"}
    )
    workflow.add_edge("extract_facts",           "clarification_check")
    workflow.add_conditional_edges(
        "clarification_check",
        clarification_router,
        {"clarify": "clarification", "continue": "multi_query_rewrite"}
    )
    workflow.add_edge("clarification",            END)
    workflow.add_edge("multi_query_rewrite",      "parallel_retrieve")
    workflow.add_edge("parallel_retrieve",        "temporal_priority_tagger")
    workflow.add_edge("temporal_priority_tagger", "rerank")
    workflow.add_edge("rerank",                   "map_laws")

    # Application Mode Router: generate vs practice_evaluate
    workflow.add_conditional_edges(
        "map_laws",
        application_mode_router,
        {
            "practice_evaluate": "practice_evaluate",
            "generate":          "generate",
        }
    )
    # generate passes through the answer_verify quality gate
    workflow.add_edge("generate",          "answer_verify")
    workflow.add_edge("answer_verify",     END)
    # practice_evaluate bypasses verification
    workflow.add_edge("practice_evaluate", END)
    workflow.add_edge("followup",          END)
    workflow.add_edge("casual",            END)

    # ── MemorySaver: in-process checkpointing ────────────────────────────────
    # With a single uvicorn worker, MemorySaver stores AgentState per thread_id
    # (= session UUID) in a Python dict. On follow-up turns the checkpoint
    # restores Turn 1's documents, mapped_laws, extracted_facts, and
    # full_case_content — so the follow-up path gets reranked docs without re-retrieval.
    checkpointer = MemorySaver()
    compiled     = workflow.compile(checkpointer=checkpointer)
    print("✅ LangGraph compiled with MemorySaver checkpointing.")
    return compiled
