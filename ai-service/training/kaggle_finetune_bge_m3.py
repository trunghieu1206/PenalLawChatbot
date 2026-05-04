# ================================================================
# Fine-tune BGE-M3 on Vietnamese Legal Case Dataset
# ================================================================
# HOW TO USE ON KAGGLE:
#   1. Upload toaan_gov_datasets.json as a Kaggle Dataset
#      (New Dataset → upload file → name it "toaan-datasets")
#   2. In your notebook: Add data → search "toaan-datasets" → attach
#   3. Enable GPU: Settings → Accelerator → GPU T4 x2 (or P100)
#   4. Copy each cell block below into separate Kaggle code cells
# ================================================================


# ================================================================
# CELL 1 — Install dependencies
# ================================================================
import subprocess, sys

def pip_install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

print("Installing packages...")
pip_install("sentence-transformers==3.3.1")
pip_install("datasets")
pip_install("accelerate")
pip_install("tqdm")
print("✓ All packages installed")


# ================================================================
# CELL 2 — Imports
# ================================================================
import json
import random
import os
from collections import defaultdict

import torch
from tqdm.auto import tqdm                          # ← tqdm (auto picks notebook-aware bar)
from datasets import Dataset
from sentence_transformers import (
    SentenceTransformer,
    SentenceTransformerTrainer,
    SentenceTransformerTrainingArguments,
    losses,
    evaluation,
)
from transformers import TrainerCallback

print("PyTorch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))


# ================================================================
# CELL 3 — Load dataset
# ================================================================
import os, glob

# Auto-detect the dataset path — works regardless of the dataset name you chose
candidates = glob.glob("/kaggle/input/**/toaan_gov_datasets.json", recursive=True)

if not candidates:
    print("\n[ERROR] Dataset file not found!")
    print("Fix: Go to Kaggle notebook → Add Data (top right) →")
    print("     search for your dataset (toaan-datasets) → click Add")
    print("\nAvailable input files:")
    for root, dirs, files in os.walk("/kaggle/input"):
        for f in files:
            print(" ", os.path.join(root, f))
    raise FileNotFoundError("Attach the dataset to this notebook first.")

DATASET_PATH = candidates[0]
print(f"✓ Dataset found: {DATASET_PATH}")

print("Loading dataset...")
with open(DATASET_PATH, "r", encoding="utf-8") as f:
    raw_data = json.load(f)

print(f"✓ Total Q&A entries: {len(raw_data)}")
print("Sample entry:")
print(json.dumps(raw_data[0], ensure_ascii=False, indent=2))


# ================================================================
# CELL 4 — Build training pairs
# ================================================================
# Each entry in the dataset is already a complete (anchor, positive) pair:
#   anchor   = question        (case fact description)
#   positive = expected_article (the correct law article label)
#
# This uses ALL 5,886 entries directly — no grouping, no filtering, no cap.
# The model learns: "this legal situation" → "Điều 173 - BLHS 2015 (sửa đổi 2017)"

random.seed(42)

print("\nBuilding training pairs from all entries...")
anchors   = [entry["question"]           for entry in tqdm(raw_data, desc="Loading anchors",   unit="entry")]
positives = [entry["expected_article"]   for entry in tqdm(raw_data, desc="Loading positives", unit="entry")]

print(f"✓ Total training pairs: {len(anchors)}  (1 per dataset entry, no adjustments)")

# Shuffle
combined = list(zip(anchors, positives))
random.shuffle(combined)
anchors, positives = zip(*combined)


# 85 / 15 train-val split
split_idx = int(len(anchors) * 0.85)
train_anchors, val_anchors     = anchors[:split_idx],   anchors[split_idx:]
train_positives, val_positives = positives[:split_idx], positives[split_idx:]

train_dataset = Dataset.from_dict({
    "anchor":   list(train_anchors),
    "positive": list(train_positives),
})
val_dataset = Dataset.from_dict({
    "anchor":   list(val_anchors),
    "positive": list(val_positives),
})

print(f"✓ Train pairs : {len(train_dataset)}")
print(f"✓ Val pairs   : {len(val_dataset)}")


# ================================================================
# CELL 5 — Build validation evaluator
# ================================================================
# EmbeddingSimilarityEvaluator: rates (sentence1, sentence2, score)
# score=1.0 → same article (positive pair), score=0.0 → different articles

print("\nBuilding evaluator pairs...")
eval_s1, eval_s2, eval_scores = [], [], []

# Positive pairs (score = 1.0)
for a, p in tqdm(zip(val_anchors, val_positives), desc="Positive pairs",
                 total=len(val_anchors), unit="pair"):
    eval_s1.append(a)
    eval_s2.append(p)
    eval_scores.append(1.0)

# Negative pairs (score = 0.0) — random cross-article pairs
all_questions = [e["question"] for e in raw_data]
neg_count = len(val_anchors)
for _ in tqdm(range(neg_count), desc="Negative pairs", unit="pair"):
    q1, q2 = random.sample(all_questions, 2)
    eval_s1.append(q1)
    eval_s2.append(q2)
    eval_scores.append(0.0)

evaluator = evaluation.EmbeddingSimilarityEvaluator(
    sentences1=eval_s1,
    sentences2=eval_s2,
    scores=eval_scores,
    name="legal-val",
    show_progress_bar=True,                         # ← tqdm inside evaluator
)
print(f"✓ Evaluator ready: {len(eval_s1)} pairs ({neg_count} pos + {neg_count} neg)")


# ================================================================
# CELL 6 — Load BGE-M3 model
# ================================================================
MODEL_NAME = "BAAI/bge-m3"

print(f"\nLoading model: {MODEL_NAME} ...")
model = SentenceTransformer(MODEL_NAME, trust_remote_code=True)

# BGE-M3 default max_seq_length = 8192 — causes OOM on T4.
# Court case questions are short (~100-400 tokens), so 512 is sufficient
# and saves ~15x attention memory (attention is O(seq_len²)).
model.max_seq_length = 512
print(f"✓ Model loaded | max_seq_length set to: {model.max_seq_length}")


# ================================================================
# CELL 7 — Define loss function (MNRL)
# ================================================================
# MultipleNegativesRankingLoss (MNRL):
#   - Given N (anchor, positive) pairs in a batch
#   - Every OTHER positive in the same batch = automatic in-batch negative
#   - Larger batch size → more negatives → stronger gradient signal
#   - Scale parameter (default=20) controls logit sharpness

train_loss = losses.MultipleNegativesRankingLoss(model=model, scale=20.0)
print("✓ Loss: MultipleNegativesRankingLoss (MNRL), scale=20")


# ================================================================
# CELL 8 — Custom tqdm callback for per-step progress logging
# ================================================================
class TqdmProgressCallback(TrainerCallback):
    """Prints a clean tqdm-style summary after every epoch."""

    def on_epoch_begin(self, args, state, control, **kwargs):
        epoch = int(state.epoch) if state.epoch else 0
        total = int(args.num_train_epochs)
        print(f"\n{'='*60}")
        print(f"  Epoch {epoch + 1} / {total}")
        print(f"{'='*60}")

    def on_log(self, args, state, control, logs=None, **kwargs):
        if logs is None:
            return
        step   = state.global_step
        total  = state.max_steps
        loss   = logs.get("loss", None)
        lr     = logs.get("learning_rate", None)
        pct    = step / total * 100 if total else 0

        parts = [f"step {step}/{total} ({pct:.1f}%)"]
        if loss is not None:
            parts.append(f"loss={loss:.4f}")
        if lr is not None:
            parts.append(f"lr={lr:.2e}")
        print("  " + " | ".join(parts))

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        if metrics:
            print("\n  [Eval]")
            for k, v in metrics.items():
                if isinstance(v, float):
                    print(f"    {k}: {v:.4f}")

    def on_epoch_end(self, args, state, control, **kwargs):
        print(f"\n  Epoch done — global step: {state.global_step}")


# ================================================================
# CELL 9 — Training arguments
# ================================================================
# Notes for Kaggle GPU:
#   - T4 (16 GB) : batch_size=16, fp16=True
#   - P100 (16 GB): batch_size=16, fp16=True
#   - T4 x2      : effective batch = 32 (per_device * num_gpus)

OUTPUT_DIR = "/kaggle/working/bge-m3-legal-vn"

# Memory breakdown for BGE-M3 on T4 (14.56 GB):
#   Model weights  fp16  : ~1.1 GB
#   Gradients      fp16  : ~1.1 GB
#   Adam optimizer fp32  : ~4.6 GB
#   Activations batch=16 : ~7 GB  ← OOM here
#   ─────────────────────────────────
#   Total batch=16        : ~14 GB → OOM on T4
#   Total batch=8         : ~10 GB → fits comfortably
#
# Fix: batch=8 + gradient_accumulation=2 → effective batch=16 (unchanged)

args = SentenceTransformerTrainingArguments(
    output_dir=OUTPUT_DIR,
    num_train_epochs=3,
    per_device_train_batch_size=8,          # was 16 → OOM; 8 fits T4 (14.56 GB)
    gradient_accumulation_steps=2,          # effective batch = 8 × 2 = 16 (same as before)
    per_device_eval_batch_size=8,
    warmup_ratio=0.1,
    learning_rate=2e-5,
    fp16=True,                              # mixed precision
    bf16=False,
    gradient_checkpointing=True,            # trade compute for memory (~30% slower, saves ~3 GB)
    eval_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="eval_legal-val_spearman_cosine",
    greater_is_better=True,
    logging_strategy="steps",
    logging_steps=10,
    save_total_limit=2,
    dataloader_num_workers=2,
    report_to="none",
    disable_tqdm=False,
)


# ================================================================
# CELL 10 — Train
# ================================================================
trainer = SentenceTransformerTrainer(
    model=model,
    args=args,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
    loss=train_loss,
    evaluator=evaluator,
    callbacks=[TqdmProgressCallback()],     # ← custom progress logger
)

print("\nStarting training...")
trainer.train()
print("\n✓ Training complete!")


# ================================================================
# CELL 11 — Evaluate best model
# ================================================================
print("\nRunning final evaluation on best checkpoint...")
results = evaluator(model)
print("\n=== Final Evaluation Results ===")
for k, v in results.items():
    if isinstance(v, float):
        print(f"  {k}: {v:.4f}")


# ================================================================
# CELL 12 — Save final model
# ================================================================
FINAL_DIR = "/kaggle/working/bge-m3-legal-vn-final"
model.save_pretrained(FINAL_DIR)
print(f"\n✓ Model saved to: {FINAL_DIR}")
print("Files:", os.listdir(FINAL_DIR))


# ================================================================
# CELL 13 — Quick sanity check
# ================================================================
# Test that the fine-tuned model ranks similar legal cases higher

print("\nRunning sanity check...")
test_queries = [
    "Bị cáo dùng dao khống chế nạn nhân và cướp điện thoại trị giá 5 triệu đồng.",
    "Bị cáo lén lút trộm cắp xe máy trong đêm khuya.",
]
test_docs = [
    "Tội cướp tài sản theo Điều 168",       # should match query 0
    "Tội trộm cắp tài sản theo Điều 173",   # should match query 1
    "Tội giết người theo Điều 123",          # negative for both
]

embeddings_q = model.encode(
    test_queries, normalize_embeddings=True,
    show_progress_bar=True,                 # ← tqdm during encoding
)
embeddings_d = model.encode(
    test_docs, normalize_embeddings=True,
    show_progress_bar=True,
)
scores = embeddings_q @ embeddings_d.T

print("\n=== Sanity Check: Cosine Similarity ===")
for i, q in enumerate(test_queries):
    print(f"\nQuery: {q[:65]}...")
    for j, d in enumerate(test_docs):
        marker = "✓" if scores[i][j] == scores[i].max() else " "
        print(f"  {marker} [{scores[i][j]:.4f}] {d}")
