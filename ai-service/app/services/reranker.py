"""
services/reranker.py — VNPLaw AI Service
Cross-encoder reranker loading and scoring helper.

Uses AutoModelForSequenceClassification directly to bypass sentence_transformers
wrapper layers that break on transformers ≥4.57 (prepare_for_model / BatchEncoding).
"""


from typing import List, Tuple


def load_reranker(model_name: str, device: str):
    """
    Load a reranker via AutoModelForSequenceClassification + AutoTokenizer.
    Returns (tokenizer, model) tuple, or (None, None) if loading fails.

    Using AutoModel directly is more stable than sentence_transformers.CrossEncoder
    or FlagEmbedding across transformers versions (avoids 4.57 prepare_for_model crash).
    """
    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification

        print(f"🔄 Loading reranker '{model_name}' on {device}...")
        tokenizer = AutoTokenizer.from_pretrained(model_name) # load tokenizer
        # load model (cross-encoder)
        model = AutoModelForSequenceClassification.from_pretrained(
            model_name,
            torch_dtype=torch.float16 if device.startswith("cuda") else torch.float32,
        )
        model.eval() # set model to evaluation mode (read-only) (if we dont do this then it might return a random answer everytime because it will go to training mode)
        model.to(device) # move model to device (if GPU available then move to GPU VRAM)
        prec = "fp16" if device.startswith("cuda") else "fp32"
        print(f"✅ Reranker loaded: {model_name} ({prec}, AutoModel direct, max_length=1024).")
        return tokenizer, model
    except Exception as e:
        print(f"⚠️  Reranker load failed ({type(e).__name__}: {e}) — reranking disabled.")
        return None, None


def make_rerank_scorer(tokenizer, model, device: str):
    """
    Returns a callable _rerank_scores(pairs, batch_size) → List[float].

    pairs: List[Tuple[str, str]] — (query, doc_text)
    Returns raw logits (higher = more relevant).
    If tokenizer/model is None, returns uniform zero scores (reranking disabled).
    """
    if tokenizer is None or model is None:
        # if model loading failed, then skip reranking step by returning uniform zero scores
        # so that retrieval module will still work
        def _scores_noop(pairs: List[Tuple[str, str]], batch_size: int = 8) -> List[float]:
            return [0.0] * len(pairs)
        return _scores_noop

    def _rerank_scores(pairs: List[Tuple[str, str]], batch_size: int = 8) -> List[float]:
        """Score (query, doc_text) pairs with the cross-encoder. Returns raw logits."""
        import torch
        all_scores: List[float] = []
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i: i + batch_size]
            with torch.no_grad(): # disable learning (which speed up inference and save memory)
                enc = tokenizer(
                    [p[0] for p in batch], # query texts
                    [p[1] for p in batch], # document texts
                    padding=True,
                    truncation=True,
                    max_length=1024, # truncate to 1024 tokens if law article is too long to speed up 
                    return_tensors="pt",
                ).to(device)
                logits = model(**enc).logits.view(-1).float() # get logit from model (each law article will have a logit)
            all_scores.extend(logits.cpu().tolist()) # add scores to list (if GPU is used then move to CPU first)
        return all_scores

    return _rerank_scores
