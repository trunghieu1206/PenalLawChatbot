"""
utils/clean_json.py \u2014 VNPLaw AI Service
Robust JSON extraction from LLM output.
"""
import re
import json
from typing import Any


def _extract_json(text: str) -> Any:
    """Extract the first valid JSON object or array from LLM output.

    Strategy:
    1. Strip markdown code fences (```json ... ```).
    2. Find the first \'{\' or \'[\' and the last matching \'}\' or \']\'.
    3. Parse the substring with json.loads().

    This is far more robust than the naive regex+strip approach because
    it handles conversational preamble, trailing commentary, and partial
    fences that LLMs commonly produce.
    """
    # Step 1: strip markdown fences
    cleaned = re.sub(r"```(?:json|JSON)?\s*", "", text).strip().rstrip("`").strip()

    # Step 2: find outermost JSON brackets
    obj_start = cleaned.find("{")
    arr_start = cleaned.find("[")

    if obj_start == -1 and arr_start == -1:
        raise ValueError(f"No JSON object or array found in: {cleaned[:200]}")

    # Pick whichever bracket comes first
    if arr_start == -1 or (obj_start != -1 and obj_start < arr_start):
        start = obj_start
        end = cleaned.rfind("}")
        if end == -1 or end <= start:
            raise ValueError(f"Unmatched \'{{' in: {cleaned[:200]}")
    else:
        start = arr_start
        end = cleaned.rfind("]")
        if end == -1 or end <= start:
            raise ValueError(f"Unmatched \'[\' in: {cleaned[:200]}")

    return json.loads(cleaned[start:end + 1])
