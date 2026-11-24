"""Keeps market prices moving on a deployed instance.

Runs as two scheduler jobs in the worker process when PRICE_FEED is enabled.
Crypto prices come from Binance, falling back to Yahoo Finance; stock prices
come from Yahoo Finance. Any asset whose live price cannot be fetched (provider
down, rate limited, region blocked, symbol delisted) takes a small random step
from its last price instead, so the platform never freezes. Each row records
which of these happened in `source`.

Every insert goes through the market_data trigger, so latest_prices_cache,
and with it portfolio values and unrealized P&L, follow automatically.
"""

import logging
import random
import time
from datetime import UTC, datetime
from decimal import Decimal

import requests
import yfinance as yf
from psycopg2.extras import execute_values

from . import database

logger = logging.getLogger(__name__)

BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
TIMEOUT_SECONDS = 5
PRICE_PLACES = Decimal("0.00000001")  # market_data.price is NUMERIC(18,8)

# After Binance is unreachable (some regions block it outright), stop asking
# for this long, so each run does not wait out a timeout per coin.
BINANCE_BACKOFF_SECONDS = 600

# Standard deviation of one simulated step, as a fraction of the price.
STEP_VOLATILITY = {"crypto": 0.002, "stock": 0.0008}

_session = requests.Session()
_binance_skip_until = 0.0


def _yahoo_price(ticker):
    price = yf.Ticker(ticker).fast_info["last_price"]
    if not price or price != price:  # None, zero or NaN
        raise ValueError(f"no price for {ticker}")
    # str() keeps the shortest decimal form instead of the float's binary expansion.
    return Decimal(str(price))


def _binance_price(symbol):
    global _binance_skip_until
    if time.monotonic() < _binance_skip_until:
        raise ConnectionError("Binance recently unreachable; skipping")
    try:
        resp = _session.get(
            BINANCE_URL, params={"symbol": f"{symbol}USDT"}, timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException:
        _binance_skip_until = time.monotonic() + BINANCE_BACKOFF_SECONDS
        raise
    resp.raise_for_status()  # e.g. a delisted pair: other coins are unaffected
    return Decimal(resp.json()["price"])


def fetch_crypto(symbol):
    """Return (price, source) for a coin, trying Binance, then Yahoo Finance."""
    try:
        return _binance_price(symbol), "binance"
    except Exception as exc:
        logger.debug("Binance unavailable for %s: %s", symbol, exc)
    return _yahoo_price(f"{symbol}-USD"), "yfinance"


def fetch_stock(symbol):
    """Return (price, source) for a stock from Yahoo Finance."""
    return _yahoo_price(symbol), "yfinance"


FETCHERS = {"crypto": fetch_crypto, "stock": fetch_stock}


def _random_step(price, volatility):
    step = Decimal(str(random.gauss(0, volatility)))
    # Floor at half the last price so a run of bad draws cannot reach zero.
    return max(price * (1 + step), price / 2).quantize(PRICE_PLACES)


def update_prices(asset_type):
    """Record one new price for every asset of `asset_type`.

    Returns (live, simulated) counts.
    """
    fetch = FETCHERS[asset_type]
    assets = database.query_all(
        "SELECT asset_id, symbol FROM assets WHERE type = %s ORDER BY asset_id", (asset_type,)
    )
    latest = dict(database.query_all("SELECT asset_id, price FROM latest_prices_cache"))

    now = datetime.now(UTC)
    rows = []
    for asset_id, symbol in assets:
        try:
            price, source = fetch(symbol)
        except Exception as exc:
            logger.debug("Live price unavailable for %s: %s", symbol, exc)
            last = latest.get(asset_id)
            if last is None:
                continue
            price, source = _random_step(last, STEP_VOLATILITY[asset_type]), "simulated"
        if price > 0:
            rows.append((asset_id, price.quantize(PRICE_PLACES), now, source))

    if rows:
        with database.transaction() as cur:
            execute_values(
                cur, "INSERT INTO market_data (asset_id, price, time, source) VALUES %s", rows
            )
    simulated = sum(1 for row in rows if row[3] == "simulated")
    live = len(rows) - simulated
    logger.info("Price feed (%s): %d live, %d simulated", asset_type, live, simulated)
    return live, simulated
