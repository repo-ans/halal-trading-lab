from app.services.ai_summary_guards import (
    MAX_GENERATED_PORTION_CHARS,
    validate_banned_phrases,
    validate_placeholders_and_no_numbers,
    validate_raw_generated_text,
)


# --- digits / number words / placeholders ---

def test_digit_is_rejected():
    assert validate_placeholders_and_no_numbers("This run had 5 trades.") is not None


def test_number_word_is_rejected():
    assert validate_placeholders_and_no_numbers("This run had four trades.") is not None


def test_number_word_percent_phrase_is_rejected():
    assert validate_placeholders_and_no_numbers("The win rate was twelve percent.") is not None


def test_unknown_placeholder_is_rejected():
    assert validate_placeholders_and_no_numbers("The run had {made_up_field} trades.") is not None


def test_ordinal_number_word_is_rejected():
    assert validate_placeholders_and_no_numbers("This was the third trade.") is not None


def test_malformed_brace_is_rejected():
    assert validate_placeholders_and_no_numbers("This had a stray { brace.") is not None


def test_valid_placeholder_text_passes():
    text = "This run produced {trades_count} trade(s) with a gain or loss of {realized_pnl}."
    assert validate_placeholders_and_no_numbers(text) is None


# --- banned phrases: word boundary, not substring ---

def test_learned_is_not_flagged():
    assert validate_banned_phrases("We learned a lot from this simulated run.") is None


def test_unpredictable_is_not_flagged():
    assert validate_banned_phrases("Markets are unpredictable by nature.") is None


def test_banned_words_are_flagged():
    for phrase in [
        "a healthy profit", "you earned a lot", "this is guaranteed", "you should buy",
        "you should sell", "we recommend this", "please advise", "this is likely",
        "we expect gains", "a forecast follows", "a great opportunity", "we predict a rise",
        "this is halal", "this is haram", "prices will rise", "prices will fall",
        "will go up tomorrow", "will go down tomorrow", "see http://example.com",
        "see www.example.com",
    ]:
        assert validate_banned_phrases(phrase) is not None, f"{phrase!r} should have been flagged"


# --- Unicode bypass attempts ---

def test_fullwidth_homoglyph_bypass_is_caught():
    fullwidth_profit = "ｐｒｏｆｉｔ"  # NFKC-normalizes to "profit"
    assert validate_banned_phrases(f"This had a {fullwidth_profit} in it.") is not None


def test_zero_width_insertion_bypass_is_caught():
    assert validate_banned_phrases("This had a pro​fit hidden in it.") is not None


# --- length cap ---

def test_text_over_the_length_cap_is_rejected():
    long_text = "trades " * (MAX_GENERATED_PORTION_CHARS // len("trades ") + 1)
    assert validate_raw_generated_text(long_text) is not None


def test_text_at_the_length_cap_is_not_rejected_for_length():
    short_text = "This run had {trades_count} trade(s)."
    reason = validate_raw_generated_text(short_text)
    assert reason is None
