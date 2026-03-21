"""
Batch embedding script: reads laws from PostgreSQL, embeds with LoRA BGE-M3,
and stores in Milvus law_embeddings collection.
"""
import os
import sys
import json
import torch
from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
    from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility
    from sentence_transformers import SentenceTransformer
except ImportError as e:
    print(f"Missing dependency: {e}")
    sys.exit(1)

# --- CONFIG ---
MILVUS_URI = os.getenv("MILVUS_URI", "./VN_law_lora.db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "legal_rag_lora")
EMBED_DIM = 1024  # BGE-M3 dimension
BATCH_SIZE = 32
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def get_pg_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DB", "penallaw"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def load_embedding_model() -> SentenceTransformer:
    from huggingface_hub import login
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        login(token=hf_token)

    print(f"Loading embedding model on {DEVICE}...")
    model = SentenceTransformer("BAAI/bge-m3", trust_remote_code=True, device=DEVICE)
    adapter = os.getenv("EMBEDDING_ADAPTER", "trunghieu1206/lawchatbot-40k")
    try:
        model.load_adapter(adapter)
        print("✅ LoRA adapter loaded")
    except Exception as e:
        print(f"⚠️  Adapter not loaded: {e}")
    return model


def setup_milvus_collection():
    """Create Milvus collection if not exists."""
    connections.connect(db_path=MILVUS_URI)
    if utility.has_collection(COLLECTION_NAME):
        print(f"Collection '{COLLECTION_NAME}' already exists.")
        return Collection(COLLECTION_NAME)

    schema = CollectionSchema(fields=[
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="law_id", dtype=DataType.INT64),
        FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=500),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBED_DIM),
    ], description="Vietnamese legal documents")

    collection = Collection(name=COLLECTION_NAME, schema=schema)
    collection.create_index("embedding", {
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 200}
    })
    print(f"✅ Created Milvus collection '{COLLECTION_NAME}'")
    return collection


def embed_and_insert(model: SentenceTransformer, collection: Collection):
    pg = get_pg_connection()
    cursor = pg.cursor()
    cursor.execute("""
        SELECT id, article_number, title, content, source
        FROM laws
        WHERE is_active = TRUE
        ORDER BY id
    """)
    rows = cursor.fetchall()
    cursor.close()
    pg.close()

    print(f"Found {len(rows)} active law articles.")

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        law_ids = [r[0] for r in batch]
        texts = [f"Điều {r[1]} - {r[2]}\n{r[3]}" for r in batch]
        sources = [r[4] or "Unknown" for r in batch]

        embeddings = model.encode(texts, normalize_embeddings=True, batch_size=BATCH_SIZE).tolist()

        collection.insert([law_ids, sources, texts, embeddings])
        print(f"  Inserted batch {i // BATCH_SIZE + 1} ({len(batch)} items)")

    collection.flush()
    print(f"✅ Embedding complete. Total: {len(rows)} articles.")


if __name__ == "__main__":
    model = load_embedding_model()
    collection = setup_milvus_collection()
    embed_and_insert(model, collection)
