"""Output guards -- code, not prompt. Run on BOTH modes' raw generated text
before any placeholder substitution happens (the template is trusted by
construction, but is checked anyway: defense in depth, and it's what
tests/test_ai_summary_template.py's pinned-hash test is really protecting).

Two independent guards, both must pass:
  1. validate_placeholders_and_no_numbers -- the model/template may not write
     a single digit or number word anywhere; it writes placeholders like
     {trades_count} instead, and only placeholders from
     app.services.ai_summary_input.ALLOWED_PLACEHOLDERS.
  2. validate_banned_phrases -- word-boundary matching (never substring --
     "learned"/"unpredictable" must never trip this), on text normalized with
     Unicode NFKC and with zero-width characters stripped first, so a
     fullwidth-character or zero-width-insertion bypass attempt normalizes
     back to the plain word before matching. This does NOT defend against
     cross-script homoglyphs (e.g. Cyrillic "а" vs Latin "a") -- NFKC does not
     unify those, and that is a different, unaddressed threat class.
"""
import re
import unicodedata

from app.services.ai_summary_input import ALLOWED_PLACEHOLDERS

MAX_GENERATED_PORTION_CHARS = 1500  # the model/template portion only -- checked
                                    # BEFORE the fixed disclaimer/limitation suffix
                                    # is appended, so that fixed text can never be
                                    # pushed out by a cap hit

_ZERO_WIDTH_CHARS = "​‌‍﻿⁠"  # ZW space, ZWNJ, ZWJ, BOM, word joiner

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety", "hundred", "thousand", "million",
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
    "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
    "eighteenth", "nineteenth", "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth",
    "seventieth", "eightieth", "ninetieth", "hundredth", "thousandth", "millionth",
}

# Single words: word-boundary, common inflections included. Phrases: matched as
# a literal word-boundary-delimited sequence. URLs: any http(s):// or www.
_BANNED_WORD_PATTERNS = [
    r"\bprofit(s)?\b", r"\bearn(ed|s|ing)?\b", r"\bguarantee(d|s)?\b",
    r"\bshould\b", r"\brecommend(ed|s|ation|ations)?\b", r"\badvis(e|es|ed|ing)\b",
    r"\blikely\b", r"\bexpect(ed|s|ing|ation)?\b", r"\bforecast(s|ed|ing)?\b",
    r"\bopportunit(y|ies)\b", r"\bpredict(s|ed|ion|ions|ing)?\b",
    r"\bhalal\b", r"\bharam\b",
    r"\byou should buy\b", r"\byou should sell\b",
    r"\bwill rise\b", r"\bwill fall\b", r"\bwill go up\b", r"\bwill go down\b",
    r"https?://\S+", r"www\.\S+",
]
_BANNED_PATTERN = re.compile("|".join(_BANNED_WORD_PATTERNS), re.IGNORECASE)


def normalize_for_guard(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(c for c in normalized if c not in _ZERO_WIDTH_CHARS)


def validate_placeholders_and_no_numbers(text: str) -> str | None:
    """Returns a rejection reason, or None if the raw (pre-substitution) text
    is valid: every {token} is a known placeholder, every brace is part of a
    well-formed {token}, and once placeholders are stripped the remaining
    prose contains no digit and no number word."""
    used = set(_PLACEHOLDER_RE.findall(text))
    unknown = used - ALLOWED_PLACEHOLDERS
    if unknown:
        return f"unknown placeholder(s): {sorted(unknown)}"

    without_placeholders = _PLACEHOLDER_RE.sub("", text)
    if "{" in without_placeholders or "}" in without_placeholders:
        return "malformed placeholder braces"

    if any(c.isdigit() for c in without_placeholders):
        return "raw output contains a digit"

    words = re.findall(r"[A-Za-z']+", without_placeholders.lower())
    number_words_found = [w for w in words if w in _NUMBER_WORDS]
    if number_words_found:
        return f"raw output contains a number word: {number_words_found[0]!r}"

    return None


def validate_banned_phrases(text: str) -> str | None:
    """Returns a rejection reason, or None if clean. Normalizes first (NFKC +
    zero-width stripped) so a bypass attempt via a fullwidth character or an
    inserted zero-width character still gets caught."""
    normalized = normalize_for_guard(text)
    match = _BANNED_PATTERN.search(normalized)
    if match:
        return f"banned phrase: {match.group(0)!r}"
    return None


def validate_raw_generated_text(text: str) -> str | None:
    """Runs every guard. Returns the first rejection reason, or None if the
    text passes everything and is safe to substitute and use."""
    if len(text) > MAX_GENERATED_PORTION_CHARS:
        return f"generated text exceeds {MAX_GENERATED_PORTION_CHARS} characters"
    reason = validate_placeholders_and_no_numbers(text)
    if reason is not None:
        return reason
    return validate_banned_phrases(text)
