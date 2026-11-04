# Data Source Contract (PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift the LSE-only data surfaces into the `Provider` contract, add the shared timeframe table and bound parser, the candle contract clauses, plugin ordering and defaults, and a harness that checks all of it. No route or UI change.

**Architecture:** The contract module (`lse_terminal/contracts/`) owns every shape: provider attributes, optional methods, row `TypedDict`s, `TIMEFRAME_SECONDS`, `to_epoch`. Providers import from it. The registry owns plugin order and the `is_custom` default. The harness (`lse_terminal/testing.py`) reads the contract and checks a provider against it. Data flows one way: contract → providers → harness. Nothing in `server.py` changes in this PR.

**Tech Stack:** Python 3.10+, pandas, pytest, stdlib `typing.TypedDict`, `zoneinfo`-free UTC parsing via `datetime`.

**Spec:** `openspec/changes/data-source-contract-cache/` — design D1, D3, D4; spec `data-provider-contract`; tasks 1.1 to 1.8.

**One behaviour change, intended:** `DemoProvider.candles` filters `ts <= end` today. The contract says `end` is exclusive. Task 4 changes demo to `ts < end`. No route sends demo an `end` that lands exactly on a bar boundary except the windowed backtest load, which pads `end` by seven days, so no visible change.

---

## File map

| File | Responsibility | Change |
|---|---|---|
| `lse_terminal/contracts/types.py` | Shared data shapes | Add `TIMEFRAME_SECONDS`, `to_epoch`, row `TypedDict`s |
| `lse_terminal/contracts/provider.py` | Provider contract | Add attributes, nine optional methods, capability names, candle clauses |
| `lse_terminal/contracts/__init__.py` | Public exports | Export the new names |
| `lse_terminal/providers/demo.py` | Synthetic source | Use shared table plus `tick`, exclusive `end`, `cacheable = False` |
| `lse_terminal/providers/userdata.py` | Local files | Use shared table, `cacheable = False` |
| `lse_terminal/engine/broker_hub.py` | Broker as source | `cacheable = False` |
| `lse_terminal/engine/registry.py` | Discovery | Sort plugins by name, default `is_custom` |
| `lse_terminal/testing.py` | Compliance harness | Capability checks, candle clause checks |
| `tests/fixtures/lse_rows.json` | Recorded LSE rows | New |
| `tests/test_contract.py` | Contract tests | New |
| `tests/test_registry.py` | Plugin order tests | New |

---

### Task 0: Environment

**Files:** none.

- [ ] **Step 1: Create the venv and install in editable mode**

Run from the worktree root:

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -e ../../../../brue -e ../../../../brue-connect -e ".[dev]"
```

If `../../../../brue` does not exist, clone it next to `lse-terminal` as the README says, then rerun.

- [ ] **Step 2: Run the existing suite as the baseline**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass. Record the count. Every later task must keep it green.

---

### Task 1: Shared timeframe table and bound parser

**Files:**
- Modify: `lse_terminal/contracts/types.py`
- Modify: `lse_terminal/contracts/__init__.py`
- Test: `tests/test_contract.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_contract.py`:

```python
import pytest

from lse_terminal.contracts import TIMEFRAME_SECONDS, to_epoch


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
])
def test_to_epoch_shapes(value, expected):
    assert to_epoch(value) == expected


def test_to_epoch_none_and_empty():
    assert to_epoch(None) is None
    assert to_epoch("") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_contract.py -v`
Expected: FAIL, `ImportError: cannot import name 'TIMEFRAME_SECONDS'`.

- [ ] **Step 3: Implement**

Append to `lse_terminal/contracts/types.py`:

```python
from datetime import datetime, timezone

# Bar length per fixed-length timeframe, in UI display order. "tick" is not
# here: a tick has no length, and the cache treats any label outside this
# table as not cacheable. Providers that serve tick add it themselves.
TIMEFRAME_SECONDS: dict[str, int] = {
    "1s": 1, "30s": 30,
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}


def to_epoch(bound) -> int | None:
    """A start/end bound as integer epoch seconds UTC.

    Accepts an int/float, a digit string, a date-only string, or an ISO
    string with or without an offset ("Z" included). A naive value is UTC.
    """
    if bound is None or bound == "":
        return None
    if isinstance(bound, (int, float)):
        return int(bound)
    s = str(bound).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())
```

Move the `from datetime import ...` line to the top of the file with the other imports.

In `lse_terminal/contracts/__init__.py`, add `TIMEFRAME_SECONDS` and `to_epoch` to the import from `types` and to `__all__`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_contract.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add lse_terminal/contracts tests/test_contract.py
git commit -m "feat(contract): shared TIMEFRAME_SECONDS and to_epoch" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Record the LSE fixture

**Files:**
- Create: `tests/fixtures/lse_rows.json`
- Create: `tools/record_lse_fixture.py`

This task needs a real LSE key in `~/.config/lse-terminal/config.json` or `LSE_API_KEY`. The key goes only to api.londonstrategicedge.com, as the app already does. The fixture holds market data rows, no key.

- [ ] **Step 1: Write the recorder**

Create `tools/record_lse_fixture.py`:

```python
"""Record one row per LSE data surface into tests/fixtures/lse_rows.json.

Run once by hand with a real key. The contract's required row keys are
derived from this file, so re-record when the LSE feed adds or drops a key.
"""
import json
import sys
from pathlib import Path

from lse_terminal.providers import LseProvider


def first(rows):
    return rows[0] if rows else None


def main() -> int:
    p = LseProvider()
    if not p.configured():
        print("no LSE key set", file=sys.stderr)
        return 1
    und = first(p.options_underlyings())
    sym = und["symbol"] if und else "SPY"
    cat = p.macro_catalog()
    out = {
        "prices": first(p.prices(["AAPL"])),
        "screener": {k: (v[:1] if isinstance(v, list) else v)
                     for k, v in p.screener().items()},
        "logos": dict(list(p.logos().items())[:1]),
        "options_underlyings": und,
        "option_chain": first(p.option_chain(sym, limit=5)),
        "options_flow": first(p.options_flow(limit=5)),
        "economic_calendar": first(p.economic_calendar(limit=5)),
        "macro_catalog": first(cat),
        "macro_series": first(p.macro_series(cat[0]["symbol"], limit=5)) if cat else None,
    }
    path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "lse_rows.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/python tools/record_lse_fixture.py`
Expected: `wrote .../tests/fixtures/lse_rows.json`. Open the file. Every key except possibly `options_flow` outside US market hours holds a non-null value. If a value is null, rerun during market hours or record that surface by hand from the LSE docs, and note it in the commit message.

- [ ] **Step 3: Commit**

```bash
git add tools/record_lse_fixture.py tests/fixtures/lse_rows.json
git commit -m "test: record one LSE row per data surface" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Row TypedDicts

**Files:**
- Modify: `lse_terminal/contracts/types.py`
- Modify: `lse_terminal/contracts/__init__.py`
- Test: `tests/test_contract.py`

Required keys are the keys the UI or engine reads, verified in `app.js` and `EconomicCalendar.tsx`, intersected with the fixture. The lists below are the verified UI reads. Step 3 removes any key the fixture lacks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contract.py`:

```python
import json
from pathlib import Path

from lse_terminal.contracts import ROW_TYPES

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "lse_rows.json").read_text())


@pytest.mark.parametrize("method", sorted(ROW_TYPES))
def test_fixture_row_has_required_keys(method):
    row = FIXTURE[method]
    if row is None:
        pytest.skip(f"fixture has no {method} row")
    missing = ROW_TYPES[method].__required_keys__ - set(row)
    assert not missing, f"{method} fixture lacks {sorted(missing)}"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_contract.py -k fixture -v`
Expected: FAIL, `ImportError: cannot import name 'ROW_TYPES'`.

- [ ] **Step 3: Implement**

Append to `lse_terminal/contracts/types.py`:

```python
from typing import TypedDict


class PriceRow(TypedDict, total=False):
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    ts: float


class UnderlyingRow(TypedDict, total=False):
    symbol: str
    name: str


class ChainRow(TypedDict, total=False):
    ticker: str
    contract_type: str      # "call" | "put"
    strike: float
    expiry: str             # YYYY-MM-DD
    last_price: float
    underlying_price: float
    iv: float
    volume_today: float
    premium_today: float


class FlowRow(TypedDict, total=False):
    ticker: str
    contract_type: str
    strike: float
    expiry: str
    premium: float
    iv: float


class CalendarRow(TypedDict, total=False):
    datetime: str
    region_code: str
    event: str
    actual: str | None
    consensus: str | None
    forecast: str | None


class MacroCatalogRow(TypedDict, total=False):
    symbol: str
    name: str


class MacroSeriesRow(TypedDict, total=False):
    date: str
    value: float


# Required keys per surface. total=False above lets a row carry extras;
# __required_keys__ below is what the harness checks.
for _cls, _req in (
    (PriceRow, {"symbol", "price"}),
    (UnderlyingRow, {"symbol"}),
    (ChainRow, {"contract_type", "strike", "expiry", "last_price"}),
    (FlowRow, {"contract_type", "strike", "expiry", "premium"}),
    (CalendarRow, {"datetime", "event"}),
    (MacroCatalogRow, {"symbol", "name"}),
    (MacroSeriesRow, {"date", "value"}),
):
    _cls.__required_keys__ = frozenset(_req)
    _cls.__optional_keys__ = frozenset(_cls.__annotations__) - _cls.__required_keys__

ROW_TYPES = {
    "prices": PriceRow,
    "options_underlyings": UnderlyingRow,
    "option_chain": ChainRow,
    "options_flow": FlowRow,
    "economic_calendar": CalendarRow,
    "macro_catalog": MacroCatalogRow,
    "macro_series": MacroSeriesRow,
}
```

Move `from typing import TypedDict` to the top imports. Export `ROW_TYPES` and the seven classes from `contracts/__init__.py`.

`total=False` plus an explicit required set is used on purpose: Python 3.10 has no `Required[]`, and the project supports 3.10.

Run the test. For each failure, open the fixture. If the fixture names the field differently (for example `type` instead of `contract_type`), check `app.js` for which name it reads. The UI read wins only if the fixture also carries it; otherwise drop the key from the required set and leave a one-line comment naming the fixture field.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_contract.py -v`
Expected: PASS. Skips allowed only for a null fixture row.

- [ ] **Step 5: Commit**

```bash
git add lse_terminal/contracts tests/test_contract.py
git commit -m "feat(contract): row TypedDicts from the LSE fixture and UI reads" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Providers use the shared table

**Files:**
- Modify: `lse_terminal/providers/demo.py:21-27, 59, 88, 120-128`
- Modify: `lse_terminal/providers/userdata.py:26-35, 184, 574, 604-605`
- Test: `tests/test_demo_provider.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_demo_provider.py`:

```python
def test_demo_keeps_tick():
    assert "tick" in DemoProvider().timeframes


def test_demo_end_is_exclusive():
    p = DemoProvider()
    df = p.candles("DEMO:SPX", "1h", limit=10)
    last = int(df["ts"].iloc[-1])
    out = p.candles("DEMO:SPX", "1h", limit=10, start=str(last - 3600), end=str(last))
    assert last not in set(out["ts"])
    assert last - 3600 in set(out["ts"])
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_demo_provider.py -v`
Expected: `test_demo_end_is_exclusive` FAILS (bar at `end` present). `test_demo_keeps_tick` passes today and guards the refactor.

Note: `pd.Timestamp("1700000000", tz="UTC")` does not parse a digit string as epoch; demo's current filter would raise on this input. That failure is also expected and is fixed by switching to `to_epoch`.

- [ ] **Step 3: Implement**

In `demo.py`, replace the `_TF_SECONDS = {...}` block (lines 21-27) with:

```python
from lse_terminal.contracts import TIMEFRAME_SECONDS, to_epoch

# Demo serves "tick" as a one-second walk (trades print about once a second
# here); the shared table has no tick, so demo adds it.
_TF_SECONDS = {"tick": 1, **TIMEFRAME_SECONDS}
```

Keep `tick` first so `timeframes = list(_TF_SECONDS)` keeps its current order. Replace the two filter lines in `candles`:

```python
        s, e = to_epoch(start), to_epoch(end)
        if s is not None:
            df = df[df["ts"] >= s]
        if e is not None:
            df = df[df["ts"] < e]
```

Keep `df = df.head(n) if start else df.tail(n)` unchanged.

Add `cacheable = False` to `DemoProvider` next to `deterministic = True`.

In `userdata.py`, replace the `_TF_SECONDS = {...}` block with:

```python
from lse_terminal.contracts import TIMEFRAME_SECONDS

# Imported files are OHLC bars; there is no honest way to split a bar back
# into trades, so userdata serves the shared fixed-length table, no tick.
_TF_SECONDS = TIMEFRAME_SECONDS
```

Add `cacheable = False` to `UserDataProvider`.

In `lse_terminal/engine/broker_hub.py`, add `cacheable = False` below `is_broker = True` (line 1216).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: baseline count plus new tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add lse_terminal/providers lse_terminal/engine/broker_hub.py tests/test_demo_provider.py
git commit -m "refactor(providers): shared timeframe table, exclusive end in demo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Contract attributes, optional methods, capabilities

**Files:**
- Modify: `lse_terminal/contracts/provider.py`
- Test: `tests/test_contract.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contract.py`:

```python
import pandas as pd

from lse_terminal.contracts import NotSupported, Provider
from lse_terminal.providers import DemoProvider, LseProvider, UserDataProvider

LIFTED = ["prices", "screener", "logos", "options_underlyings", "option_chain",
          "options_flow", "economic_calendar", "macro_catalog", "macro_series"]


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
    assert Bare.snapshot_ttl == {}


@pytest.mark.parametrize("method", LIFTED)
def test_default_raises_not_supported(method):
    with pytest.raises(NotSupported):
        getattr(Bare(), method)(*([["X"]] if method == "prices" else
                                  ["X"] if method in ("option_chain", "macro_series") else []))


def test_bare_capabilities():
    assert Bare().capabilities() == {"search", "candles"}


def test_partial_options_is_not_options():
    class Half(Bare):
        def option_chain(self, underlying, type=None, expiry=None, limit=5000):
            return []
    assert "options" not in Half().capabilities()


def test_full_surface_capabilities():
    class Full(Bare):
        def quote(self, symbol): ...
        async def stream(self, symbols): yield {}
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
    # demo must never win a global-route fallback over LSE
    lifted = {"prices", "screener", "logos", "options", "flow", "calendar", "macro"}
    assert not (DemoProvider().capabilities() & lifted)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_contract.py -v`
Expected: FAIL on `Bare.is_custom` (`AttributeError`) and on the lifted methods.

- [ ] **Step 3: Implement**

In `lse_terminal/contracts/provider.py`, add to the class body after `deterministic`:

```python
    # User-owned source (a plugin, a custom key). Exempt from the fleet
    # directory gate. load_plugins sets it for every entry-point provider.
    is_custom: bool = False
    # A live execution venue. The UI keeps research off it.
    is_broker: bool = False
    # False for sources that are already local or synthetic: the registry
    # never puts the read-through cache in front of them.
    cacheable: bool = True
    # Seconds after a bar's close before the source publishes it final.
    # The cache treats a bar as closed only after this delay.
    settle_seconds: int = 60
    # Per-method snapshot TTL overrides in seconds, e.g. {"option_chain": 60}.
    snapshot_ttl: dict = {}
```

Replace the `candles` docstring with:

```python
        """OHLCV history as a DataFrame with exactly ``CANDLE_COLUMNS``.

        Contract (the cache depends on every clause):
        - ``start`` is inclusive, ``end`` is exclusive: a bar is returned only
          when ``start <= ts < end``. Both accept anything ``to_epoch`` does.
        - ``ts`` ascending, no duplicates.
        - With ``start``: the OLDEST ``limit`` bars from ``start``.
          Without ``start``: the NEWEST ``limit`` bars (before ``end`` if given).
        - Complete: fewer than ``limit`` rows means every bar the source holds
          in the range is present.
        - Contiguous: no bar the source holds between the first and last
          ``ts`` of the answer is left out.
        - Raise on failure. An empty frame means "no bars in this range",
          never "the call failed".
        """
```

Add after `stream`, each with its docstring naming the row type:

```python
    def prices(self, symbols: list[str]) -> list[dict]:
        """Latest price per symbol. Rows: ``PriceRow``."""
        raise NotSupported(f"{self.name} has no price board")

    def screener(self) -> dict:
        """Cross-sectional snapshot ``{"columns": [...], "rows": [...]}``."""
        raise NotSupported(f"{self.name} has no screener")

    def logos(self) -> dict:
        """Symbol -> ``{"light": url, "dark": url}``."""
        raise NotSupported(f"{self.name} has no logos")

    def options_underlyings(self) -> list[dict]:
        """Optionable underlyings. Rows: ``UnderlyingRow``."""
        raise NotSupported(f"{self.name} has no options")

    def option_chain(self, underlying: str, type: str | None = None,
                     expiry: str | None = None, limit: int = 5000) -> list[dict]:
        """Current chain for one underlying. Rows: ``ChainRow``."""
        raise NotSupported(f"{self.name} has no options")

    def options_flow(self, underlying: str | None = None, type: str | None = None,
                     min_premium: float | None = None, limit: int = 300) -> list[dict]:
        """Recent option prints, newest first. Rows: ``FlowRow``."""
        raise NotSupported(f"{self.name} has no options flow")

    def economic_calendar(self, region=None, event: str | None = None,
                          start: str | None = None, end: str | None = None,
                          released_only: bool = False, order: str = "asc",
                          limit: int = 5000) -> list[dict]:
        """Macro release events. Rows: ``CalendarRow``."""
        raise NotSupported(f"{self.name} has no economic calendar")

    def macro_catalog(self) -> list[dict]:
        """Every macro series the source holds. Rows: ``MacroCatalogRow``."""
        raise NotSupported(f"{self.name} has no macro data")

    def macro_series(self, symbol: str, dataset: str | None = None,
                     start: str | None = None, end: str | None = None,
                     order: str = "asc", limit: int = 5000) -> list[dict]:
        """One macro series. Rows: ``MacroSeriesRow``."""
        raise NotSupported(f"{self.name} has no macro data")
```

Add a docstring line to `configured`: `"Must do no I/O: the engine calls it on every request."`

Replace `capabilities`:

```python
    # Capability name -> methods that must ALL be overridden to claim it.
    _CAPABILITY_METHODS = {
        "quote": ("quote",),
        "stream": ("stream",),
        "prices": ("prices",),
        "screener": ("screener",),
        "logos": ("logos",),
        "options": ("options_underlyings", "option_chain"),
        "flow": ("options_flow",),
        "calendar": ("economic_calendar",),
        "macro": ("macro_catalog", "macro_series"),
    }

    def capabilities(self) -> set[str]:
        """Which optional surfaces this provider actually implements."""
        t = type(self)
        caps = {"search", "candles"}
        for cap, methods in self._CAPABILITY_METHODS.items():
            if all(getattr(t, m) is not getattr(Provider, m) for m in methods):
                caps.add(cap)
        return caps
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS. `test_providers_listing` in `test_api.py` still sees `stream` in demo's capabilities.

- [ ] **Step 5: Commit**

```bash
git add lse_terminal/contracts/provider.py tests/test_contract.py
git commit -m "feat(contract): optional data surfaces, attributes, candle clauses" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Plugin order and is_custom default

**Files:**
- Modify: `lse_terminal/engine/registry.py:85-94`
- Test: `tests/test_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry.py`:

```python
from types import SimpleNamespace

import pandas as pd

from lse_terminal.contracts import Provider
from lse_terminal.engine import registry as regmod


def _prov(n):
    class P(Provider):
        name = n

        def search(self, query="", limit=50):
            return []

        def candles(self, symbol, timeframe, limit=500, start=None, end=None):
            return pd.DataFrame()
    return P


def test_plugins_load_sorted_and_custom(monkeypatch):
    eps = [SimpleNamespace(name="zeta", load=lambda: _prov("zeta")),
           SimpleNamespace(name="alpha", load=lambda: _prov("alpha"))]
    monkeypatch.setattr(regmod, "entry_points",
                        lambda group: eps if group == "lse_terminal.providers" else [])
    reg = regmod.Registry()
    regmod.load_plugins(reg)
    assert [p.name for p in reg.all()] == ["alpha", "zeta"]
    assert all(p.is_custom for p in reg.all())


def test_explicit_is_custom_false_is_overridden(monkeypatch):
    # A plugin cannot opt back into the directory gate: entry-point
    # providers are user-installed by definition.
    eps = [SimpleNamespace(name="x", load=lambda: _prov("x"))]
    monkeypatch.setattr(regmod, "entry_points",
                        lambda group: eps if group == "lse_terminal.providers" else [])
    reg = regmod.Registry()
    regmod.load_plugins(reg)
    assert reg.get("x").is_custom is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_registry.py -v`
Expected: FAIL, order is `["zeta", "alpha"]` and `is_custom` is `False`.

- [ ] **Step 3: Implement**

In `load_plugins`, replace the provider loop head and body:

```python
    for ep in sorted(entry_points(group="lse_terminal.providers"),
                     key=lambda e: e.name):
        try:
            obj = ep.load()
            provider = obj() if inspect.isclass(obj) else obj
            # Installed by the user, so never hidden by the fleet directory.
            provider.is_custom = True
            reg.register(provider)
```

Keep the `except` block unchanged. Sort the indicator and engine loops the same way, for the same reproducibility reason.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lse_terminal/engine/registry.py tests/test_registry.py
git commit -m "feat(registry): sort plugins by name, mark entry-point providers custom" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Harness checks capabilities and candle clauses

**Files:**
- Modify: `lse_terminal/testing.py`
- Test: `tests/test_contract.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contract.py`:

```python
from lse_terminal.testing import check_provider


def test_demo_passes_full_harness():
    assert check_provider(DemoProvider()) == []


def test_claimed_capability_that_raises_is_a_violation():
    class Liar(DemoProvider):
        def screener(self):
            raise NotSupported("no")
    problems = check_provider(Liar())
    assert any("screener" in p for p in problems)


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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_contract.py -k "harness or violation" -v`
Expected: the three violation tests FAIL (harness reports nothing).

- [ ] **Step 3: Implement**

In `lse_terminal/testing.py`, add imports:

```python
from lse_terminal.contracts import (CANDLE_COLUMNS, ROW_TYPES, Instrument,
                                    NotSupported, Provider, to_epoch)
```

Replace the final `return problems` of `check_provider` with:

```python
    problems += _check_candle_clauses(provider, sym, tf, df)
    problems += _check_capabilities(provider, sym)
    return problems
```

Add below `check_provider`:

```python
def _check_candle_clauses(provider: Provider, sym: str, tf: str, df) -> list[str]:
    """Half-open bounds and prefix completeness, the clauses the cache needs."""
    out: list[str] = []
    if len(df) < 3:
        return out
    ts = [int(t) for t in df["ts"]]
    s, e = ts[0], ts[2]
    try:
        win = provider.candles(sym, tf, limit=50, start=str(s), end=str(e))
    except Exception as ex:
        return [f"windowed candles raised: {ex}"]
    got = set(int(t) for t in win["ts"])
    if e in got:
        out.append("end is exclusive: a bar at ts == end was returned")
    if s not in got:
        out.append("start is inclusive: the bar at ts == start is missing")
    try:
        small = provider.candles(sym, tf, limit=2, start=str(s))
        big = provider.candles(sym, tf, limit=10, start=str(s))
    except Exception as ex:
        return out + [f"windowed candles raised: {ex}"]
    if list(small["ts"]) != list(big["ts"])[:len(small)]:
        out.append("windowed answer with a smaller limit is not a prefix "
                   "of the same call with a larger limit")
    return out


# Arguments for one probe call per method; the first underlying/series
# symbol is filled in from the provider's own listing.
def _probe(provider: Provider, method: str, sym: str):
    if method == "prices":
        return provider.prices([sym])
    if method == "option_chain":
        und = provider.options_underlyings()
        return provider.option_chain(und[0]["symbol"] if und else sym, limit=5)
    if method == "macro_series":
        cat = provider.macro_catalog()
        return provider.macro_series(cat[0]["symbol"] if cat else sym, limit=5)
    if method in ("options_flow", "economic_calendar"):
        return getattr(provider, method)(limit=5)
    return getattr(provider, method)()


_METHODS_BY_CAP = {cap: ms for cap, ms in Provider._CAPABILITY_METHODS.items()
                   if cap not in ("quote", "stream")}


def _check_capabilities(provider: Provider, sym: str) -> list[str]:
    out: list[str] = []
    caps = provider.capabilities()
    for cap, methods in _METHODS_BY_CAP.items():
        if cap not in caps:
            continue
        for m in methods:
            try:
                res = _probe(provider, m, sym)
            except NotSupported:
                out.append(f"{m}: claimed via capability {cap!r} but raised NotSupported")
                continue
            except Exception as ex:
                out.append(f"{m}: raised {type(ex).__name__}: {ex}")
                continue
            if m == "screener":
                if not isinstance(res, dict) or not {"columns", "rows"} <= set(res):
                    out.append("screener: must return {columns, rows}")
                continue
            if m == "logos":
                if not isinstance(res, dict):
                    out.append("logos: must return a symbol -> {light, dark} map")
                continue
            if not isinstance(res, list):
                out.append(f"{m}: must return a list of rows")
                continue
            if m in ("options_flow", "economic_calendar", "option_chain",
                     "macro_series") and len(res) > 5:
                out.append(f"{m}: limit=5 not respected: got {len(res)} rows")
            req = ROW_TYPES[m].__required_keys__
            for row in res[:5]:
                missing = req - set(row)
                if missing:
                    out.append(f"{m}: row lacks required keys {sorted(missing)}")
                    break
    return out
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS, including `test_demo_passes_compliance_harness` in `test_demo_provider.py`.

If `test_fixture_rows_pass_the_harness` fails on `screener` because the fixture's screener keys differ from `{columns, rows}`, read the fixture, set the check to the fixture's two top-level keys, and update the `screener` docstring in `provider.py` to match.

- [ ] **Step 5: Commit**

```bash
git add lse_terminal/testing.py tests/test_contract.py
git commit -m "feat(testing): harness checks claimed capabilities and candle clauses" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Close PR 1

**Files:**
- Modify: `openspec/changes/data-source-contract-cache/tasks.md`

- [ ] **Step 1: Full suite and lint**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

Run: `.venv/bin/ruff check lse_terminal tests tools`
Expected: no new findings in touched files. If `ruff` is not installed, run `.venv/bin/pip install ruff` first.

- [ ] **Step 2: Grep gate: no private timeframe table left**

Run: `grep -n '"1s": 1' lse_terminal/providers/*.py`
Expected: no output.

- [ ] **Step 3: Tick tasks 1.1 to 1.8 in `tasks.md`**

Change `- [ ] 1.x` to `- [x] 1.x` for 1.1 through 1.8.

Run: `openspec validate data-source-contract-cache`
Expected: `Change 'data-source-contract-cache' is valid`.

- [ ] **Step 4: Commit**

```bash
git add openspec/changes/data-source-contract-cache/tasks.md
git commit -m "chore(openspec): tick PR 1 tasks" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Pre-merge review**

Dispatch a Claude review subagent on Opus (cross-module contract change) over `git diff main...HEAD`, with the spec `data-provider-contract` and design D1, D3, D4 as intent. Fold each finding or dismiss it with a reason before merge.
