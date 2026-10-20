# backend/metrics.py
from rapidfuzz.distance import Levenshtein as L

def _normalize_for_diff(text: str) -> str:
    """
    Normalización mínima:
    - …  → ...
    - — / –  → -
    - “ ”  → "
    - ‘ ’  → '
    Mantiene saltos de línea.
    """

    if not text:
        return ""

    t = text
    t = t.replace("…", "...")
    t = (
        t.replace("“", '"')
         .replace("”", '"')
    )
    t = (
        t.replace("‘", "'")
         .replace("’", "'")
    )
    t = (
        t.replace("—", "-")
         .replace("–", "-")
    )
    t = t.replace("\r\n", "\n").replace("\r", "\n")

    return t.strip()

def word_levenshtein_count(a_text: str, b_text: str) -> int:
    a_tokens = _normalize_for_diff(a_text).split()
    b_tokens = _normalize_for_diff(b_text).split()
    return int(L.distance(a_tokens, b_tokens))
