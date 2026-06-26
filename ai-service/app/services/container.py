"""
services/container.py — VNPLaw AI Service
Lightweight Services dataclass — holds all long-lived singleton dependencies
that are instantiated once in lifespan() and passed to build_graph().
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional

from langchain_core.documents import Document


@dataclass
class Services:
    """
    Holds all startup-time singleton services.

    Fields
    ------
    llm            : ChatOpenAI-compatible LLM (OpenRouter).
    retriever      : MilvusRetriever (thin wrapper over MilvusClient).
    milvus_client  : Raw MilvusClient — needed by pinned-article lookup in retrieve.py.
    reranker_fn    : Callable[[List[tuple]], List[float]] — raw-logit scorer.
    bm25_index     : BM25Okapi instance, or None if rank_bm25 not installed.
    bm25_docs      : In-memory corpus used by BM25 lookup.
    graph          : Compiled LangGraph application (set after build_graph()).
    model_loaded   : Boolean sentinel for /health endpoint.
    """
    llm:          Any
    retriever:    Any
    milvus_client: Any
    reranker_fn:  Any
    bm25_index:   Optional[Any]                = None
    bm25_docs:    List[Document]               = field(default_factory=list)
    graph:        Optional[Any]                = None
    model_loaded: bool                         = False
