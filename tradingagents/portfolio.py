"""The caller's book, as the decision agents see it.

Optional input to a run: what is held, at what average price, and how much cash
is free. Without it the agents cannot tell adding to a full position from
opening a new one. Three states are distinct and must stay so: a position, a
flat book, and no context at all, since treating "not provided" as "flat" would
invent a fact about the caller's account.

Broker-neutral by construction: quantities are generic units and the currency is
whatever label the caller passes, so nothing here implies a venue or an
execution path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class Position(BaseModel):
    ticker: str = Field(description="Instrument symbol, e.g. AAPL")
    quantity: float = Field(description="Signed units held; negative is short")
    average_price: float | None = Field(default=None, description="Average entry price per unit")
    purchase_date: date | None = Field(
        default=None,
        description="Date of the latest purchase, from which a profit-sale lock (profit_lock_days) runs",
    )


class PortfolioContext(BaseModel):
    cash: float | None = Field(default=None, description="Free cash available")
    currency: str | None = Field(default=None, description="Currency label for cash and prices")
    positions: list[Position] = Field(default_factory=list)

    def position_in(self, ticker: str) -> Position | None:
        return next((p for p in self.positions if p.ticker.upper() == ticker.strip().upper()), None)

    def render(self, ticker: str, trade_date: str | None = None, profit_lock_days: int | None = None) -> str:
        """The portfolio block for the decision agents, led by the analyzed instrument.

        With ``profit_lock_days`` and the position's purchase date, says whether
        the position is still inside the period in which it may not be sold at a
        profit, as of ``trade_date``.
        """
        symbol = ticker.strip().upper()
        held = self.position_in(symbol)
        if held is None:
            lines = [f"- No current position in {symbol}"]
        else:
            price = f", average price {held.average_price:,.2f}" if held.average_price is not None else ""
            bought = f", bought {held.purchase_date.isoformat()}" if held.purchase_date is not None else ""
            lines = [f"- Current position in {symbol}: {held.quantity:,.4g} units{price}{bought}"]
            if held.purchase_date is not None and profit_lock_days and trade_date:
                unlocks = held.purchase_date + timedelta(days=profit_lock_days)
                if date.fromisoformat(str(trade_date)) < unlocks:
                    lines.append(f"- Until {unlocks.isoformat()} this position may be sold only at a loss, "
                                 "not at a profit")
        if self.cash is not None:
            lines.append(f"- Cash available: {self.cash:,.2f}{' ' + self.currency if self.currency else ''}")
        others = [p for p in self.positions if p is not held]
        if others:
            lines.append("- Other positions: " + ", ".join(f"{p.ticker.upper()} {p.quantity:,.4g}" for p in others))
        return "Portfolio at the analysis date:\n" + "\n".join(lines)

    def fingerprint(self) -> str:
        """Stable digest of the book, so a changed one cannot resume a stale run."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]


def coerce_profit_lock_days(value) -> int | None:
    """Validate ``profit_lock_days`` to a positive int, or None when unset.

    Accepts an int or a numeric string (env vars arrive as strings), and fails
    loudly on anything else so a misconfigured restriction stops the run at start.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"profit_lock_days must be an integer, not a boolean: {value!r}")
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"profit_lock_days must be an integer, got {value!r}") from exc
    if days < 1:
        raise ValueError(f"profit_lock_days must be >= 1, got {days}")
    return days


def load_portfolio(path: str | Path) -> PortfolioContext:
    """Read a portfolio JSON file, failing before the run rather than mid-graph."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return PortfolioContext.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"portfolio file {path} is not usable: {exc}") from exc
