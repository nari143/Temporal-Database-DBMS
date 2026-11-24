"""The live price feed: real prices when a provider answers, a small random
step from the last price when it does not, and never a frozen market."""

from decimal import Decimal

import pytest

from backend import price_feed

pytestmark = pytest.mark.db


def latest_price(db, aid):
    return db.query_value("SELECT price FROM latest_prices_cache WHERE asset_id = %s", (aid,))


def latest_source(db, aid):
    return db.query_value(
        "SELECT source FROM market_data WHERE asset_id = %s ORDER BY time DESC LIMIT 1", (aid,)
    )


def crypto_asset(db):
    return db.query_value("SELECT asset_id FROM assets WHERE symbol = 'BTC'")


@pytest.fixture
def priced_btc(clean_db, set_price):
    aid = crypto_asset(clean_db)
    set_price(aid, 100)
    return aid


class TestPriceFeed:
    def test_live_price_is_recorded_and_becomes_current(self, clean_db, priced_btc, monkeypatch):
        monkeypatch.setitem(
            price_feed.FETCHERS, "crypto", lambda symbol: (Decimal("123.45"), "binance")
        )
        live, simulated = price_feed.update_prices("crypto")

        assert live == 12 and simulated == 0
        assert latest_price(clean_db, priced_btc) == Decimal("123.45")
        assert latest_source(clean_db, priced_btc) == "binance"

    def test_unavailable_provider_falls_back_to_a_small_step(
        self, clean_db, priced_btc, monkeypatch
    ):
        def down(symbol):
            raise ConnectionError("provider unreachable")

        monkeypatch.setitem(price_feed.FETCHERS, "crypto", down)
        price_feed.update_prices("crypto")

        new = latest_price(clean_db, priced_btc)
        assert latest_source(clean_db, priced_btc) == "simulated"
        # One step is a fraction of a percent, never a jump.
        assert Decimal("95") < new < Decimal("105")

    def test_unrealized_pnl_follows_the_feed(self, clean_db, priced_btc, make_user, monkeypatch):
        user = make_user()
        clean_db.query_value(
            "SELECT place_order(%s, %s, 'buy', 2, 'market', NULL, NULL)", (user, priced_btc)
        )
        assert clean_db.query_value(
            "SELECT unrealized_pl FROM portfolio_summary WHERE user_id = %s", (user,)
        ) == Decimal("0")

        monkeypatch.setitem(
            price_feed.FETCHERS, "crypto", lambda symbol: (Decimal("110"), "binance")
        )
        price_feed.update_prices("crypto")

        assert clean_db.query_value(
            "SELECT unrealized_pl FROM portfolio_summary WHERE user_id = %s", (user,)
        ) == Decimal("20")

    def test_crypto_falls_back_to_yahoo_when_binance_is_blocked(self, monkeypatch):
        def blocked(symbol):
            raise ConnectionError("region blocked")

        monkeypatch.setattr(price_feed, "_binance_price", blocked)
        monkeypatch.setattr(
            price_feed, "_yahoo_price", lambda ticker: {"BTC-USD": Decimal("1")}[ticker]
        )
        assert price_feed.fetch_crypto("BTC") == (Decimal("1"), "yfinance")

    def test_random_step_never_reaches_zero(self, monkeypatch):
        monkeypatch.setattr(price_feed.random, "gauss", lambda mu, sigma: -5.0)
        assert price_feed._random_step(Decimal("10"), 0.002) == Decimal("5.00000000")
