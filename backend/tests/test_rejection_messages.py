from decimal import Decimal

import pytest

from app.models.paper import REJECTION_REASONS
from app.services.rejection_messages import rejection_message


def test_every_reason_has_a_message():
    """Completeness: a new reason added to REJECTION_REASONS without a message
    here must fail loudly, not render a blank cell."""
    for reason in REJECTION_REASONS:
        assert rejection_message(reason)


def test_none_reason_returns_none():
    assert rejection_message(None) is None


def test_unknown_reason_raises():
    with pytest.raises(ValueError):
        rejection_message("not_a_real_reason")


def test_price_exceeds_max_position_names_price_and_cap():
    msg = rejection_message("price_exceeds_max_position",
                           intended_price=Decimal("355.48"), max_position_value=Decimal("200"))
    assert "355.48" in msg
    assert "200" in msg


def test_price_exceeds_max_position_without_context_still_returns_a_message():
    assert rejection_message("price_exceeds_max_position") is not None
