"""Citation checking and chunking. Used by the extraction engine AND re-checked by the ledger."""
from __future__ import annotations

import re

_QUOTES = str.maketrans({"‘": "'", "’": "'", "ʼ": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-", " ": " "})


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").translate(_QUOTES)).strip().lower()


def verify_quote(quote: str, source_text: str, min_len: int = 8) -> bool:
    """No citation, no claim: the quote must appear verbatim (modulo whitespace, case and typographic quotes)."""
    q = normalize(quote)
    return len(q) >= min_len and q in normalize(source_text)


def split_parts(text: str, size: int = 18000) -> list[str]:
    """Split on paragraph or line boundaries near `size` characters so no quote is cut in half."""
    if len(text) <= size:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut == -1:
                cut = text.rfind(". ", start + size // 2, end)
            end = cut + 1 if cut != -1 else end
        parts.append(text[start:end])
        start = end
    return parts


def first_json_block(raw: str):
    """Pull the first JSON object or array out of a model reply."""
    import json
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': in_str = False
                continue
            if ch == '"': in_str = True
            elif ch == opener: depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(raw[start:i + 1])
    raise ValueError("No JSON found in model output.")


def signal_hits(signals: list[str], text: str) -> int:
    """Count pack signals in text as whole words, allowing common endings (migrate -> migrated, migration)."""
    import re as _re
    low = text.lower()
    n = 0
    for sg in signals or []:
        n += len(_re.findall(r"\b" + _re.escape(sg.lower()) + r"(?:s|es|d|ed|ing|ion|ions|ation|ations)?\b", low))
    return n
