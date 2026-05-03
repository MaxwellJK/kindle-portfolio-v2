"""
Trading 212 API client.

Endpoints used:
  GET /equity/portfolio          → open positions (for per-holding detail)
  GET /equity/account/summary    → exact account totals in GBP

Set T212_API_KEY in docker-compose.yml.
Set T212_ENV to 'demo' for paper trading, 'live' for real money (default).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

T212_API_KEY    = os.environ.get("T212_API_KEY", "")
T212_ENV        = os.environ.get("T212_ENV", "live")
BASE_URL        = f"https://{T212_ENV}.trading212.com/api/v0"
REFRESH_MINUTES = int(os.environ.get("REFRESH_INTERVAL_MINUTES", "15"))

# Manual overrides where T212 internal ticker differs from your DB ticker
_TICKER_OVERRIDES = {
    "RB": "RKT",
}


class T212Position:
    def __init__(self, data: dict):
        self.ticker        = data.get("ticker", "")
        self.quantity      = float(data.get("quantity", 0))
        self.avg_price     = float(data.get("averagePrice", 0))
        self.current_price = float(data.get("currentPrice", 0))
        self.gain_loss     = float(data.get("ppl", 0) or 0)   # GBP


class T212Client:
    def __init__(self):
        self._positions: dict[str, T212Position] = {}
        self._cash:           float = 0.0
        self._total_value:    float = 0.0
        self._invested_value: float = 0.0
        self._total_cost:     float = 0.0
        self._unrealized_pnl: float = 0.0
        self._last_refresh: datetime | None = None
        self._lock = asyncio.Lock()

    def is_stale(self) -> bool:
        if self._last_refresh is None:
            return True
        if not self._positions:
            return True
        age = (datetime.now(timezone.utc) - self._last_refresh).total_seconds()
        return age > REFRESH_MINUTES * 60

    def get_position(self, ticker: str) -> T212Position | None:
        return self._positions.get(ticker.upper())

    def all_positions(self) -> dict[str, T212Position]:
        return self._positions

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def total_value(self) -> float:
        return self._total_value

    @property
    def invested_value(self) -> float:
        return self._invested_value

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def unrealized_pnl(self) -> float:
        return self._unrealized_pnl

    async def refresh(self):
        if not self.is_stale():
            return
        async with self._lock:
            if not self.is_stale():
                return
            if not T212_API_KEY:
                logger.error("T212_API_KEY not set — cannot fetch live data")
                return

            headers = {"Authorization": T212_API_KEY}

            async with httpx.AsyncClient(
                base_url=BASE_URL, headers=headers, timeout=15
            ) as client:
                await asyncio.gather(
                    self._fetch_positions(client),
                    self._fetch_summary(client),
                )

            self._last_refresh = datetime.now(timezone.utc)
            logger.info(
                f"T212 refresh complete — "
                f"{len(self._positions)} positions, "
                f"total £{self._total_value:,.2f}, "
                f"cash £{self._cash:,.2f}"
            )

    async def _fetch_positions(self, client: httpx.AsyncClient):
        try:
            r = await client.get("/equity/portfolio")
            r.raise_for_status()
            data = r.json()
            positions = data if isinstance(data, list) else data.get("items", [])
            self._positions = {
                _clean_ticker(p.get("ticker", "")).upper(): T212Position(p)
                for p in positions
                if p.get("ticker")
            }
        except Exception as e:
            logger.error(f"Failed to fetch T212 positions: {e}")

    async def _fetch_summary(self, client: httpx.AsyncClient):
        try:
            r = await client.get("/equity/account/summary")
            r.raise_for_status()
            data = r.json()
            investments          = data.get("investments", {})
            cash                 = data.get("cash", {})
            self._total_value    = float(data.get("totalValue", 0))
            self._invested_value = float(investments.get("currentValue", 0))
            self._total_cost     = float(investments.get("totalCost", 0))
            self._unrealized_pnl = float(investments.get("unrealizedProfitLoss", 0))
            self._cash           = float(cash.get("availableToTrade", 0))
        except Exception as e:
            logger.error(f"Failed to fetch T212 summary: {e}")


def _clean_ticker(t212_ticker: str) -> str:
    """
    T212 tickers:
      'AAPL_US_EQ'  → 'AAPL'
      'BATSl_EQ'    → 'BATS'  (LSE: lowercase suffix before _EQ)
      'VUSCd_EQ'    → 'VUSC'
    """
    base = t212_ticker.split("_")[0]
    cleaned = base.rstrip("abcdefghijklmnopqrstuvwxyz") or base
    return _TICKER_OVERRIDES.get(cleaned, cleaned)
