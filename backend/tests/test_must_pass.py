"""CLAUDE.md section 9 must-pass tests, as strict-xfail placeholders.
When the real implementation lands, replace the body and REMOVE the xfail marker.
strict=True means an accidentally-passing placeholder fails the suite."""
import pytest


def placeholder(stage: str):
    return pytest.mark.xfail(strict=True, reason=f"not implemented until Stage {stage}",
                             raises=NotImplementedError)


@placeholder("E")
def test_1_math_and_loss_calculations_are_correct():
    """P/L maths incl. losses (not just gains), Decimal throughout."""
    raise NotImplementedError


@placeholder("E")
def test_2_buys_exceeding_available_cash_are_rejected():
    raise NotImplementedError


@placeholder("C")
def test_3_non_compliant_or_expired_symbols_are_rejected():
    """Missing, not_halal, doubtful, not_covered, or expired record -> order refused."""
    raise NotImplementedError


@placeholder("D")
def test_4_no_future_bars_used_next_bar_execution():
    """Signal on bar T executes at the OPEN of bar T+1."""
    raise NotImplementedError


@placeholder("E")
def test_5_no_fills_when_market_closed_or_holiday():
    raise NotImplementedError


@placeholder("B")
def test_6_api_failure_raises_never_invents_a_price():
    """Provider failure -> error. No forward-fill, interpolation or last-known price."""
    raise NotImplementedError


@placeholder("D")
def test_7_identical_strategy_run_gives_identical_results():
    """Same config + same bars -> byte-identical signals, orders and fills."""
    raise NotImplementedError


@placeholder("D")
def test_8_run_rejected_if_instruments_not_all_in_run_currency():
    """Single-currency simulation: no FX conversion. Mixed currency -> run rejected."""
    raise NotImplementedError
