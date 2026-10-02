"""Canonicalizer for WER. Rules: .claude/specs/verify.md, each covered by a test.

Applied only to COPIES of the text for comparison; Shot.line is never changed.
"""

from __future__ import annotations

import re
import unicodedata

from num2words import num2words

_QUOTES = str.maketrans({"‘": "'", "’": "'", "ʼ": "'", "`": "'", "“": '"', "”": '"', "„": '"'})
_DASHES = re.compile(r"[‐-―−-]")
_MONEY = re.compile(r"\$(\d[\d,]*(?:\.\d+)?)")
_PERCENT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*%")
_ORDINAL = re.compile(r"\b(\d+)(st|nd|rd|th)\b")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# An apostrophe stays only inside a word (don't, i'm); other punctuation goes.
_PUNCT = re.compile(r"(?!\b'\b)[^\w\s]")


def _words(num: str, **kw) -> str:
    value = num.replace(",", "")
    n = float(value) if "." in value else int(value)
    return num2words(n, lang="en", **kw)


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).translate(_QUOTES)  # rule 1
    s = s.lower()  # rule 2
    s = _MONEY.sub(lambda m: f"{_words(m.group(1))} dollars", s)  # rule 3: $5
    s = _PERCENT.sub(lambda m: f"{_words(m.group(1))} percent", s)  # 50%
    s = _ORDINAL.sub(lambda m: _words(m.group(1), to="ordinal"), s)  # 21st
    s = _NUMBER.sub(lambda m: _words(m.group(0)), s)  # 3, 1,000, 3.5
    s = s.replace("&", " and ")  # rule 4
    s = _DASHES.sub(" ", s)  # dashes and hyphens (incl. num2words: twenty-one) -> space
    s = s.replace(",", " ")  # num2words: "one thousand, two hundred"
    s = _PUNCT.sub(" ", s)  # rule 5
    return " ".join(s.split())  # rule 6
