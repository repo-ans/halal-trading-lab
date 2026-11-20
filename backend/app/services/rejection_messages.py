"""Plain-language text for every app.models.paper.REJECTION_REASONS code -- one
source, shared by the Virtual Trades screen and the CSV export, so the UI and
the exported file can never say something different about the same code."""
from decimal import Decimal

from app.models.paper import REJECTION_REASONS

_STATIC_MESSAGES: dict[str, str] = {
    "insufficient_cash": "Not enough cash was available at the time to afford this trade.",
    "position_already_open": "A position was already open for this instrument -- only one at a time.",
    "no_open_position_to_sell": "There was no open position to sell.",
    "execution_bar_missing": "No price data was available for the execution bar -- the trade could not be priced.",
    "execution_bar_missing_eod": "No price data was available to close this position at session end.",
    "instrument_frozen_unresolved_eod": (
        "This instrument was frozen after an earlier end-of-day exit failed -- "
        "no further trades were attempted for it."),
}


def rejection_message(
    reason: str | None,
    *,
    intended_price: Decimal | None = None,
    max_position_value: Decimal | None = None,
) -> str | None:
    """None if reason is None (the order filled -- nothing to explain). Raises on
    an unrecognized reason rather than silently returning a blank string, so a
    future reason added without updating this module fails loudly (see
    test_rejection_messages.py's completeness test)."""
    if reason is None:
        return None
    if reason not in REJECTION_REASONS:
        raise ValueError(f"unrecognized rejection reason: {reason!r}")

    if reason == "price_exceeds_max_position":
        if intended_price is not None and max_position_value is not None:
            return (f"Price (${intended_price}) exceeded the ${max_position_value} max "
                    "position size -- no whole shares could be bought.")
        return "Price exceeded the max position size -- no whole shares could be bought."

    return _STATIC_MESSAGES[reason]
