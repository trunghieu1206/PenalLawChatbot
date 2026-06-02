#!/usr/bin/env python3
"""
clean_jsonl_dashes.py — Post-process evaluation JSONL files to collapse
excessively long markdown table separator dashes produced by the LLM.

The LLM sometimes generates separator rows like:
  | :------...thousands of dashes...------ |
to "match" the width of wide content columns. This creates multi-MB single
lines in the JSONL, which freezes terminals and inflates file sizes.

This script collapses those to: | :--- |

Usage (run on server):
  python3 clean_jsonl_dashes.py combined_results_role_progress.jsonl
  python3 clean_jsonl_dashes.py combined_results.jsonl
  python3 clean_jsonl_dashes.py combined_results_role_progress.jsonl combined_results.jsonl
"""
import sys
import re
import json
from pathlib import Path


_DASH_PAT = re.compile(r"(\|\s*:?)-{4,}(\s*\|)")


def collapse_dashes(text: str) -> str:
    """Collapse | :---...--- | → | :--- | inside markdown table separator rows."""
    return _DASH_PAT.sub(r"\1---\2", text)


def clean_value(val):
    """Recursively collapse dashes in all string values of a dict/list."""
    if isinstance(val, str):
        return collapse_dashes(val)
    if isinstance(val, dict):
        return {k: clean_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [clean_value(i) for i in val]
    return val


def clean_file(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    print(f"[{path.name}] {len(lines)} lines — scanning...")

    cleaned_lines = []
    n_changed = 0
    total_saved = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            # Not valid JSON — clean the raw string directly and keep it
            new_line = collapse_dashes(line)
            if new_line != line:
                n_changed += 1
                total_saved += len(line) - len(new_line)
            cleaned_lines.append(new_line)
            continue

        new_obj = clean_value(obj)
        new_line = json.dumps(new_obj, ensure_ascii=False) + "\n"
        if new_line != line:
            n_changed += 1
            total_saved += len(line) - len(new_line)
        cleaned_lines.append(new_line)

        if i % 20 == 0:
            print(f"  processed {i}/{len(lines)} lines...")

    # Write back in-place (atomic via temp file)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(cleaned_lines), encoding="utf-8")
    tmp.replace(path)

    saved_kb = total_saved / 1024
    print(f"  ✅ Done — {n_changed} lines cleaned, {saved_kb:.1f} KB saved → {path}")


if __name__ == "__main__":
    targets = sys.argv[1:] or [
        "combined_results_role_progress.jsonl",
        "combined_results.jsonl",
    ]
    results_dir = Path(__file__).resolve().parent / "results"
    for t in targets:
        p = Path(t) if Path(t).is_absolute() else results_dir / t
        if not p.exists():
            print(f"  ⚠️  File not found: {p}")
            continue
        clean_file(p)
