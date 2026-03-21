# ============================================================
# embed_laws_colab.py
# Run this on Google Colab (GPU runtime recommended).
#
# Workflow:
#   1. Upload your 4 JSON files to Google Drive
#   2. Mount Google Drive in Colab
#   3. Run this script cell by cell
#   4. Download the output VN_law_lora.db file
# ============================================================

# ── Cell 1: Install dependencies ────────────────────────────
# !pip install -q pymilvus==2.4.4 milvus-lite==2.4.8 "marshmallow<4.0.0" sentence-transformers huggingface_hub peft

# ── Cell 2: Mount Google Drive ───────────────────────────────
# from google.colab import drive
# drive.mount('/content/drive')

# ── Cell 3: Core embedding script ───────────────────────────
# ============================================================
# embed_laws_colab.py (CORRECTED)
# ============================================================
import json
import os
import shutil
from pathlib import Path

# ─── CONFIG ─────────────────────────────────────────────────
JSON_DIR = Path("/content/drive/MyDrive/datasets_lawchatbot/")

# ⚠️ FIX: Build locally first to prevent Google Drive deadlocks, then copy later
LOCAL_DB_PATH = "/content/VN_law_lora.db"
DRIVE_DB_PATH = "/content/drive/MyDrive/datasets_lawchatbot/VN_law_lora.db"

JSON_FILES = [
    "VB_1999.json",
    "VB_2009.json",
    "VB_2017.json",
    "VB_2025.json",
]

COLLECTION_NAME = "legal_rag_lora"
EMBEDDING_MODEL  = "BAAI/bge-m3"      
LORA_ADAPTER     = "trunghieu1206/lawchatbot-40k"   
BATCH_SIZE       = 4
VECTOR_DIM       = 1024               

# ── Load all articles from JSON files ──────────────────────
def load_articles() -> list[dict]:
    articles = []
    for fname in JSON_FILES:
        fpath = JSON_DIR / fname
        if not fpath.exists():
            print(f"⚠️  Not found: {fpath}")
            continue
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        articles.extend(data)
        print(f"  ✅ Loaded {len(data):>4} articles from {fname}")
    print(f"\n📚 Total articles: {len(articles)}")
    return articles

# ── Build text to embed ─────────────────────────────────────
def build_text(article: dict) -> str:
    parts = []
    if article.get("chapter"):
        parts.append(f"Chương {article['chapter']}")
    parts.append(f"Điều {article['article_number']}")
    if article.get("title"):
        parts.append(article["title"])
    parts.append(article.get("content", ""))
    return " | ".join(parts)

# ── Load embedding model ────────────────────────────────────
def load_model():
    from sentence_transformers import SentenceTransformer
    from peft import PeftModel
    from huggingface_hub import login
    from google.colab import userdata

    # ⚠️ FIX: Authenticate with Hugging Face first
    try:
        hf_token = userdata.get('HF_TOKEN')
        login(token=hf_token)
        print("✅ Logged in to Hugging Face.")
    except:
        print("⚠️ HF_TOKEN not found in Colab secrets. Model downloads may fail if private.")

    print(f"\n🤖 Loading base model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL, trust_remote_code=True)

    try:
        # ⚠️ FIX: Correctly access the inner Transformer model using model[0]
        base = model[0].auto_model
        peft_model = PeftModel.from_pretrained(base, LORA_ADAPTER)
        
        # Excellent optimization! Merging makes inference much faster.
        peft_model = peft_model.merge_and_unload()
        model[0].auto_model = peft_model
        
        print(f"  ✅ LoRA adapter applied & merged: {LORA_ADAPTER}")
    except Exception as e:
        print(f"  ⚠️  LoRA adapter skipped ({e}). Using base model.")

    return model

# ── Setup Milvus Lite collection ────────────────────────────
def setup_milvus():
    from pymilvus import MilvusClient
    import glob # ⚠️ Add this import inside the function or at the top of your script

    # ⚠️ FIX: Aggressively delete all possible SQLite lock/journal files
    print("🗑️ Checking for old database files...")
    for old_file in glob.glob(f"{LOCAL_DB_PATH}*"):
        try:
            os.remove(old_file)
            print(f"   - Deleted: {old_file}")
        except Exception as e:
            print(f"   - Could not delete {old_file}: {e}")

    print(f"\n📦 Opening Milvus Lite DB: {LOCAL_DB_PATH}")
    client = MilvusClient(uri=LOCAL_DB_PATH)

    if client.has_collection(COLLECTION_NAME):
        client.drop_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        dimension=VECTOR_DIM,
        metric_type="COSINE",
    )
    print(f"  ✅ Collection '{COLLECTION_NAME}' created")
    return client

# ── Main: embed & insert ────────────────────────────────────
def main():
    articles = load_articles()
    if not articles:
        print("No articles loaded. Check your JSON_DIR path.")
        return

    model  = load_model()
    client = setup_milvus()

    texts = [build_text(a) for a in articles]
    total = len(texts)
    inserted = 0

    print(f"\n⚡ Embedding {total} articles in batches of {BATCH_SIZE}...")
    for i in range(0, total, BATCH_SIZE):
        batch_texts    = texts[i : i + BATCH_SIZE]
        batch_articles = articles[i : i + BATCH_SIZE]

        embeddings = model.encode(
            batch_texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        rows = []
        for j, (art, vec) in enumerate(zip(batch_articles, embeddings)):
            rows.append({
                "id":             i + j,
                "vector":         vec.tolist(),
                "article_number": art.get("article_number", ""),
                "title":          art.get("title", ""),
                "chapter":        art.get("chapter", ""),
                "source":         art.get("source", ""),
                "effective_start":art.get("effective_start", ""),
                "effective_end":  art.get("effective_end", ""),
                "content":        art.get("content", "")[:60000],  # Increased safely to 60k chars
            })

        client.insert(collection_name=COLLECTION_NAME, data=rows)
        inserted += len(rows)

        if (i // BATCH_SIZE) % 5 == 0:
            print(f"  [{inserted:>4}/{total}] embedded & inserted")

    print(f"\n🎉 Done! {inserted} vectors stored locally.")
    
    # ⚠️ FIX: Safely push the completed file to Google Drive
    print(f"⏳ Backing up database to Google Drive at: {DRIVE_DB_PATH}")
    os.makedirs(os.path.dirname(DRIVE_DB_PATH), exist_ok=True)
    shutil.copy2(LOCAL_DB_PATH, DRIVE_DB_PATH)
    print("✅ Backup complete, .db file uploaded to gg drive")

if __name__ == "__main__":
    main()