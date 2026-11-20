"""Dashboard metrics, computed server-side in Decimal from stored paper_orders/
paper_fills ONLY -- never price_bars, never a live price lookup (Stage F plan
point 2).

Equity curve is a REALIZED curve: starting_cash, then one point per closed
round trip (a filled buy paired with the filled sell that closed it), moved by
that trade's own realized P&L -- never a point at the buy itself. A cash-ledger
curve (cash after every fill, buys included) would show the capital tied up in
an open position as a drawdown before anything is actually lost -- buying 2
shares nowhere near a loss would still crater the curve by the trade's full
cost. The realized curve avoids that, at the stated cost that it does not
reflect an open position's paper gains or losses while it's open, or at all if
it's never closed (the frontend states this limitation next to the chart).

An unresolved position (a filled buy with no matching filled sell -- Stage E
only ever produces this via a failed EOD exit) is excluded from every realized
metric entirely: not counted as a trade, not counted as a loss, no point on the
equity curve. Its fees (commission/spread/slippage already paid) still count
in total_fees -- that money is genuinely spent regardless of whether the
position ever resolves.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.paper import PaperFill, PaperOrder
from app.models.strategy import StrategyRun
from app.services.money import quantize_money

_RATIO_QUANT = Decimal("0.0001")


@dataclass(frozen=True)
class ClosedTrade:
    instrument_id: int
    buy_fill_ts: datetime
    sell_fill_ts: datetime
    pnl: Decimal


@dataclass(frozen=True)
class MetricsResult:
    trades_count: int
    realized_pnl: Decimal | None
    total_return: Decimal | None
    win_rate: Decimal | None
    avg_win: Decimal | None
    avg_non_win: Decimal | None
    total_fees: Decimal
    max_drawdown: Decimal
    unresolved_positions: int
    unresolved_instrument_ids: list[int]
    equity_curve: list[tuple[datetime, Decimal]]


def _pair_fills(filled: list[tuple[PaperOrder, PaperFill]]) -> tuple[list[ClosedTrade], list[int]]:
    """`filled` is every FILLED (order, fill) pair, ascending by fill_ts. Stage E
    enforces one open position per instrument at a time, so pairing is simply
    "the next sell for this instrument closes the most recent open buy" -- no
    ambiguity. Any buy left unmatched at the end is unresolved."""
    open_buy: dict[int, tuple[PaperOrder, PaperFill]] = {}
    closed: list[ClosedTrade] = []
    for order, fill in filled:
        if order.side == "buy":
            open_buy[order.instrument_id] = (order, fill)
            continue
        pair = open_buy.pop(order.instrument_id, None)
        if pair is None:
            continue  # defensive -- Stage E's own invariants should prevent this
        _buy_order, buy_fill = pair
        pnl = (fill.fill_price - buy_fill.fill_price) * fill.qty - buy_fill.commission - fill.commission
        closed.append(ClosedTrade(instrument_id=order.instrument_id, buy_fill_ts=buy_fill.fill_ts,
                                  sell_fill_ts=fill.fill_ts, pnl=pnl))
    return closed, sorted(open_buy.keys())


def _max_drawdown(points: list[tuple[datetime, Decimal]]) -> Decimal:
    peak = points[0][1]
    max_dd = Decimal("0")
    for _ts, value in points:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd.quantize(_RATIO_QUANT, rounding=ROUND_HALF_UP)


def compute_metrics(session: Session, run: StrategyRun) -> MetricsResult:
    rows = session.execute(
        select(PaperOrder, PaperFill)
        .join(PaperFill, PaperFill.order_id == PaperOrder.id, isouter=True)
        .where(PaperOrder.strategy_run_id == run.id)
    ).all()
    filled = sorted(((o, f) for o, f in rows if o.status == "filled"), key=lambda pair: pair[1].fill_ts)

    closed_trades, unresolved_ids = _pair_fills(filled)
    trades_count = len(closed_trades)
    wins = [t for t in closed_trades if t.pnl > 0]
    non_wins = [t for t in closed_trades if t.pnl <= 0]

    total_fees = quantize_money(sum((f.commission + f.spread_cost + f.slippage_cost for _o, f in filled),
                                    Decimal("0")))

    if trades_count == 0:
        realized_pnl = None
        total_return = None
        win_rate = None
    else:
        realized_pnl = quantize_money(sum((t.pnl for t in closed_trades), Decimal("0")))
        total_return = (realized_pnl / run.starting_cash).quantize(_RATIO_QUANT, rounding=ROUND_HALF_UP)
        win_rate = (Decimal(len(wins)) / Decimal(trades_count)).quantize(_RATIO_QUANT, rounding=ROUND_HALF_UP)

    avg_win = (quantize_money(sum((t.pnl for t in wins), Decimal("0")) / len(wins))
              if wins else None)
    avg_non_win = (quantize_money(sum((t.pnl for t in non_wins), Decimal("0")) / len(non_wins))
                  if non_wins else None)

    equity_curve: list[tuple[datetime, Decimal]] = [(run.range_start, run.starting_cash)]
    running = run.starting_cash
    for trade in sorted(closed_trades, key=lambda t: t.sell_fill_ts):
        running = quantize_money(running + trade.pnl)
        equity_curve.append((trade.sell_fill_ts, running))
    max_drawdown = _max_drawdown(equity_curve)

    return MetricsResult(
        trades_count=trades_count, realized_pnl=realized_pnl, total_return=total_return,
        win_rate=win_rate, avg_win=avg_win, avg_non_win=avg_non_win, total_fees=total_fees,
        max_drawdown=max_drawdown, unresolved_positions=len(unresolved_ids),
        unresolved_instrument_ids=unresolved_ids, equity_curve=equity_curve,
    )
