import json
from pathlib import Path

import pandas as pd
import pytest

from lse_terminal.contracts import (
    ROW_TYPES, TIMEFRAME_SECONDS, NotSupported, Provider, to_epoch)
from lse_terminal.providers import DemoProvider, LseProvider, UserDataProvider

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "lse_rows.json").read_text())


def test_timeframe_table_has_no_tick():
    assert "tick" not in TIMEFRAME_SECONDS
    assert TIMEFRAME_SECONDS["1m"] == 60
    assert TIMEFRAME_SECONDS["1d"] == 86400
    assert list(TIMEFRAME_SECONDS) == [
        "1s", "30s", "1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]


@pytest.mark.parametrize("value,expected", [
    (1700000000, 1700000000),
    (1700000000.9, 1700000000),
    ("1700000000", 1700000000),
    ("2024-01-02", 1704153600),
    ("2024-01-02T03:04:05Z", 1704164645),
    ("2024-01-02T03:04:05+00:00", 1704164645),
    ("2024-01-02T05:04:05+02:00", 1704164645),
    ("2024-01-02 03:04:05", 1704164645),  # naive means UTC
    ("-5", -5),
    ("  1700000000 ", 1700000000),
])
def test_to_epoch_shapes(value, expected):
    assert to_epoch(value) == expected


def test_to_epoch_none_and_empty():
    assert to_epoch(None) is None
    assert to_epoch("") is None


def test_to_epoch_whitespace_only_is_empty():
    assert to_epoch("  ") is None


def test_to_epoch_rejects_bool():
    with pytest.raises(TypeError):
        to_epoch(True)


@pytest.mark.parametrize("bad", ["nope", "1700000000.5"])
def test_to_epoch_names_bad_input(bad):
    with pytest.raises(ValueError, match="unparseable bound"):
        to_epoch(bad)


@pytest.mark.parametrize("method", sorted(ROW_TYPES))
def test_fixture_row_has_required_keys(method):
    row = FIXTURE[method]
    if row is None:
        pytest.skip(f"fixture has no {method} row")
    missing = ROW_TYPES[method].__required_keys__ - set(row)
    assert not missing, f"{method} fixture lacks {sorted(missing)}"


@pytest.mark.parametrize("method", sorted(ROW_TYPES))
def test_required_keys_are_declared(method):
    t = ROW_TYPES[method]
    assert t.__required_keys__ <= set(t.__annotations__)


def test_required_sets():
    from lse_terminal.contracts import (
        CalendarRow, ChainRow, FlowRow, PriceRow, UnderlyingRow)
    assert ChainRow.__required_keys__ == {"contract_type", "strike", "expiry", "last_price"}
    assert FlowRow.__required_keys__ == {"contract_type", "strike", "expiry", "premium"}
    assert PriceRow.__required_keys__ == {"symbol", "price"}
    assert CalendarRow.__required_keys__ == {"datetime", "event"}
    assert UnderlyingRow.__required_keys__ == {"symbol"}
    assert "underlying_price" in FlowRow.__optional_keys__


def test_timeframe_table_is_frozen():
    with pytest.raises(TypeError):
        TIMEFRAME_SECONDS["2m"] = 120


LIFTED = ["prices", "screener", "logos", "options_underlyings", "option_chain",
          "options_flow", "economic_calendar", "macro_catalog", "macro_series"]
_ARGS = {"prices": (["X"],), "option_chain": ("X",), "macro_series": ("X",)}


class Bare(Provider):
    name = "bare"

    def search(self, query="", limit=50):
        return []

    def candles(self, symbol, timeframe, limit=500, start=None, end=None):
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])


def test_attributes_defaults():
    assert Bare.is_custom is False
    assert Bare.is_broker is False
    assert Bare.cacheable is True
    assert Bare.settle_seconds == 60
    assert dict(Bare.snapshot_ttl) == {}


def test_snapshot_ttl_default_is_read_only():
    with pytest.raises(TypeError):
        Bare.snapshot_ttl["option_chain"] = 1


@pytest.mark.parametrize("method", LIFTED)
def test_default_raises_not_supported(method):
    with pytest.raises(NotSupported):
        getattr(Bare(), method)(*_ARGS.get(method, ()))


def test_bare_capabilities():
    assert Bare().capabilities() == {"search", "candles"}


def test_partial_options_is_not_options():
    class Half(Bare):
        def option_chain(self, underlying, type=None, expiry=None, limit=5000):
            return []
    assert "options" not in Half().capabilities()


def test_full_surface_capabilities():
    class Full(Bare):
        def quote(self, symbol):
            return None

        async def stream(self, symbols):
            yield {}
    for m in LIFTED:
        setattr(Full, m, lambda self, *a, **k: [])
    assert Full().capabilities() == {
        "search", "candles", "quote", "stream", "prices", "screener", "logos",
        "options", "flow", "calendar", "macro"}


def test_lse_claims_every_lifted_surface():
    caps = LseProvider(api_key="x").capabilities()
    assert {"prices", "screener", "logos", "options", "flow",
            "calendar", "macro"} <= caps


def test_local_sources_opt_out_of_cache():
    assert DemoProvider.cacheable is False
    assert UserDataProvider.cacheable is False


def test_demo_claims_no_lifted_surface():
    # demo must never win a capability fallback over LSE
    lifted = {"prices", "screener", "logos", "options", "flow", "calendar", "macro"}
    assert not (DemoProvider().capabilities() & lifted)


def test_broker_capabilities():
    from lse_terminal.engine.broker_hub import BrokerProvider
    b = BrokerProvider.__new__(BrokerProvider)  # skip the hub
    assert b.capabilities() == {"candles", "prices", "quote", "search"}


from lse_terminal.testing import check_provider  # noqa: E402


def test_claimed_capability_that_raises_is_a_violation():
    class Liar(DemoProvider):
        def screener(self):
            raise NotSupported("no")
    problems = check_provider(Liar())
    assert any("screener" in p and "NotSupported" in p for p in problems)


def test_missing_row_key_is_a_violation():
    class BadChain(DemoProvider):
        def options_underlyings(self):
            return [{"symbol": "X"}]

        def option_chain(self, underlying, type=None, expiry=None, limit=5000):
            return [{"contract_type": "call", "expiry": "2030-01-01", "last_price": 1.0}]
    problems = check_provider(BadChain())
    assert any("option_chain" in p and "strike" in p for p in problems)


def test_inclusive_end_is_a_violation():
    class Inclusive(DemoProvider):
        def candles(self, symbol, timeframe, limit=500, start=None, end=None):
            df = super().candles(symbol, timeframe, limit=limit, start=start)
            e = to_epoch(end)
            return df if e is None else df[df["ts"] <= e].reset_index(drop=True)
    problems = check_provider(Inclusive())
    assert any("end is exclusive" in p for p in problems)


def test_limit_not_respected_on_a_surface_is_a_violation():
    class Chatty(DemoProvider):
        def options_flow(self, underlying=None, type=None, min_premium=None, limit=300):
            row = {"contract_type": "call", "strike": 1.0, "expiry": "2030-01-01", "premium": 1.0}
            return [row] * (limit + 3)
    problems = check_provider(Chatty())
    assert any("options_flow" in p and "limit" in p for p in problems)


def test_fixture_rows_pass_the_harness():
    class Replay(DemoProvider):
        pass
    for m in ROW_TYPES:
        row = FIXTURE[m]
        if row is not None:
            setattr(Replay, m, lambda self, *a, _r=row, **k: [_r])
    for m in ("screener", "logos"):
        if FIXTURE[m] is not None:
            setattr(Replay, m, lambda self, *a, _r=FIXTURE[m], **k: _r)
    assert check_provider(Replay()) == []


def test_non_dict_row_is_reported_not_raised():
    class Bad(DemoProvider):
        def options_flow(self, underlying=None, type=None, min_premium=None, limit=300):
            return [1]
    problems = check_provider(Bad())
    assert any("options_flow" in p and "not a dict" in p for p in problems)


def test_bad_listing_is_blamed_on_the_listing_method():
    class BadListing(DemoProvider):
        def options_underlyings(self):
            return [{"x": 1}]

        def option_chain(self, underlying, type=None, expiry=None, limit=5000):
            return []
    problems = check_provider(BadListing())
    assert any("options_underlyings" in p and "symbol" in p for p in problems)
    assert any("option_chain" in p and "not checked" in p for p in problems)
    assert not any(p.startswith("option_chain: raised") for p in problems)


def test_too_few_bars_is_reported():
    class Short(DemoProvider):
        def candles(self, symbol, timeframe, limit=500, start=None, end=None):
            return super().candles(symbol, timeframe, limit=limit,
                                   start=start, end=end).head(2)
    problems = check_provider(Short())
    assert any("candle clauses not checked: need at least 3 bars" in p
               for p in problems)


def test_default_timeframe_falls_through_to_one_that_works():
    class OnlyHourly(DemoProvider):
        def candles(self, symbol, timeframe, limit=500, start=None, end=None):
            if timeframe != "1h":
                raise ValueError("native timeframe only")
            return super().candles(symbol, timeframe, limit=limit,
                                   start=start, end=end)
    assert check_provider(OnlyHourly()) == []


def test_nondeterministic_provider_claiming_determinism_is_a_violation():
    class Drift(DemoProvider):
        deterministic = True
        calls = 0

        def candles(self, symbol, timeframe, limit=500, start=None, end=None):
            df = super().candles(symbol, timeframe, limit=limit,
                                 start=start, end=end).copy()
            Drift.calls += 1
            df.loc[df.index[-1], "volume"] += Drift.calls
            return df
    problems = check_provider(Drift())
    assert "deterministic=True but two identical calls differ" in problems
