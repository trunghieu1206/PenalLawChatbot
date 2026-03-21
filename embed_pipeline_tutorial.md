cd ai-service

# NOTE
create database if not exist: "penallaw"

# Step 0: install python-docx if needed
pip install python-docx

# Step 1: Parse all 4 DOCX → 4 JSON files
python scripts/parse_docx.py
# Creates: laws_documents_raw/VB_1999.json, VB_2009.json, VB_2017.json, VB_2025.json

# Step 2: Ingest each JSON into PostgreSQL (no --source/--date needed, they're embedded)
python scripts/ingest_laws.py --file laws_documents_raw/VB_1999.json
python scripts/ingest_laws.py --file laws_documents_raw/VB_2009.json
python scripts/ingest_laws.py --file laws_documents_raw/VB_2017.json
python scripts/ingest_laws.py --file laws_documents_raw/VB_2025.json

# Step 3: Embed all active laws into Milvus (for semantic search) (If run on local machine, however the script is currently wrong in logic)
python scripts/embed_laws.py

# (If embed using Google Colab) After uploading chunked documents json files to drive, use the embed_laws_colab.py script