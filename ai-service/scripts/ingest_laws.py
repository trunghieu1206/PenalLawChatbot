"""
Data ingestion script: reads law data from JSON/CSV exports and populates PostgreSQL.
Supports: Bộ luật Hình sự 2015 (sửa đổi 2017), BLTTHS 2015, Nghị quyết HĐTP, Án lệ.
"""
import os
import sys
import json
import csv
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("Install psycopg2: pip install psycopg2-binary")
    sys.exit(1)


def get_pg_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DB", "penallaw"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def create_schema(conn):
    """Create tables if they don't exist."""
    with conn.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS laws (
                id SERIAL PRIMARY KEY,
                article_number VARCHAR(20) NOT NULL,
                title VARCHAR(500),
                chapter VARCHAR(20),
                content TEXT NOT NULL,
                source VARCHAR(200) NOT NULL,
                effective_date DATE,
                effective_end_date DATE,
                is_active BOOLEAN DEFAULT TRUE,
                version INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(article_number, source, version)
            );

            -- Add columns if upgrading from old schema
            ALTER TABLE laws ADD COLUMN IF NOT EXISTS chapter VARCHAR(20);
            ALTER TABLE laws ADD COLUMN IF NOT EXISTS effective_end_date DATE;

            CREATE INDEX IF NOT EXISTS idx_laws_article ON laws(article_number);
            CREATE INDEX IF NOT EXISTS idx_laws_source ON laws(source);
            CREATE INDEX IF NOT EXISTS idx_laws_is_active ON laws(is_active);

            CREATE TABLE IF NOT EXISTS cases (
                id SERIAL PRIMARY KEY,
                case_number VARCHAR(100),
                title VARCHAR(500),
                content TEXT NOT NULL,
                verdict TEXT,
                source VARCHAR(200),
                judgment_date DATE,
                court VARCHAR(200),
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS evaluation_cases (
                id SERIAL PRIMARY KEY,
                case_description TEXT NOT NULL,
                ground_truth_laws JSONB,
                ground_truth_verdict TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS chat_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID,
                mode VARCHAR(20) DEFAULT 'neutral',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role VARCHAR(10) NOT NULL,
                content TEXT NOT NULL,
                extracted_facts JSONB,
                mapped_laws JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS training_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID,
                case_description TEXT NOT NULL,
                user_analysis TEXT NOT NULL,
                ai_reference TEXT,
                score INTEGER,
                feedback JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                full_name VARCHAR(200),
                role VARCHAR(20) DEFAULT 'user',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
    conn.commit()
    print("✅ Schema created/verified")


def ingest_from_json(conn, filepath: str, source: str = None, effective_date: str = None):
    """
    Ingest law articles from a JSON file.

    Supports two formats:
    1. Simple:  [{"article_number": "51", "title": "...", "content": "..."}]
    2. Rich (from parse_docx.py):
       [{"article_number": "51", "title": "...", "chapter": "...",
         "chapter_number": "IV", "content": "...",
         "source": "BLHS 2015", "effective_start": "2018-01-01", "effective_end": null}]

    For the rich format, source/effective_date CLI args are optional — they are
    read from each record. CLI args act as fallback.
    """
    with open(filepath, encoding="utf-8") as f:
        records = json.load(f)

    rows = []
    for r in records:
        # Source: record field takes priority, CLI arg is fallback
        rec_source = r.get("source") or source or "Unknown"

        # Effective start date
        rec_eff_start = r.get("effective_start") or effective_date
        eff_date = (
            datetime.strptime(rec_eff_start, "%Y-%m-%d").date()
            if rec_eff_start else None
        )

        # Effective end date (only in parse_docx output)
        rec_eff_end = r.get("effective_end")
        eff_end_date = (
            datetime.strptime(rec_eff_end, "%Y-%m-%d").date()
            if rec_eff_end else None
        )

        rows.append((
            r["article_number"],
            r.get("title", ""),
            r.get("chapter", ""),
            r["content"],
            rec_source,
            eff_date,
            eff_end_date,
            True,
            1,
        ))

    with conn.cursor() as c:
        execute_values(c, """
            INSERT INTO laws
              (article_number, title, chapter, content,
               source, effective_date, effective_end_date, is_active, version)
            VALUES %s
            ON CONFLICT (article_number, source, version) DO UPDATE
              SET content            = EXCLUDED.content,
                  title              = EXCLUDED.title,
                  chapter            = EXCLUDED.chapter,
                  effective_end_date = EXCLUDED.effective_end_date,
                  is_active          = TRUE
        """, rows)
    conn.commit()
    print(f"✅ Ingested {len(rows)} articles from {filepath}")


def ingest_from_csv(conn, filepath: str, source: str, effective_date: str):
    """
    Ingest law articles from a CSV file.
    Columns: article_number, title, content
    """
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records = list(reader)

    eff_date = datetime.strptime(effective_date, "%Y-%m-%d").date() if effective_date else None
    rows = [
        (r["article_number"], r.get("title", ""), r["content"], source, eff_date, True, 1)
        for r in records
    ]

    with conn.cursor() as c:
        execute_values(c, """
            INSERT INTO laws (article_number, title, content, source, effective_date, is_active, version)
            VALUES %s
            ON CONFLICT (article_number, source, version) DO UPDATE
            SET content = EXCLUDED.content, is_active = TRUE
        """, rows)
    conn.commit()
    print(f"✅ Ingested {len(rows)} articles from {filepath}")


def mark_old_versions_inactive(conn, article_number: str, source: str):
    """When updating a law, mark older versions inactive."""
    with conn.cursor() as c:
        c.execute("""
            UPDATE laws SET is_active = FALSE
            WHERE article_number = %s AND source = %s AND is_active = TRUE
        """, (article_number, source))
    conn.commit()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Ingest law data into PostgreSQL")
    parser.add_argument("--file", required=True, help="Path to JSON or CSV file")
    parser.add_argument("--source", default=None,
                        help="Source name override — not needed for parse_docx.py output")
    parser.add_argument("--date", default=None,
                        help="Effective start date override (YYYY-MM-DD) — not needed for parse_docx.py output")
    args = parser.parse_args()

    conn = get_pg_connection()
    create_schema(conn)

    if args.file.endswith(".json"):
        ingest_from_json(conn, args.file, args.source, args.date)
    elif args.file.endswith(".csv"):
        ingest_from_csv(conn, args.file, args.source or "Unknown", args.date or "2018-01-01")
    else:
        print("Unsupported file format. Use .json or .csv")

    conn.close()
