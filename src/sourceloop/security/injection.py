"""Untrusted-text handling: wrapping and heuristic injection flags."""
import re
import unicodedata

_PATTERNS = [
    r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above)\s+(instructions|prompts?)",
    r"disregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above)",
    r"\bsystem\s+prompt\b",
    r"\byou\s+are\s+now\b",
    r"\bact\s+as\b.{0,40}\b(admin|system|developer)\b",
    r"<\s*/?\s*(system|assistant|untrusted_data)\s*>",
    r"\b(approve|qualify|clear)\b.{0,30}\b(this|the)\b.{0,20}\bsupplier\b",
    r"\"?(tool|function)_?call\"?\s*:",
]
_RX = [re.compile(p, re.I | re.S) for p in _PATTERNS]
_BREAKOUT = re.compile(r"<\s*/?\s*untrusted_data\s*>", re.I)
_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


def sanitize_text(s: str, max_len: int = 4000) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_ZW)
    s = "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return s[:max_len]


def injection_flags(text: str) -> list[str]:
    t = sanitize_text(text, 20000)
    return [p.pattern[:40] for p in _RX if p.search(t)]


def wrap_untrusted(text: str, max_len: int = 6000) -> str:
    """External text goes in the USER turn inside this block; never into a system prompt."""
    safe = sanitize_text(text, max_len)
    safe = _BREAKOUT.sub(lambda m: "[/untrusted_data]" if "/" in m.group(0) else "[untrusted_data]", safe)
    return f"<untrusted_data>\n{safe}\n</untrusted_data>"
