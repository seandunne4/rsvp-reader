"""Text helpers for extraction: wordlist, garbage scoring, hyphen repair."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# Includes accented Latin letters so mis-encoded runs like "ÃÄÅÆ" count as (non-)words
# instead of slipping through as "normal letters".
ALPHA_TOKEN = re.compile(r"[A-Za-zÀ-ɏ]{3,}")
# Characters we expect in normal English prose (beyond letters, digits, whitespace).
NORMAL_PUNCT = set(".,;:!?'\"()[]-–—‘’“”…/%$&@#*+=°€£¥§©®™•·")


@lru_cache(maxsize=1)
def wordset() -> frozenset[str]:
    """~250k common English words (lowercase), from the wordfreq package (bundled data)."""
    from wordfreq import top_n_list

    return frozenset(top_n_list("en", 250_000))


def is_word(token: str) -> bool:
    return token.lower() in wordset()


def char_normality(text: str) -> float:
    """Share of non-space characters that look like ordinary prose characters."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 1.0
    ok = 0
    for c in chars:
        if c.isalnum() and ord(c) < 0x250:  # Latin letters/digits incl. accents
            ok += 1
        elif c in NORMAL_PUNCT:
            ok += 1
        elif unicodedata.category(c) in ("Co", "Cc", "Cn", "Cs") or c == "�":
            pass  # private use / control / unassigned / replacement: strong garbage signal
    return ok / len(chars)


_SYMBOL_RUN = re.compile(r"[^\w\s]{3,}")
_BENIGN_RUN = re.compile(r"^[.…'\"’”“‘()\[\]—–-]+$")  # "...", '."', ")—" etc. occur in prose


def symbol_soup(text: str) -> float:
    """Share of non-space chars sitting in runs of 3+ mixed symbols (";<;=>", "!\"#$$")."""
    chars = sum(1 for c in text if not c.isspace())
    if not chars:
        return 0.0
    bad = sum(len(m) for m in _SYMBOL_RUN.findall(text) if not _BENIGN_RUN.match(m))
    return bad / chars


def word_validity(text: str) -> tuple[float, int]:
    """(share of 3+ letter tokens that are dictionary words, number of such tokens)."""
    tokens = ALPHA_TOKEN.findall(text)
    if not tokens:
        return 1.0, 0
    return sum(1 for t in tokens if is_word(t)) / len(tokens), len(tokens)


def quality(text: str) -> float:
    """0..1 score; below ~0.6 means the text layer is garbage.

    Combines character normality with dictionary-word validity. Short snippets with few
    tokens lean on character normality only, so a two-word caption isn't misjudged.
    """
    cn = max(0.0, char_normality(text) - 2 * symbol_soup(text))
    wv, n = word_validity(text)
    if n < 3:
        return cn
    weight = min(1.0, n / 12)  # trust word validity more as token count grows
    return min(cn, (1 - weight) * cn + weight * wv)


GARBAGE_THRESHOLD = 0.6


def is_garbage(text: str) -> bool:
    return quality(text) < GARBAGE_THRESHOLD


def is_trustworthy(text: str) -> bool:
    """Stricter test used on pages already known to have a bad text layer: native text is
    kept over OCR only if it's clearly real prose (enough dictionary words, high quality)."""
    wv, n = word_validity(text)
    return n >= 3 and wv >= 0.85 and char_normality(text) >= 0.95


# --- hyphen repair --------------------------------------------------------------------

_TRAIL_WORD = re.compile(r"([A-Za-z]+)-$")
_LEAD_WORD = re.compile(r"^([a-z]+)")


def join_lines(lines: list[str]) -> str:
    """Join the lines of one block into a paragraph, repairing line-break hyphenation.

    A line ending in "word-" followed by a line starting with a lowercase "rest" becomes
    "wordrest" only when that is a dictionary word and "word-rest" isn't a real compound
    (e.g. "exacer-/bated" joins; "market-/ready" and "pandemic-/related" keep the hyphen).
    Soft hyphens (U+00AD) always join.
    """
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
            continue
        if out.endswith("­"):
            out = out[:-1] + line
            continue
        m, n = _TRAIL_WORD.search(out), _LEAD_WORD.match(line)
        if m and n:
            joined = m.group(1) + n.group(1)
            if is_word(joined) and not (is_word(m.group(1)) and is_word(n.group(1)) and len(n.group(1)) > 3):
                out = out[:-1] + line
            else:
                out = out + line  # keep the hyphen, no space: "market-ready"
            continue
        if re.search(r"[A-Za-z0-9]-$", out) or out.endswith("—") or line.startswith("—"):
            out = out + line  # "Anheuser-/Busch", "thing—/next"
            continue
        out = out + " " + line
    return re.sub(r"[ \t]{2,}", " ", out.replace("­", ""))
