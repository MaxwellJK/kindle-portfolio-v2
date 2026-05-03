"""
Derives portfolio metrics from two sources:
  1. Your Postgres transaction log → cost basis, dividends, deposits/withdrawals
  2. Trading 212 API              → live positions, cash, account totals
"""

import os
import logging
from datetime import datetime, timezone
from decimal import Decimal

from .database import Database
from .models import Holding, PortfolioSummary
from .t212 import T212Client

logger = logging.getLogger(__name__)

TABLE       = os.environ.get("TABLE_NAME", "transactions")
DISPLAY_CCY = os.environ.get("DISPLAY_CURRENCY", "GBP")

BUY_ACTIONS  = {"market buy", "limit buy", "stock split open"}
SELL_ACTIONS = {"market sell", "stock split close"}
DIV_ACTIONS  = {
    "dividend (ordinary)",
    "dividend (dividends paid by us corporations)",
    "dividend (dividends paid by foreign corporations)",
    "dividend (bonus)",
    "dividend (dividend)",
    "dividend adjustment",
}
INT_ACTIONS  = {"interest on cash"}


class PortfolioService:
    def __init__(self, db: Database, t212: T212Client):
        self.db   = db
        self.t212 = t212

    async def get_summary(self) -> PortfolioSummary:
        # ── Fetch live data from T212 ──────────────────────────────────────
        await self.t212.refresh()

        # ── Walk transaction log ───────────────────────────────────────────
        rows = await self.db.pool.fetch(
            f"""
            SELECT action, time, isin, TRIM(ticker) AS ticker, name,
                   no_of_shares, price_per_share,
                   currency_price_per_share AS price_currency,
                   exchange_rate, total, currency_total AS total_currency
            FROM {TABLE}
            ORDER BY time ASC
            """
        )

        positions: dict[str, dict] = {}
        total_dividends = Decimal("0")
        total_interest  = Decimal("0")
        total_deposited = Decimal("0")
        total_withdrawn = Decimal("0")
        as_of = datetime.now(timezone.utc)

        for row in rows:
            action = (row["action"] or "").strip().lower()
            ticker = (row["ticker"] or "").strip().upper()
            as_of  = row["time"] if row["time"] else as_of

            if action in DIV_ACTIONS:
                total_dividends += _decimal(row["total"])
                continue
            if action in INT_ACTIONS:
                total_interest += _decimal(row["total"])
                continue
            if action == "deposit":
                total_deposited += abs(_decimal(row["total"]))
                continue
            if action == "withdrawal":
                total_withdrawn += abs(_decimal(row["total"]))
                continue
            if action not in BUY_ACTIONS | SELL_ACTIONS:
                continue
            if not ticker:
                continue

            shares = _decimal(row["no_of_shares"]) or Decimal("0")
            total  = _decimal(row["total"]) or Decimal("0")   # always GBP

            if ticker not in positions:
                positions[ticker] = {
                    "shares":     Decimal("0"),
                    "cost_basis": Decimal("0"),
                    "avg_cost":   Decimal("0"),
                    "name":       row["name"] or ticker,
                    "isin":       row["isin"],
                    "last_tx":    row["time"],
                }

            pos = positions[ticker]

            if action in BUY_ACTIONS:
                new_shares        = pos["shares"] + shares
                new_cost          = pos["cost_basis"] + total
                pos["shares"]     = new_shares
                pos["cost_basis"] = new_cost
                pos["avg_cost"]   = new_cost / new_shares if new_shares else Decimal("0")
            elif action in SELL_ACTIONS:
                pos["shares"] -= shares
                pos["cost_basis"] = (
                    pos["avg_cost"] * pos["shares"]
                    if pos["shares"] > 0 else Decimal("0")
                )

            if row["time"]:
                pos["last_tx"] = row["time"]
            if row["name"]:
                pos["name"] = row["name"]

        # ── Build holdings using T212 live data ────────────────────────────
        holdings: list[Holding] = []

        for ticker, pos in positions.items():
            if pos["shares"] <= Decimal("0.0001"):
                continue

            t212_pos = self.t212.get_position(ticker)
            if t212_pos is None:
                logger.debug(f"No T212 position for '{ticker}' — likely closed, skipping")
                continue

            gain_loss     = Decimal(str(t212_pos.gain_loss))
            current_value = pos["cost_basis"] + gain_loss
            gain_loss_pct = (
                float(gain_loss / pos["cost_basis"] * 100)
                if pos["cost_basis"] != 0 else 0.0
            )

            holdings.append(Holding(
                ticker           = ticker,
                name             = pos["name"],
                isin             = pos["isin"],
                shares           = float(pos["shares"]),
                avg_cost         = float(pos["avg_cost"]),
                latest_price     = t212_pos.current_price,
                currency         = DISPLAY_CCY,
                cost_basis       = float(pos["cost_basis"]),
                current_value    = float(current_value),
                gain_loss        = float(gain_loss),
                gain_loss_pct    = gain_loss_pct,
                last_transaction = pos["last_tx"],
            ))

        holdings.sort(key=lambda h: h.current_value, reverse=True)

        # ── Headline numbers from T212 summary — exact match with app ──────
        net_deposited    = total_deposited - total_withdrawn
        cash_balance     = Decimal(str(self.t212.cash))
        t212_total       = Decimal(str(self.t212.total_value))
        t212_cost        = Decimal(str(self.t212.total_cost))
        t212_pnl         = Decimal(str(self.t212.unrealized_pnl))
        t212_gl_pct      = float(t212_pnl / t212_cost * 100) if t212_cost else 0.0
        cash_on_cash_gl  = t212_total - net_deposited
        cash_on_cash_pct = float(cash_on_cash_gl / net_deposited * 100) if net_deposited else 0.0

        return PortfolioSummary(
            total_cost_basis    = float(t212_cost),
            total_current_value = float(t212_total),
            cash_balance        = float(cash_balance),
            total_gain_loss     = float(t212_pnl),
            total_gain_loss_pct = t212_gl_pct,
            net_deposited       = float(net_deposited),
            cash_on_cash_gl     = float(cash_on_cash_gl),
            cash_on_cash_pct    = cash_on_cash_pct,
            total_dividends     = float(total_dividends),
            total_interest      = float(total_interest),
            currency            = DISPLAY_CCY,
            holdings            = holdings,
            market_open         = self._is_market_open(),
            generated_at        = datetime.now(timezone.utc),
            as_of               = as_of,
        )

    async def get_monthly_dividends(self, months: int = 12) -> list[tuple[int, int, float]]:
        rows = await self.db.pool.fetch(
            f"""
            SELECT
                EXTRACT(YEAR  FROM time)::int AS yr,
                EXTRACT(MONTH FROM time)::int AS mo,
                SUM(ABS(total))               AS total
            FROM {TABLE}
            WHERE LOWER(action) = ANY($1::text[])
              AND time >= now() - ($2 || ' months')::interval
            GROUP BY yr, mo
            ORDER BY yr, mo
            """,
            list(DIV_ACTIONS),
            str(months),
        )
        return [(r["yr"], r["mo"], float(r["total"])) for r in rows]

    @staticmethod
    def _is_market_open() -> bool:
        """Rough check — US market hours Mon-Fri 14:30-21:00 UTC."""
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return False
        h = now.hour + now.minute / 60
        return 14.5 <= h <= 21.0


def _decimal(val) -> Decimal:
    if val is None:
        return Decimal("0")
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal("0")
