"""Canonicalizer rules from .claude/specs/verify.md — one test per rule."""

from __future__ import annotations

import pytest

from pipeline.text import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 1. NFKC + smart quotes/apostrophes
        ("I don’t know", "i don't know"),
        ("“Hello”", "hello"),
        ("ﬁne", "fine"),
        # 2. lower case
        ("MAYA Said", "maya said"),
        # 3. numbers -> words
        ("I have 3 days", "i have three days"),
        ("the 21st of May", "the twenty first of may"),
        ("1,000 letters", "one thousand letters"),
        ("3.5 hours", "three point five hours"),
        ("50% chance", "fifty percent chance"),
        ("It costs $5", "it costs five dollars"),
        ("21 years", "twenty one years"),
        # 4. & → and
        ("you & me", "you and me"),
        # 5. punctuation goes, an apostrophe inside a word stays
        ("Wait... what?!", "wait what"),
        ("I'm here, aren't I?", "i'm here aren't i"),
        ("'quoted'", "quoted"),
        ("well—no", "well no"),
        ("twenty-one", "twenty one"),
        # 6. spaces
        ("  so   many\tspaces \n", "so many spaces"),
    ],
)
def test_rules(raw, expected):
    assert normalize(raw) == expected


def test_contractions_are_not_expanded():
    # Spec: I'm vs I am is a real mismatch, not normalization noise.
    assert normalize("I'm") != normalize("I am")


def test_digit_and_word_forms_match():
    assert normalize("It says I have 3 days to stop myself.") == normalize(
        "It says I have three days to stop myself"
    )


@pytest.mark.parametrize(
    "raw",
    ["It says I have 3 days.", "I don’t—know & $5, 21st, 50%!", "  Twenty-One  ", "o'clock 1,000"],
)
def test_idempotent(raw):
    once = normalize(raw)
    assert normalize(once) == once
