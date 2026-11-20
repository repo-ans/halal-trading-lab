"""The default and fallback mode: no LLM, no key, no network call. A single
fixed string, written using ONLY placeholders from
app.services.ai_summary_input.ALLOWED_PLACEHOLDERS -- it goes through the exact
same guard pipeline as an LLM response (app/services/ai_summary_guards.py)
before substitution, so it is held to the same standard, not just trusted.

PROMPT_VERSION: bump this whenever RAW_TEMPLATE_TEXT's wording changes.
tests/test_ai_summary_template.py pins a sha256 hash of this template's output
against a fixed fixture -- changing the wording breaks that test on purpose,
forcing a deliberate PROMPT_VERSION bump and a deliberate hash update, not a
silent drift.
"""
PROMPT_VERSION = "ai-summary-v1"

RAW_TEMPLATE_TEXT = (
    "This simulated run used a moving-average crossover strategy with a fast "
    "period of {fast_period} bars and a slow period of {slow_period} bars, "
    "with moving-average reset-per-session set to {reset_ma_each_session}, over "
    "the range {range_start} to {range_end}, starting with {starting_cash} in "
    "{currency} and a maximum position size of {max_position_value}. It "
    "produced {trades_count} closed round trip(s), with a realized gain or "
    "loss of {realized_pnl} and a total return of {total_return}. The win rate "
    "was {win_rate}, with an average winning trade of {avg_win} and an average "
    "non-winning trade of {avg_non_win}. Total fees paid, combining "
    "commission, spread, and slippage, were {total_fees}, and the realized "
    "equity curve's maximum drawdown was {max_drawdown}. {unresolved_positions} "
    "position(s) could not be closed due to missing data and are excluded from "
    "every figure above. Order attempts that were not filled broke down as: "
    "{rejection_price_exceeds_max_position_count} over the position cap, "
    "{rejection_insufficient_cash_count} for insufficient cash, "
    "{rejection_position_already_open_count} with a position already open, "
    "{rejection_no_open_position_to_sell_count} with nothing open to sell, "
    "{rejection_execution_bar_missing_count} with no price data, "
    "{rejection_execution_bar_missing_eod_count} that failed to close at "
    "session end, and {rejection_instrument_frozen_unresolved_eod_count} "
    "skipped after an instrument was frozen."
)


def render_raw_template() -> str:
    """Returns the raw (pre-substitution) template text. A function, not just
    the constant directly, so callers don't need to know RAW_TEMPLATE_TEXT's
    name -- mirrors the shape of ai_summary_llm.py's call_llm so the
    orchestration layer (app/services/ai_summary.py) treats both modes
    identically up to this point."""
    return RAW_TEMPLATE_TEXT
