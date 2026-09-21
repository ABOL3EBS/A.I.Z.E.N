"""Number-word normalizer: shared by the verifier and (later) TTS."""

from __future__ import annotations

import pytest
from aizen.normalize import (
    is_ordinal,
    normalize_number_words,
    number_to_words,
    words_to_number,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("five", 5),
        ("twenty-five", 25),
        ("one hundred and twenty three", 123),
        ("three thousand", 3000),
        ("two million five hundred thousand", 2_500_000),
        ("and", None),
        ("accounts", None),
    ],
)
def test_words_to_number(text: str, expected: int | None) -> None:
    assert words_to_number(text) == expected


def test_normalize_number_words_replaces_cardinals_only() -> None:
    assert normalize_number_words("You have two accounts") == "You have 2 accounts"
    assert normalize_number_words("twenty-five dollars") == "25 dollars"
    assert normalize_number_words("one hundred and one") == "101"
    # ordinals are left for the verifier to exempt explicitly
    assert normalize_number_words("the first one") == "the first 1"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "zero"),
        (7, "seven"),
        (21, "twenty-one"),
        (42, "forty-two"),
        (123, "one hundred and twenty-three"),
        (1_000_000, "one million"),
        (-5, "minus five"),
    ],
)
def test_number_to_words(value: int, expected: str) -> None:
    assert number_to_words(value) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [("first", True), ("twenty-first", True), ("3rd", True), ("12th", True), ("3", False)],
)
def test_is_ordinal(token: str, expected: bool) -> None:
    assert is_ordinal(token) is expected
