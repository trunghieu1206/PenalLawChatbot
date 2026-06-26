"""
services/embeddings.py — VNPLaw AI Service
Jina v5 Nano embedding wrapper and Milvus vector retriever.
"""


from typing import List, Optional

import torch
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pymilvus import MilvusClient

from app.core.config import (
    COLLECTION_NAME,
    TOP_K,
    OUTPUT_FIELDS,
)
from app.utils.text import sanitize_text


# ===========================================================
# JINA EMBEDDINGS CLASS  — uses SentenceTransformer
# ===========================================================
class JinaEmbeddings(Embeddings):
    """
    LangChain-compatible embeddings wrapper for Jina v5 Nano
    (or any SentenceTransformer model that accepts a 'task' kwarg).

    Both documents and queries use task='retrieval' as recommended
    by the Jina retrieval model documentation.
    """

    def __init__(
        self,
        model_name: str = "trunghieu1206/jina-embeddings-v5-text-nano-retrieval-vn-legal-lora-2026-04-28-19-05",
        device: Optional[str] = None,
        batch_size: int = 32,
    ):
        from sentence_transformers import SentenceTransformer

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        print(f"🔄 Loading Jina model '{model_name}' on {device}...")
        self._model = SentenceTransformer(
            model_name,
            trust_remote_code=True,
            device=device,
        )
        self._batch_size = batch_size

        # Probe once whether this model supports task= (base Jina does, LoRA adapters may not)
        try:
            self._model.encode(["probe"], task="retrieval", show_progress_bar=False)
            self._supports_task = True
            print("✅ Jina embedding model loaded (task= supported).")
        except TypeError:
            self._supports_task = False
            print("✅ Jina embedding model loaded (task= not supported — fine-tuned adapter).")

    def _encode(self, texts: List[str]) -> List[List[float]]:
        kwargs = dict(
            normalize_embeddings=True,
            batch_size=self._batch_size,
            show_progress_bar=False,
        )
        if self._supports_task:
            kwargs["task"] = "retrieval"
        vecs = self._model.encode(texts, **kwargs)
        return vecs.tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._encode([text])[0]


# ===========================================================
# MILVUS RETRIEVER — thin wrapper around MilvusClient.search
# ===========================================================
class MilvusRetriever:
    """
    Retriever that performs dense (ANN) search against a Milvus Lite collection.

    Uses COSINE similarity metric (matching the collection's index config).
    top_k_override allows per-call overriding of the default TOP_K value,
    which is used by parallel_retrieve to apply asymmetric k-values across queries
    (Q1=20, Q2/Q3=10).
    """

    def __init__(
        self,
        milvus_client: MilvusClient,
        embeddings: JinaEmbeddings,
        collection_name: str = COLLECTION_NAME,
        top_k: int = TOP_K,
        output_fields: List[str] = None,
    ):
        self._client = milvus_client
        self._emb = embeddings
        self._col = collection_name
        self._k = top_k
        self._fields = output_fields or list(OUTPUT_FIELDS)

    def invoke(self, query: str, top_k_override: Optional[int] = None) -> List[Document]:
        """Embed query, search Milvus with COSINE metric, return LangChain Documents."""
        limit = top_k_override if top_k_override is not None else self._k
        vec = self._emb.embed_query(query)

        results = self._client.search(
            collection_name=self._col,
            data=[vec],
            limit=limit,
            output_fields=self._fields,
            search_params={"metric_type": "COSINE"},
        )[0]

        # --- LOG RAG CHUNK IDs ---
        print(f"  [RAG] Retrieved {len(results)} chunks:")
        for r in results:
            ch  = r["entity"].get("chapter", "?")
            art = r["entity"].get("article_number", "?")
            src = r["entity"].get("source", "?")
            print(f"    ID={r['id']}  score={r['distance']:.4f}  | Chương: {ch}  Điều: {art}  [{src}]")
        # -------------------------

        docs: List[Document] = []
        for r in results:
            entity = r["entity"]
            docs.append(Document(
                page_content=sanitize_text(entity.get("content", "")),
                metadata={
                    f: entity.get(f, "")
                    for f in self._fields if f != "content"
                },
            ))
        return docs
