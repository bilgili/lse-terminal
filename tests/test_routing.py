"""Route source resolution (design D2): one resolver, static default
source, explicit provider never falls back, X-Source on every answer,
per-provider route snapshot cache."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from lse_terminal.contracts import NotSupported, Provider
from lse_terminal.engine import registry as regmod
from lse_terminal.engine.server import create_app
from lse_terminal.providers import LseProvider

ROOT = Path(__file__).resolve().parents[1]
FIX = json.loads((ROOT / "tests/fixtures/lse_rows.json").read_text())
# The fixture has no macro rows (captured without a macro plan); synthetic
# ones in the same spirit, epochs after 1e9.
MACRO_CAT_ROW = {"symbol": "US.CPI", "name": "US CPI", "dataset": "economics"}
MACRO_SERIES_ROW = {"symbol": "US.CPI", "ts": 1_700_000_000, "value": 3.1}

CAL = "LSE API key not set. Add your free key to load the economic calendar."
MACRO = "LSE API key not set. Add your free key to load macro data."
OPT = "LSE API key not set. Add your free key under MARKETS to load options data."
SCR = ("LSE provider needs an API key. Get a free one at "
       "https://londonstrategicedge.com/data#api and set it in the terminal.")


class FullStubProvider(Provider):
    name = "stub"
    title = "Stub"

    def __init__(self):
        self.on = True
        self.calls: dict[str, int] = {}
        self.fail: Exception | None = None

    def _hit(self, m, value):
        self.calls[m] = self.calls.get(m, 0) + 1
        if self.fail is not None:
            raise self.fail
        return value

    def total(self):
        return sum(self.calls.values())

    def configured(self):
        return self.on

    def search(self, query="", limit=50):
        return []

    def candles(self, symbol, timeframe, limit=500, start=None, end=None):
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

    def quote(self, symbol):
        return self._hit("quote", None)

    async def stream(self, symbols):
        yield {}

    def prices(self, symbols):
        return self._hit("prices", [FIX["prices"]])

    def screener(self):
        return self._hit("screener", FIX["screener"])

    def logos(self):
        return self._hit("logos", FIX["logos"])

    def options_underlyings(self):
        return self._hit("options_underlyings", [FIX["options_underlyings"]])

    def option_chain(self, underlying, type=None, expiry=None, limit=5000):
        return self._hit("option_chain", [FIX["option_chain"]])

    def options_flow(self, underlying=None, type=None, min_premium=None, limit=300):
        return self._hit("options_flow", [FIX["options_flow"]])

    def economic_calendar(self, region=None, event=None, start=None, end=None,
                          released_only=False, order="asc", limit=5000):
        return self._hit("economic_calendar", [FIX["economic_calendar"]])

    def macro_catalog(self):
        return self._hit("macro_catalog", [MACRO_CAT_ROW])

    def macro_series(self, symbol, dataset=None, start=None, end=None,
                     order="asc", limit=5000):
        return self._hit("macro_series", [MACRO_SERIES_ROW])


LSE_ROW = {"source": "lse"}

# (path, params, provider method, 502 prefix, LSE 409 text)
ROUTES = [
    ("/api/economic-calendar", {}, "economic_calendar", "economic calendar", CAL),
    ("/api/macro/catalog", {}, "macro_catalog", "macro catalog", MACRO),
    ("/api/macro/series", {"symbol": "US.CPI"}, "macro_series", "macro series", MACRO),
    ("/api/options/underlyings", {}, "options_underlyings", "options request", OPT),
    ("/api/options/chain", {"underlying": "X"}, "option_chain", "options request", OPT),
    ("/api/options/flow", {"underlying": "X"}, "options_flow", "options request", OPT),
    ("/api/screener", {}, "screener", "screener", SCR),
    ("/api/prices", {"symbols": "X"}, "prices", "prices", "lse is not configured"),
]
IDS = [r[0] for r in ROUTES]


@pytest.fixture()
def stub():
    return FullStubProvider()


@pytest.fixture()
def lse(monkeypatch):
    """LSE key state plus a record of LSE method calls. Off by default."""
    st = SimpleNamespace(on=False, calls=[])
    monkeypatch.setattr(LseProvider, "configured", lambda self: st.on)
    for _, _, m, _, _ in ROUTES:
        def fake(self, *a, _m=m, **k):
            st.calls.append(_m)
            return {"source": "lse"} if _m == "screener" else [LSE_ROW]
        monkeypatch.setattr(LseProvider, m, fake)
    return st


@pytest.fixture()
def client(tmp_path, monkeypatch, stub, lse):
    monkeypatch.setenv("LSE_TERMINAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("LSE_API_KEY", raising=False)
    eps = [SimpleNamespace(name="stub", load=lambda: stub)]
    monkeypatch.setattr(regmod, "entry_points",
                        lambda group: eps if group == "lse_terminal.providers" else [])
    return TestClient(create_app(), base_url="http://127.0.0.1")


@pytest.mark.parametrize("path,params,method,what,text", ROUTES, ids=IDS)
def test_explicit_stub_answers_with_its_rows(client, stub, path, params, method, what, text):
    r = client.get(path, params={**params, "provider": "stub"})
    assert r.status_code == 200, r.text
    assert r.headers["X-Source"] == "stub"
    assert stub.calls == {method: 1}
    want = {"screener": FIX["screener"], "macro_catalog": [MACRO_CAT_ROW],
            "macro_series": [MACRO_SERIES_ROW]}.get(method) or [FIX[method]]
    assert r.json() == want


@pytest.mark.parametrize("path,params,method,what,text", ROUTES, ids=IDS)
def test_default_source_is_lse_even_when_unconfigured(client, stub, path, params, method, what, text):
    r = client.get(path, params=params)
    assert r.status_code == 409
    assert r.json()["detail"] == text
    assert stub.total() == 0


@pytest.mark.parametrize("path,params,method,what,text", ROUTES, ids=IDS)
def test_default_source_is_lse_when_configured(client, stub, lse, path, params, method, what, text):
    lse.on = True
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    assert r.headers["X-Source"] == "lse"
    assert lse.calls == [method]
    assert stub.total() == 0


@pytest.mark.parametrize("path,params,method,what,text", ROUTES, ids=IDS)
def test_explicit_source_without_cap_is_404_no_fallback(client, stub, lse, path, params, method, what, text):
    lse.on = True
    r = client.get(path, params={**params, "provider": "demo"})
    assert r.status_code == 404
    assert stub.total() == 0 and lse.calls == []


@pytest.mark.parametrize("path,params,method,what,text", ROUTES, ids=IDS)
def test_explicit_unconfigured_stub_is_409(client, stub, path, params, method, what, text):
    stub.on = False
    r = client.get(path, params={**params, "provider": "stub"})
    assert r.status_code == 409
    assert r.json()["detail"] == "stub is not configured"
    assert stub.total() == 0


@pytest.mark.parametrize("path,params,method,what,text", ROUTES, ids=IDS)
def test_unknown_provider_is_404(client, path, params, method, what, text):
    r = client.get(path, params={**params, "provider": "nope"})
    assert r.status_code == 404


@pytest.mark.parametrize("path,params,method,what,text", ROUTES, ids=IDS)
def test_not_supported_is_404(client, stub, path, params, method, what, text):
    stub.fail = NotSupported("stub cannot today")
    r = client.get(path, params={**params, "provider": "stub"})
    assert r.status_code == 404
    assert r.json()["detail"] == "stub cannot today"


@pytest.mark.parametrize("path,params,method,what,text", ROUTES, ids=IDS)
def test_other_errors_are_502_with_prefix(client, stub, path, params, method, what, text):
    stub.fail = RuntimeError("[400] bad underlying")
    r = client.get(path, params={**params, "provider": "stub"})
    assert r.status_code == 502
    assert r.json()["detail"] == f"{what} failed: [400] bad underlying"


def test_logos_never_error(client, stub):
    for prov in ("nope", "demo"):
        r = client.get("/api/logos", params={"provider": prov})
        assert (r.status_code, r.json()) == (200, {})
    stub.fail = RuntimeError("cdn down")
    r = client.get("/api/logos", params={"provider": "stub"})
    assert (r.status_code, r.json()) == (200, {})


def test_logos_default_needs_no_key(client, stub, monkeypatch):
    seen = []
    monkeypatch.setattr(LseProvider, "logos", lambda self: seen.append(1) or {"A": {"light": "a", "dark": "a"}})
    r = client.get("/api/logos")
    assert r.status_code == 200 and r.json() == {"A": {"light": "a", "dark": "a"}}
    assert r.headers["X-Source"] == "lse"
    assert seen == [1] and stub.total() == 0


def test_chain_cache_is_per_provider(client, stub, lse):
    lse.on = True
    q = {"underlying": "X"}
    assert client.get("/api/options/chain", params={**q, "provider": "lse"}).json() == [LSE_ROW]
    r = client.get("/api/options/chain", params={**q, "provider": "stub"})
    assert r.json() == [FIX["option_chain"]] and stub.calls == {"option_chain": 1}
    client.get("/api/options/chain", params={**q, "provider": "stub"})
    assert stub.calls == {"option_chain": 1}


def test_sign_out_inside_ttl_is_409(client, lse):
    lse.on = True
    assert client.get("/api/macro/series", params={"symbol": "X"}).status_code == 200
    lse.on = False
    r = client.get("/api/macro/series", params={"symbol": "X"})
    assert (r.status_code, r.json()["detail"]) == (409, MACRO)


def test_catalog_and_series_agree_across_key_save(client, stub, lse):
    r = client.get("/api/macro/catalog")
    assert (r.status_code, r.json()["detail"]) == (409, MACRO)
    lse.on = True
    for path, q in (("/api/macro/series", {"symbol": "X"}), ("/api/macro/catalog", {})):
        r = client.get(path, params=q)
        assert r.status_code == 200 and r.headers["X-Source"] == "lse"
    assert stub.total() == 0


def test_providers_lists_stub_as_custom_with_every_capability(client):
    provs = {p["name"]: p for p in client.get("/api/providers").json()}
    assert provs["stub"]["custom"] is True
    assert set(provs["stub"]["capabilities"]) == {
        "search", "candles", "quote", "stream", "prices", "screener", "logos",
        "options", "flow", "calendar", "macro"}


def test_fit_info_lse_options_tracks_lse_key_only(client):
    assert client.get("/api/quant/fit-info").json()["lse_options"] is False


def test_grep_gate_reg_get_lse_only_in_lse_only():
    tree = ast.parse((ROOT / "lse_terminal/engine/server.py").read_text())
    bad = []

    def visit(node, fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node.name
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "reg" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "lse"
                and fn != "_lse_only"):
            bad.append((node.lineno, fn))
        for c in ast.iter_child_nodes(node):
            visit(c, fn)

    visit(tree, None)
    assert bad == []


def test_x_source_present_on_cache_hit(client, stub):
    for _ in range(2):
        r = client.get("/api/options/chain", params={"underlying": "X", "provider": "stub"})
        assert r.status_code == 200 and r.headers["X-Source"] == "stub"
    assert stub.calls == {"option_chain": 1}


def test_error_answers_carry_no_x_source(client, stub):
    stub.on = False
    r = client.get("/api/macro/catalog", params={"provider": "stub"})
    assert r.status_code == 409 and "X-Source" not in r.headers


@pytest.fixture()
def clock(client):
    """The cache's settable clock; every snapshot TTL reads it."""
    now = [1000.0]
    client.app.state.cache.clock = lambda: now[0]
    return now


@pytest.mark.parametrize("path,params,method,ttl", [
    ("/api/options/chain", {"underlying": "X"}, "option_chain", 15),
    ("/api/macro/series", {"symbol": "US.CPI"}, "macro_series", 600),
])
def test_snapshot_ttl_expiry(client, stub, clock, path, params, method, ttl):
    q = {**params, "provider": "stub"}
    client.get(path, params=q)
    clock[0] += ttl - 1
    client.get(path, params=q)
    assert stub.calls == {method: 1}
    clock[0] += 2
    client.get(path, params=q)
    assert stub.calls == {method: 2}


def test_flow_without_underlying_reaches_stub(client, stub):
    r = client.get("/api/options/flow", params={"provider": "stub"})
    assert r.status_code == 200 and r.json() == [FIX["options_flow"]]
    assert stub.calls == {"options_flow": 1}


def test_flow_without_underlying_default_source(client, lse):
    lse.on = True
    r = client.get("/api/options/flow")
    assert r.status_code == 200 and r.headers["X-Source"] == "lse"
    assert lse.calls == ["options_flow"]


def test_databank_unconfigured_is_409_with_options_hint(client):
    r = client.get("/api/lse/databank")
    assert (r.status_code, r.json()["detail"]) == (409, OPT)


def _app(tmp_path, monkeypatch, stub, **env):
    monkeypatch.setenv("LSE_TERMINAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("LSE_API_KEY", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    eps = [SimpleNamespace(name="stub", load=lambda: stub)]
    monkeypatch.setattr(regmod, "entry_points",
                        lambda group: eps if group == "lse_terminal.providers" else [])
    return TestClient(create_app(), base_url="http://127.0.0.1")


def test_cache_stats_and_clear(client, stub, tmp_path):
    q = {"underlying": "X", "provider": "stub"}
    client.get("/api/options/chain", params=q)
    r = client.get("/api/cache/stats")
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert "bars" in r.json() and "bytes" in r.json()
    assert (tmp_path / "cache.sqlite").exists()
    assert client.delete("/api/cache", params={"provider": "stub"}).status_code == 200
    client.get("/api/options/chain", params=q)
    assert stub.calls == {"option_chain": 2}


def test_cache_routes_denied_when_hosted(tmp_path, monkeypatch, stub):
    c = _app(tmp_path, monkeypatch, stub, LSE_TERMINAL_HOSTED="1")
    assert c.get("/api/cache/stats").status_code == 403
    assert c.delete("/api/cache").status_code == 403
    assert not (tmp_path / "cache.sqlite").exists()


def test_cache_off_has_no_file_and_ttl_holds(tmp_path, monkeypatch, stub):
    c = _app(tmp_path, monkeypatch, stub, LSE_TERMINAL_CACHE="off")
    q = {"underlying": "X", "provider": "stub"}
    c.get("/api/options/chain", params=q)
    c.get("/api/options/chain", params=q)
    assert stub.calls == {"option_chain": 1}
    assert not (tmp_path / "cache.sqlite").exists()
    assert c.get("/api/cache/stats").json() == {"enabled": False}


def test_unopenable_store_fails_open(tmp_path, monkeypatch, stub):
    (tmp_path / "cache.sqlite").mkdir()
    c = _app(tmp_path, monkeypatch, stub)
    assert c.get("/api/cache/stats").json()["enabled"] is False
    q = {"underlying": "X", "provider": "stub"}
    c.get("/api/options/chain", params=q)
    c.get("/api/options/chain", params=q)
    assert stub.calls == {"option_chain": 1}
