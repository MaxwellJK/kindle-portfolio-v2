"""
Price cache — fetches live prices and FX rates from Yahoo Finance.

Prices are cached in memory and refreshed every REFRESH_INTERVAL_MINUTES.
The cache is keyed by ticker symbol as stored in your DB (e.g. AAPL, BATS.L).

Yahoo ticker conventions
─────────────────────────
  US stocks   : AAPL, MSFT            → as-is, price in USD
  LSE stocks  : BATS.L, RKT.L         → append .L, price in GBP (pence for some)
  TSX stocks  : CNQ.TO, BNS.TO        → append .TO, price in CAD

We detect the exchange from currency_price_per_share stored in your DB:
  USD → fetch as-is
  GBP / GBX → try ticker as-is first, then ticker.L
  CAD → try ticker.TO

FX rates are fetched once per session as GBPUSD=X, GBPCAD=X etc.
All prices returned are in GBP.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Avoid TzCache permission warning by pointing to a writable temp dir
try:
    yf.set_tz_cache_location("/tmp/yfinance-cache")
except Exception:
    pass

logger = logging.getLogger(__name__)

# Browser-like session — bypasses Yahoo's basic bot filtering
def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.5",
    })
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session

logger = logging.getLogger(__name__)

REFRESH_MINUTES = int(os.environ.get("REFRESH_INTERVAL_MINUTES", "15"))


class PriceCache:
    """
    In-memory cache of live GBP prices per ticker.
    Call await refresh(tickers_with_currencies) to populate.
    Then gbp_price(ticker) returns the latest GBP price.
    """

    def __init__(self):
        self._prices: dict[str, float] = {}       # ticker → GBP price
        self._fx: dict[str, float] = {}           # "USD" → GBP/USD rate
        self._last_refresh: datetime | None = None
        self._lock = asyncio.Lock()

    def is_stale(self) -> bool:
        if self._last_refresh is None:
            return True
        age = (datetime.now(timezone.utc) - self._last_refresh).total_seconds()
        return age > REFRESH_MINUTES * 60

    def gbp_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker.upper())

    async def refresh(self, positions: dict[str, dict]):
        """
        positions: {ticker: {"currency": "USD"|"GBP"|"GBX"|"CAD", ...}}
        Fetches live prices and converts everything to GBP.
        """
        async with self._lock:
            if not self.is_stale():
                return

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._fetch_sync, positions)
            self._last_refresh = datetime.now(timezone.utc)
            logger.info(f"Price cache refreshed — {len(self._prices)} tickers")

    def _fetch_sync(self, positions: dict[str, dict]):
        # ── Determine which FX rates we need ─────────────────────────────
        currencies = {v["currency"].upper() for v in positions.values()}
        currencies -= {"GBP", "GBX", ""}
        fx_tickers = list({f"GBP{ccy}=X" for ccy in currencies})

        # ── Build Yahoo ticker map ────────────────────────────────────────
        ticker_map: dict[str, tuple[str, str]] = {}
        for db_ticker, pos in positions.items():
            ccy = pos["currency"].upper()
            yahoo = _to_yahoo_ticker(db_ticker, ccy)
            ticker_map[yahoo] = (db_ticker, ccy)

        all_tickers = list(ticker_map.keys()) + fx_tickers
        if not all_tickers:
            return

        session = _make_session()

        # ── Fetch one ticker at a time ────────────────────────────────────
        raw: dict[str, float] = {}
        for sym in all_tickers:
            for attempt in range(3):
                try:
                    data = yf.download(
                        sym,
                        period="2d",
                        interval="1d",
                        auto_adjust=True,
                        progress=False,
                        session=session,
                    )
                    closes = data["Close"].dropna()
                    if not closes.empty:
                        raw[sym] = float(closes.iloc[-1])
                    time.sleep(0.5)
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))
                    else:
                        logger.warning(f"Could not fetch {sym}: {e}")

        # ── Parse FX rates ────────────────────────────────────────────────
        new_fx: dict[str, float] = {}
        for fx_sym in fx_tickers:
            ccy = fx_sym[3:6]
            rate = raw.get(fx_sym)
            if rate:
                new_fx[ccy] = 1.0 / rate   # GBPUSD=X → GBP per 1 USD
            else:
                logger.warning(f"Could not fetch FX rate for {fx_sym}")

        # ── Parse stock prices → convert to GBP ───────────────────────────
        new_prices: dict[str, float] = {}
        for yahoo_sym, (db_ticker, ccy) in ticker_map.items():
            price = raw.get(yahoo_sym)
            if price is None:
                logger.warning(f"No live price for {db_ticker} ({yahoo_sym}), will use fallback")
                continue

            if ccy == "GBP":
                gbp_price = price
            elif ccy == "GBX":
                gbp_price = price / 100.0
            elif ccy in new_fx:
                gbp_price = price * new_fx[ccy]
            else:
                logger.warning(f"No FX rate for {ccy}, skipping {db_ticker}")
                continue

            new_prices[db_ticker.upper()] = gbp_price

        self._prices = new_prices
        self._fx = new_fx


def _to_yahoo_ticker(ticker: str, currency: str) -> str:
    """Map a DB ticker + currency to the Yahoo Finance symbol."""
    t = ticker.upper()
    ccy = currency.upper()

    # Already has an exchange suffix
    if "." in t:
        return t

    if ccy in ("GBP", "GBX"):
        return f"{t}.L"      # London Stock Exchange
    if ccy == "CAD":
        return f"{t}.TO"     # Toronto Stock Exchange
    if ccy == "EUR":
        return f"{t}.DE"     # Deutsche Börse (common for EUR ETFs — may need tuning)

    # Default: US market, no suffix
    return t
