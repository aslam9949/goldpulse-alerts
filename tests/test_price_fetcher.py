"""
Regression tests for India price markup.

Guards the S4 fix: INDIA_MARKUP used to be applied in exactly one (dead)
method, so every fallback path silently produced a markup-free INR price.
Every fetch path must now route through apply_india_pricing().
"""

import asyncio
import sys
import types

import pytest

import ingestion.price_fetcher as pf
from ingestion.price_fetcher import PriceFetcher, apply_india_pricing


# -- apply_india_pricing math -------------------------------------------

def test_apply_india_pricing_applies_markup():
    assert apply_india_pricing(4157.0, 83.5) == pytest.approx(
        4157.0 * 83.5 * 1.15
    )


def test_apply_india_pricing_no_inr_rate():
    assert apply_india_pricing(4157.0, None) is None


# -- every fetch path must route through apply_india_pricing ------------

class _Resp:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._data

    async def text(self):
        return self._data

    @property
    def headers(self):
        return {}


class _FakeSession:
    """Routes by URL substring to canned bodies."""

    closed = False  # aiohttp.ClientSession attribute checked by _get_session

    def __init__(self, routes):
        self._routes = routes

    def get(self, url, **kw):
        # aiohttp's AsyncClientSession.get returns an async context manager;
        # mirror that with a plain call returning _Resp.
        for sub, data in self._routes.items():
            if sub in url:
                return _Resp(200, data)
        return _Resp(404, None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def close(self):
        pass


_ROUTES = {
    "swissquote.com": [{"spreadProfilePrices": [{"bid": 4150, "ask": 4151}]}],
    "query1.finance.yahoo.com": {
        "chart": {"result": [{"meta": {"regularMarketPrice": 4155.0,
                                       "previousClose": 4150.0}}]}
    },
    "goldapi.io": {"price": 4155.0, "prev_close_price": 4150.0},
    "google.com/finance": 'data-last-price="4155.00"',
    "er-api.com/v6/latest/XAU": {"rates": {"USD": 4155.0}},
    "er-api.com/v6/latest/USD": {"rates": {"INR": 83.5}},
}


def _inject_fake_yfinance():
    """Fake the yfinance module so the yfinance path runs offline."""
    fake = types.ModuleType("yfinance")

    class _Indexer:
        def __getitem__(self, i):
            return [4150.0, 4155.0][i]

    class _Close:
        iloc = _Indexer()

    class _Data:
        empty = False

        def __getitem__(self, key):
            return _Close()

        def __len__(self):
            return 2

    class _Ticker:
        def history(self, *a, **k):
            return _Data()

    fake.download = lambda *a, **k: _Data()
    fake.Ticker = lambda *a, **k: _Ticker()
    sys.modules["yfinance"] = fake


def test_all_fetch_paths_apply_markup():
    _inject_fake_yfinance()

    async def _run():
        fetcher = PriceFetcher()
        fetcher._session = _FakeSession(_ROUTES)
        pf.GOLDAPI_KEY = "test-key"  # enable the GoldAPI path

        calls: list[tuple[float, float]] = []
        original = pf.apply_india_pricing

        def _spy(usd, inr):
            calls.append((usd, inr))
            return original(usd, inr)

        pf.apply_india_pricing = _spy
        try:
            paths = [
                fetcher._fetch_swissquote_spot,
                fetcher._fetch_yfinance_gold,
                fetcher._fetch_yahoo_direct,
                fetcher._fetch_goldapi,
                fetcher._fetch_google_finance,
                fetcher._fetch_exchangerate,
            ]
            results = []
            for path in paths:
                result = await path()
                assert result is not None, f"{path.__name__} returned None"
                results.append(result)

            # Every path produced an INR price THROUGH the shared function.
            assert len(calls) == len(paths)
            for result in results:
                assert result.price_inr is not None
        finally:
            pf.apply_india_pricing = original

    asyncio.run(_run())