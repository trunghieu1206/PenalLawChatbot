from pymilvus import MilvusClient
from app.core.config import COLLECTION_NAME

# Assuming VN_law_lora.db is in the root directory or where it's stored.
# Let's check where the db actually is. Let's use the env var or default.
import os

db_path = "VN_law_lora.db"
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}. Try putting the correct path.")

try:
    client = MilvusClient(db_path)
    res = client.describe_index(collection_name=COLLECTION_NAME, index_name="vector")
    print("INDEX INFO:")
    print(res)
except Exception as e:
    print("Error:", e)
