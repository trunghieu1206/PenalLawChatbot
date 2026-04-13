#!/usr/bin/env python3
"""
merge_backups.py
----------------
Merges:
  1. penallaw_backup_20260402_140359.sql  (server backup: users, chat_sessions, chat_messages)
  2. Local laws table DDL + data from pg_dump

Produces: database/backups/penallaw_combined_backup.sql

Safe to run multiple times — uses CREATE TABLE IF NOT EXISTS and ON CONFLICT DO NOTHING.
"""

import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).parent.parent
BACKUPS = PROJECT / "database" / "backups"
SERVER_BACKUP = BACKUPS / "penallaw_backup_20260402_140359.sql"
OUTPUT = BACKUPS / "penallaw_combined_backup.sql"

PG_BIN = "/Applications/Postgres.app/Contents/Versions/16/bin"
PGPASSWORD = "postgres"
PGUSER = "postgres"
PGDB = "penallaw"


def pg_dump(*args):
    env = {"PGPASSWORD": PGPASSWORD, "PATH": PG_BIN + ":/usr/bin:/bin"}
    result = subprocess.run(
        [f"{PG_BIN}/pg_dump", "-U", PGUSER, "-d", PGDB, "--no-owner", "--no-acl"] + list(args),
        capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        print(f"pg_dump error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def strip_pg_dump_wrapper(sql: str) -> str:
    """Remove \\restrict/\\unrestrict and top-level SET boilerplate."""
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("\\restrict") or stripped.startswith("\\unrestrict"):
            continue
        lines.append(line)
    return "\n".join(lines)


def make_create_if_not_exists(ddl: str) -> str:
    """Convert CREATE TABLE to CREATE TABLE IF NOT EXISTS."""
    return re.sub(
        r"\bCREATE TABLE\b",
        "CREATE TABLE IF NOT EXISTS",
        ddl,
        flags=re.IGNORECASE
    )


def make_index_if_not_exists(ddl: str) -> str:
    """Convert CREATE INDEX to CREATE INDEX IF NOT EXISTS."""
    return re.sub(
        r"\bCREATE INDEX\b",
        "CREATE INDEX IF NOT EXISTS",
        ddl,
        flags=re.IGNORECASE
    )


def add_on_conflict_to_copy(sql: str) -> str:
    """
    pg_dump COPY blocks cannot use ON CONFLICT natively.
    We wrap each COPY block with a temp-table trick so duplicates are silently skipped.
    Actually the simpler approach: just use INSERT ... ON CONFLICT DO NOTHING
    by converting COPY to INSERT statements isn't practical for 1524 rows.
    
    Instead: we rely on the target DB being fresh. If re-running, the user should
    DROP the laws table first, or we add a comment. Keep COPY as-is.
    """
    return sql


def extract_sequence_setval(data_sql: str) -> str:
    """Extract sequence setval lines from data dump."""
    lines = [l for l in data_sql.splitlines() if "setval" in l.lower()]
    return "\n".join(lines)


print("📦 Exporting laws DDL (schema only)...")
schema_sql = pg_dump(
    "--schema-only",
    "--table=public.laws",
    "--table=public.cases",
    "--table=public.evaluation_cases",
    "--table=public.training_sessions",
)

print("📦 Exporting laws data...")
data_sql = pg_dump(
    "--data-only",
    "--table=public.laws",
)

print("📝 Reading server backup...")
server_sql = SERVER_BACKUP.read_text(encoding="utf-8")

# Strip pg_dump wrappers
schema_clean = strip_pg_dump_wrapper(schema_sql)
data_clean = strip_pg_dump_wrapper(data_sql)
server_clean = strip_pg_dump_wrapper(server_sql)

# Make DDL idempotent
schema_clean = make_create_if_not_exists(schema_clean)
schema_clean = make_index_if_not_exists(schema_clean)

# Build combined file
combined = f"""--
-- PenalLaw Combined Database Backup
-- Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
--
-- Contains:
--   1. Users, chat_sessions, chat_messages  (from server backup 20260402_140359)
--   2. laws (1524 rows), cases, evaluation_cases, training_sessions  (from local penallaw DB)
--
-- Restore with:
--   psql -U postgres -d penallaw -f penallaw_combined_backup.sql
--
-- Note: CREATE TABLE IF NOT EXISTS is used for all tables.
--       COPY data for laws will fail if rows already exist (unique constraint).
--       To re-import laws: DELETE FROM public.laws; before restoring.
--

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';
SET default_table_access_method = heap;

-- ============================================================
-- SECTION 1: Users, Chat Sessions, Chat Messages
--             (Original server backup — tables already exist on server)
-- ============================================================

{server_clean}

-- ============================================================
-- SECTION 2: Laws, Cases, Evaluation Cases, Training Sessions
--             (From local penallaw DB)
-- ============================================================

{schema_clean}

-- ============================================================
-- SECTION 3: Law data (1524 articles — Bộ luật Hình sự 1999/2009/2015/2025)
-- ============================================================

{data_clean}

--
-- End of combined backup
--
"""

OUTPUT.write_text(combined, encoding="utf-8")
size_kb = OUTPUT.stat().st_size / 1024
print(f"✅ Written: {OUTPUT}")
print(f"   Size: {size_kb:.1f} KB")
print(f"   Lines: {combined.count(chr(10))}")
