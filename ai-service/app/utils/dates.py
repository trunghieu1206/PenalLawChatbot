"""
utils/dates.py — VNPLaw AI Service
Date-parsing helpers and BLHS edition resolution.
"""
from datetime import datetime
from typing import Optional
from app.core.config import _EDITION_RANGES


def _edition_for_date(date_str: str) -> Optional[str]:
    """Return the BLHS edition name for a given crime date string.

    Accepts dd/mm/yyyy, dd-mm-yyyy, yyyy-mm-dd formats.
    Returns None for missing or unparseable input.
    """
    if not isinstance(date_str, str) or not date_str.strip():
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(date_str.strip(), fmt).date()
            for name, start, end in _EDITION_RANGES:
                if start <= d < end:
                    return name
        except (ValueError, AttributeError, TypeError):
            continue
    return None
