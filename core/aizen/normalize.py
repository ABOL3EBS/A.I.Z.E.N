"""Number-word normalizer shared by the answer verifier and TTS (§31).

The verifier must recognise spelled-out numbers ("two accounts", "one month")
and TTS must speak digits as words; both directions live here so the vocabulary
is defined exactly once. Only *cardinal* words are converted — ordinals
("first", "3rd") are detected separately and are exempt from verification.
"""

from __future__ import annotations

import re

_ONES: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_SCALES: dict[str, int] = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}

_NUMBER_WORDS: frozenset[str] = frozenset(_ONES) | frozenset(_TENS) | frozenset(_SCALES)

_CARDINAL_ALTERNATION = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_CARDINAL_RX = re.compile(
    rf"\b(?:{_CARDINAL_ALTERNATION})(?:[\s-]+(?:{_CARDINAL_ALTERNATION}|and))*\b",
    re.IGNORECASE,
)

_ORDINAL_ONES: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
}
_ORDINAL_TENS: dict[str, int] = {
    "twentieth": 20,
    "thirtieth": 30,
    "fortieth": 40,
    "fiftieth": 50,
    "sixtieth": 60,
    "seventieth": 70,
    "eightieth": 80,
    "ninetieth": 90,
}
ORDINAL_WORDS: frozenset[str] = frozenset(_ORDINAL_ONES) | frozenset(_ORDINAL_TENS)

_WORD_RX = re.compile(r"[a-z]+")
_ORDINAL_DIGIT_RX = re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE)


def words_to_number(text: str) -> int | None:
    """Parse one cardinal number phrase ("twenty-five", "one hundred and two")."""
    words = _WORD_RX.findall(text.lower())
    if not words:
        return None
    total = 0
    current = 0
    found = False
    for word in words:
        if word == "and":
            continue
        if word in _ONES:
            current += _ONES[word]
            found = True
        elif word in _TENS:
            current += _TENS[word]
            found = True
        elif word == "hundred":
            current = (current or 1) * _SCALES["hundred"]
            found = True
        elif word in _SCALES:
            total += (current or 1) * _SCALES[word]
            current = 0
            found = True
        else:
            return None
    return total + current if found else None


def normalize_number_words(text: str) -> str:
    """Replace runs of cardinal number words with their digits.

    Non-number words and ordinals are left untouched so that callers can still
    tell "3rd" / "first" apart from a real claim.
    """

    def replace(match: re.Match[str]) -> str:
        value = words_to_number(match.group(0))
        return str(value) if value is not None else match.group(0)

    return _CARDINAL_RX.sub(replace, text)


_DIGITS: tuple[str, ...] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS_BY_VALUE: dict[int, str] = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}


def _three_digit_words(value: int) -> list[str]:
    words: list[str] = []
    hundreds, rest = divmod(value, 100)
    if hundreds:
        words += [_DIGITS[hundreds], "hundred"]
        if rest:
            words.append("and")
    if rest:
        if rest < 20:
            words.append(_DIGITS[rest])
        else:
            tens, ones = divmod(rest, 10)
            name = _TENS_BY_VALUE[tens * 10]
            words.append(f"{name}-{_DIGITS[ones]}" if ones else name)
    return words


def number_to_words(value: int) -> str:
    """Speak an integer as English words (TTS direction)."""
    if value == 0:
        return "zero"
    if value < 0:
        return f"minus {number_to_words(-value)}"
    parts: list[str] = []
    for scale_name, scale in (
        ("trillion", 1_000_000_000_000),
        ("billion", 1_000_000_000),
        ("million", 1_000_000),
        ("thousand", 1_000),
    ):
        chunk, value = divmod(value, scale)
        if chunk:
            parts += [*_three_digit_words(chunk), scale_name]
    if value:
        parts += _three_digit_words(value)
    return " ".join(parts)


def is_ordinal(token: str) -> bool:
    """True for "first", "twenty-first", "3rd", "12th"."""
    bare = token.strip().lower()
    if bare in ORDINAL_WORDS:
        return True
    if _ORDINAL_DIGIT_RX.fullmatch(bare):
        return True
    if "-" in bare:
        return bare.split("-")[-1] in ORDINAL_WORDS
    return False


__all__ = [
    "ORDINAL_WORDS",
    "is_ordinal",
    "normalize_number_words",
    "number_to_words",
    "words_to_number",
]
