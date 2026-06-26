"""
utils/text.py \u2014 VNPLaw AI Service
Text sanitization and cleanup helpers.
"""
import re


def sanitize_text(text: str) -> str:
    """Strip lone surrogate characters that crash Python\'s UTF-8 JSON encoder.
    Surrogates (U+D800\u2013U+DFFF) appear in text scraped from Vietnamese PDFs via
    mixed-encoding parsers. encode(\'utf-8\', \'replace\') replaces them with U+FFFD (?).
    """
    if not isinstance(text, str):
        return text
    return text.encode("utf-8", "replace").decode("utf-8")


def _sanitize_msgs(messages: list) -> list:
    """Strip surrogates from ALL LangChain BaseMessage content immediately before
    any llm.invoke() call. Last line of defence \u2014 catches anything that slipped through
    upstream sanitization (history, mapped_context, case descriptions, etc.).
    NOTE: Returns NEW message objects \u2014 does NOT mutate the originals, preventing
    silent corruption of shared state like chat_history.
    """
    sanitized = []
    for m in messages:
        if isinstance(getattr(m, "content", None), str):
            clean = sanitize_text(m.content)
            if clean != m.content:
                # Only copy if content actually changed to avoid unnecessary overhead
                new_msg = m.__class__(content=clean)
                sanitized.append(new_msg)
                continue
        sanitized.append(m)
    return sanitized


def cleanup_response(text: str) -> str:
    """Remove or replace \'BLHS\' abbreviations in AI-generated text.
    Replaces \'BLHS\' with \'B\u1ed9 lu\u1eadt H\u00ecnh s\u1ef1\' for clarity.
    This ensures AI responses are user-friendly and don\'t use abbreviations.
    Also strips lone surrogate characters that would crash the JSON encoder.
    Also collapses excessively long markdown table separator dashes (e.g. |:----------|)
    that the LLM generates to match wide column content, which causes multi-MB JSONL lines.
    """
    text = sanitize_text(text)
    # Replace standalone "BLHS" abbreviation with full name, but PRESERVE
    # edition strings like "BLHS 1999", "BLHS 2015 (s\u1eeda \u0111\u1ed5i 2017)" which
    # the Java backend uses for exact-match lookup in suggested_laws.
    text = re.sub(
        r"\bBLHS\b(?!\s*(?:19|20)\d{2}|\s*\()",
        "B\u1ed9 lu\u1eadt H\u00ecnh s\u1ef1",
        text,
        flags=re.IGNORECASE,
    )
    # Collapse overly long markdown table separator dashes inside cell boundaries.
    text = re.sub(r"(\|[ \t]*:?)-{4,}([ \t]*\|)", r"\1---\2", text)
    return text
