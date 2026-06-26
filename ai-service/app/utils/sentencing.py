"""
utils/sentencing.py — VNPLaw AI Service
Deterministic sentencing calculations (date parsing, age at crime, etc.).
"""
from datetime import datetime
from typing import Optional, Dict, Any


def parse_date(text: str) -> Optional[datetime]:
    """Try multiple date formats to parse a date string.
    Returns None for non-string, empty, or unparseable input."""
    if not isinstance(text, str) or not text.strip():
        return None
    for pattern in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text.strip(), pattern)
        except ValueError:
            continue
    return None


def compute_detention_months(arrest_date_str: str, trial_date_str: str) -> Optional[float]:
    """Calculate months from arrest to trial."""
    d1 = parse_date(arrest_date_str)
    d2 = parse_date(trial_date_str)
    if d1 and d2 and d2 > d1:
        delta = d2 - d1
        return round(delta.days / 30.44, 1)
    return None


def compute_age_at_crime(dob_str: str, crime_date_str: str) -> Optional[float]:
    """Calculate victim/defendant age at time of crime."""
    dob = parse_date(dob_str)
    crime_date = parse_date(crime_date_str)
    if dob and crime_date:
        age = (crime_date - dob).days / 365.25
        return round(age, 2)
    return None


def extract_sentencing_data(facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministically compute numeric sentencing data from extracted facts.
    Returns structured data to augment the LLM prompt.
    """
    result: Dict[str, Any] = {}

    # Detention period
    arrest_date = facts.get("ngay_tam_giam")
    trial_date = facts.get("ngay_xet_xu")
    if arrest_date and trial_date:
        months = compute_detention_months(arrest_date, trial_date)
        result["detention_months"] = months

    # Victim age at crime
    victim_dob = facts.get("ngay_sinh_nan_nhan")
    crime_date = facts.get("ngay_pham_toi")
    if victim_dob and crime_date:
        age = compute_age_at_crime(victim_dob, crime_date)
        result["victim_age_at_crime"] = age
        result["victim_is_minor"] = age is not None and age < 18

    # Defendant age at crime
    defendant_dob = facts.get("ngay_sinh_bi_cao")
    if defendant_dob and crime_date:
        age = compute_age_at_crime(defendant_dob, crime_date)
        result["defendant_age_at_crime"] = age
        result["defendant_is_minor"] = age is not None and age < 18

    return result
