# Market-data cache (PR 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The engine reads closed candles from a local SQLite file before it asks the source. The engine serves the seven snapshot methods from one TTL cache in every mode.

**Architecture:** The registry wraps each `cacheable` provider in `CachedProvider`, a plain delegating class. The registry owns one `CandleCache` that holds the candle store, the snapshot store, the per-key locks and the clock. The candle store is a `SqliteStore` when the cache is on and the engine is local. It is `None` when the engine is hosted or `LSE_TERMINAL_CACHE=off`, and then candles pass through. The snapshot store is the same `SqliteStore` when there is one, else a bounded `MemorySnapshots`. The fill rules are design D7, checked by `design.tla`.

**Tech stack:** Python 3.10+, stdlib `sqlite3` and `threading`, pandas, FastAPI, pytest.

**Spec:** `openspec/changes/data-source-contract-cache/` — design D5 to D8, `specs/market-data-cache/spec.md`, tasks 3.1 to 3.9, `design-review.md` (counter-examples), `design.tla`.

**Branch:** `feat/pr3-cache` in worktree `../lse-terminal-pr3`, cut from `develop`. Merge back into `develop`, not `main`.

**Test command:** `$VENV/bin/python -m pytest -q` from the worktree root, where `VENV=/private/tmp/claude-501/-Users-ahmetbilgili-projects-lse-terminal/3205484b-eafa-48d7-baa4-e42c8991f837/scratchpad/venv` (the system `python3` has no pandas or pytest). Baseline: 302 passed, 3 skipped, 2 known failures (`test_broker_list_names_each_broker_from_its_own_handshake`, `test_probe_does_not_open_a_session`; both need the uncloned `brue-connect` adapter). Lint: `$VENV/bin/ruff check <changed files>`; report new findings only.

---

## Design amendments (Task 1 writes them into the OpenSpec change)

These close gaps between D5 to D8 and task 3.9. Each one names the discarded alternative.

1. **Always wrap, two backends.** `Registry.register` wraps every provider with `cacheable = True`. `CandleCache.store` is a `SqliteStore` only when the engine is local and `LSE_TERMINAL_CACHE` is not `off`; otherwise it is `None` and `candles` passes through. `CandleCache.snapshots` is that same `SqliteStore`, or a `MemorySnapshots` when `store` is `None`. One wrapper owns the snapshot TTL in every mode, so the PR 2 route helper `_snap` goes (task 3.9). Discarded: keep `_snap` for hosted and off mode beside the wrapper. That is two TTL mechanisms for one rule.
2. **Store interface.** `SqliteStore` exposes `read`, `coverage`, `write_page`, `clear`, `keys`, `snapshot_get`, `snapshot_put`, `snapshot_clear`, `stats`. `write_page` is one transaction: bars first, then the merged coverage range. It replaces the separate `upsert` and `add_coverage` of D6, because the spec requires both writes of one page in one transaction. Gap and run arithmetic are pure functions in `cache.py` over `coverage()`, so they need no database to test.
3. **D7 clarifications.** These do not change the model's rules; each states what the model already does.
   - The live tail starts at `max(edge, ws)`, so a window that opens inside the forming area gets no bars before `ws`.
   - A windowed call appends the tail only when the covered run that starts at `ws` reaches `wc`. A run that stops early (the paging stopped at `limit`) already holds `limit` bars, and `head(limit)` would drop the tail.
   - The paging stop counts the stored bars in `[ws, g)`, not only the bars of this call.
   - A limit-only miss serves the source's newest-N answer as it is. That answer is the unwrapped answer at this instant (I3), so a second tail call adds nothing. Only its bars with `ts < edge` are stored, and coverage `[first_ts, edge)` is recorded only when `first_ts < edge`.
   - A limit-only hit serves the newest `limit` bars of (run bars + tail).
4. **Global clear.** `clear()` clears each key under that key's lock, one key at a time, in sorted order. No call holds two key locks. Discarded: hold every lock at once. It adds a deadlock surface and gives nothing, because a fill after its key's clear refills correctly.
5. **Snapshot clear scope.** `DELETE /api/cache` drops snapshot rows only when no `symbol` and no `timeframe` filter is given, for the named provider or for all. Snapshot TTLs are 15 s to 1 h, so a symbol-level snapshot clear has no use.
6. **Stale risk.** The risk "Symbol-scoped routes return 400 without a provider" is obsolete since the PR 2 amendment to D2; delete it.

---

## File map

| File | Responsibility |
|---|---|
| `lse_terminal/engine/cache_store.py` (new) | `SqliteStore` (bars, coverage, snapshots tables; per-thread connections), `MemorySnapshots` |
| `lse_terminal/engine/cache.py` (new) | pure range helpers, `CandleCache` (stores, locks, clock, clear), `CachedProvider` (D7 rules, D8 snapshots) |
| `lse_terminal/engine/registry.py` | `Registry(cache=None)`; `register` wraps |
| `lse_terminal/engine/server.py` | `create_app` builds the cache; routes lose `_snap`; `DELETE /api/cache`, `GET /api/cache/stats` |
| `lse_terminal/cli.py` | `lset cache clear|stats` |
| `README.md` | one cache paragraph |
| `tests/test_cache_store.py` (new) | store tests |
| `tests/test_cache.py` (new) | rule tests, counter-example tests, snapshot tests |
| `tests/test_routing.py` | TTL tests move from the route helper to the wrapper |

---

### Task 1: Design amendments and model gate

**Files:** `openspec/changes/data-source-contract-cache/design.md`, `specs/market-data-cache/spec.md`, `tasks.md`.

- [ ] **1.1** Write amendments 1 to 6 above into D5, D6, D7, D8 and "Risks". Use STE. Keep the existing rules; add the clarifications under D7 as "Clarified in PR 3".
- [ ] **1.2** In the spec, change the "Cache off" scenario: **WHEN** `LSE_TERMINAL_CACHE=off` and the registry registers `LseProvider`, **THEN** `candles` reaches the unwrapped provider unchanged, no cache file is created, and the seven snapshot methods use an in-memory TTL. Add the same scenario for hosted mode. Change the "Per-key lock" requirement text "A global clear SHALL take every lock in sorted key order" to "A global clear SHALL clear each key under its own lock, in sorted key order".
- [ ] **1.3** Run the gate: `cd openspec/changes/data-source-contract-cache && tlc design.tla -config design.cfg` (expect "No error has been found") and `tlc design.tla -config design.cx_tail.cfg` (expect "is violated"). The other `cx_*` runs are recorded in `design-review.md`; D7's rules did not change.
- [ ] **1.4** `openspec validate data-source-contract-cache`. Commit.

### Task 2: `SqliteStore` and `MemorySnapshots`

**Files:** create `lse_terminal/engine/cache_store.py`, `tests/test_cache_store.py`.

A key is a tuple `(provider, symbol, timeframe)`. Coverage rows are half-open `[start, end)`. `empty_until` is `None` for a range that came from an answer with bars.

- [ ] **2.1 Write the failing tests** in `tests/test_cache_store.py`:

```python
import threading
import time

import pandas as pd

from lse_terminal.contracts import CANDLE_COLUMNS
from lse_terminal.engine.cache_store import MemorySnapshots, SqliteStore

K = ("p", "SYM", "1m")


def _df(ts):
    return pd.DataFrame([(t, 1.0, 2.0, 0.5, 1.5, 10.0) for t in ts], columns=CANDLE_COLUMNS)


def test_write_page_then_read_is_ascending_and_half_open(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([180, 60, 120]), 60, 240, None, now=1000)
    got = st.read(K, 60, 180)
    assert list(got["ts"]) == [60, 120]
    assert list(got.columns) == CANDLE_COLUMNS


def test_adjacent_ranges_merge(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([0]), 0, 100, None, now=1000)
    st.write_page(K, _df([100]), 100, 200, None, now=1000)
    assert st.coverage(K, now=1000) == [(0, 200, None)]


def test_different_empty_until_do_not_merge_and_expired_drop_out(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([0]), 0, 100, None, now=1000)
    st.write_page(K, _df([]), 100, 200, 4600, now=1000)
    assert st.coverage(K, now=1000) == [(0, 100, None), (100, 200, 4600)]
    assert st.coverage(K, now=4600) == [(0, 100, None)]


def test_upsert_replaces_a_bar(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([60]), 60, 120, None, now=1000)
    d = _df([60]); d.loc[0, "close"] = 9.0
    st.write_page(K, d, 60, 120, None, now=1000)
    assert st.read(K, 0, 999)["close"].tolist() == [9.0]


def test_clear_and_keys(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.write_page(K, _df([60]), 60, 120, None, now=1000)
    st.write_page(("q", "X", "1d"), _df([0]), 0, 86400, None, now=1000)
    assert sorted(st.keys()) == [K, ("q", "X", "1d")]
    st.clear(K)
    assert st.read(K, 0, 999).empty and st.coverage(K, now=1000) == []
    assert st.keys() == [("q", "X", "1d")]


def test_snapshot_round_trip_ttl_and_prune(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.snapshot_put('["p","m"]', [{"a": 1, "x": float("nan")}], now=100, ttl=15)
    hit, body = st.snapshot_get('["p","m"]', now=114)
    assert hit and body[0]["a"] == 1 and body[0]["x"] != body[0]["x"]  # NaN survives
    assert st.snapshot_get('["p","m"]', now=115) == (False, None)
    st.snapshot_put('["p","n"]', [], now=200, ttl=15)  # prunes the expired row
    assert st.stats()["snapshots"] == 1


def test_snapshot_survives_reopen(tmp_path):
    SqliteStore(tmp_path / "c.sqlite").snapshot_put('["p","m"]', [1], now=100, ttl=60)
    assert SqliteStore(tmp_path / "c.sqlite").snapshot_get('["p","m"]', now=130) == (True, [1])


def test_snapshot_clear_by_provider(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    st.snapshot_put('["p","m"]', [1], now=100, ttl=60)
    st.snapshot_put('["q","m"]', [2], now=100, ttl=60)
    st.snapshot_clear("p")
    assert st.snapshot_get('["p","m"]', now=101) == (False, None)
    assert st.snapshot_get('["q","m"]', now=101) == (True, [2])


def test_connections_are_per_thread(tmp_path):
    st = SqliteStore(tmp_path / "c.sqlite")
    errs = []

    def work(i):
        try:
            for j in range(20):
                st.write_page(("p", f"S{i}", "1m"), _df([j * 60]), j * 60, j * 60 + 60, None, now=1000)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errs == []
    assert len(st.keys()) == 4


def test_memory_snapshots_ttl_bound_and_clear():
    m = MemorySnapshots(bound=2)
    m.snapshot_put('["p","a"]', 1, now=0, ttl=10)
    m.snapshot_put('["p","b"]', 2, now=1, ttl=10)
    m.snapshot_put('["q","c"]', 3, now=2, ttl=10)   # evicts the oldest
    assert m.snapshot_get('["p","a"]', now=3) == (False, None)
    assert m.snapshot_get('["p","b"]', now=10) == (True, 2)
    assert m.snapshot_get('["p","b"]', now=11) == (False, None)
    m.snapshot_clear("q")
    assert m.snapshot_get('["q","c"]', now=3) == (False, None)
```

- [ ] **2.2 Run:** `$VENV/bin/python -m pytest -q tests/test_cache_store.py`. Expected: import error.
- [ ] **2.3 Implement** `lse_terminal/engine/cache_store.py`:

```python
"""Storage for the market-data cache (design D6).

One SQLite file, WAL mode, one connection per thread. A write
transaction never spans a source call: the caller fetches first, then
hands one page to write_page, which writes bars and coverage together.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pandas as pd
from fastapi.encoders import jsonable_encoder

from lse_terminal.contracts import CANDLE_COLUMNS

Key = tuple[str, str, str]  # (provider, symbol, timeframe)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  provider TEXT, symbol TEXT, tf TEXT, ts INTEGER,
  open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (provider, symbol, tf, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS coverage (
  provider TEXT, symbol TEXT, tf TEXT, start INTEGER, "end" INTEGER,
  empty_until INTEGER,
  PRIMARY KEY (provider, symbol, tf, start));
CREATE TABLE IF NOT EXISTS snapshots (
  key TEXT PRIMARY KEY, at REAL, expires REAL, body TEXT);
"""


class SqliteStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        c = self._conn()
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            c.execute("PRAGMA busy_timeout=5000")
            self._local.c = c
        return c

    # ── candles ───────────────────────────────────────────────────────
    def read(self, key: Key, a: int, b: int) -> pd.DataFrame:
        rows = self._conn().execute(
            "SELECT ts, open, high, low, close, volume FROM bars WHERE provider=? "
            "AND symbol=? AND tf=? AND ts>=? AND ts<? ORDER BY ts", (*key, a, b)).fetchall()
        df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
        df["ts"] = df["ts"].astype("int64")
        return df

    def coverage(self, key: Key, now: int) -> list[tuple[int, int, int | None]]:
        """Active ranges, ascending: empty_until NULL or still in the future."""
        return [tuple(r) for r in self._conn().execute(
            'SELECT start, "end", empty_until FROM coverage WHERE provider=? AND symbol=? '
            "AND tf=? AND (empty_until IS NULL OR empty_until>?) ORDER BY start", (*key, now))]

    def write_page(self, key: Key, df: pd.DataFrame, s: int, e: int,
                   empty_until: int | None, now: int) -> None:
        """One transaction: upsert the page's bars, then add coverage [s, e)
        merged with overlapping or adjacent ranges of the same kind. Expired
        empty ranges of the key are dropped on the way."""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            if len(df):
                c.executemany(
                    "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?)",
                    [(*key, int(r.ts), float(r.open), float(r.high), float(r.low),
                      float(r.close), float(r.volume)) for r in df.itertuples(index=False)])
            c.execute("DELETE FROM coverage WHERE provider=? AND symbol=? AND tf=? "
                      "AND empty_until IS NOT NULL AND empty_until<=?", (*key, now))
            same = ("empty_until IS NULL" if empty_until is None else "empty_until=?")
            args = () if empty_until is None else (empty_until,)
            rows = c.execute(
                f'SELECT start, "end" FROM coverage WHERE provider=? AND symbol=? AND tf=? '
                f'AND {same} AND start<=? AND "end">=?', (*key, *args, e, s)).fetchall()
            lo = min([s, *(r[0] for r in rows)])
            hi = max([e, *(r[1] for r in rows)])
            c.executemany("DELETE FROM coverage WHERE provider=? AND symbol=? AND tf=? AND start=?",
                          [(*key, r[0]) for r in rows])
            c.execute("INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?)",
                      (*key, lo, hi, empty_until))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def clear(self, key: Key) -> None:
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            for t in ("bars", "coverage"):
                c.execute(f"DELETE FROM {t} WHERE provider=? AND symbol=? AND tf=?", key)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def keys(self) -> list[Key]:
        return [tuple(r) for r in self._conn().execute(
            "SELECT provider, symbol, tf FROM coverage UNION "
            "SELECT provider, symbol, tf FROM bars ORDER BY 1, 2, 3")]

    # ── snapshots ─────────────────────────────────────────────────────
    def snapshot_get(self, key: str, now: float):
        r = self._conn().execute("SELECT expires, body FROM snapshots WHERE key=?",
                                 (key,)).fetchone()
        if r is None or r[0] <= now:
            return False, None
        return True, json.loads(r[1])

    def snapshot_put(self, key: str, body, now: float, ttl: float) -> None:
        c = self._conn()
        c.execute("DELETE FROM snapshots WHERE expires<=?", (now,))
        c.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
                  (key, now, now + ttl, json.dumps(jsonable_encoder(body))))

    def snapshot_clear(self, provider: str | None = None) -> None:
        if provider is None:
            self._conn().execute("DELETE FROM snapshots")
        else:
            self._conn().execute("DELETE FROM snapshots WHERE key LIKE ?",
                                 (json.dumps([provider])[:-1] + ",%",))

    def stats(self) -> dict:
        c = self._conn()
        out = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("bars", "coverage", "snapshots")}
        out["bytes"] = self.path.stat().st_size if self.path.exists() else 0
        return out


class MemorySnapshots:
    """Snapshot TTL cache for hosted mode and LSE_TERMINAL_CACHE=off: the
    same interface as SqliteStore's snapshot half, in process memory,
    bounded like the route caches it replaces."""

    def __init__(self, bound: int = 512):
        self.bound = bound
        self._d: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def snapshot_get(self, key: str, now: float):
        with self._lock:
            hit = self._d.get(key)
        if hit is None or hit[0] <= now:
            return False, None
        return True, hit[1]

    def snapshot_put(self, key: str, body, now: float, ttl: float) -> None:
        with self._lock:
            for k in [k for k, v in self._d.items() if v[0] <= now]:
                del self._d[k]
            if key not in self._d and len(self._d) >= self.bound:
                del self._d[min(self._d, key=lambda k: self._d[k][0])]
            self._d[key] = (now + ttl, body)

    def snapshot_clear(self, provider: str | None = None) -> None:
        prefix = None if provider is None else json.dumps([provider])[:-1] + ","
        with self._lock:
            for k in [k for k in self._d if prefix is None or k.startswith(prefix)]:
                del self._d[k]
```

  Check: `json.dumps(["p"])[:-1] + ","` is `'["p",'`, the prefix of every key that `CachedProvider` builds for provider `p` (Task 3 builds keys with `json.dumps([provider, method, ...])`, default separators). `MemorySnapshots` evicts the entry that expires first; with one TTL per method that is the oldest of that method.
- [ ] **2.4 Run** the store tests, then the full suite. Expected: new tests pass, baseline unchanged.
- [ ] **2.5 Tick 3.1 (store half). Commit.**

### Task 3: `CandleCache` and `CachedProvider`

**Files:** create `lse_terminal/engine/cache.py`, `tests/test_cache.py`.

This is the heart of the change. The rules are D7 (with the Task 1 clarifications) and D8. Read `design-review.md` §1.4 for each counter-example trace before you write its test.

- [ ] **3.1 Write the test fixture and the tests** in `tests/test_cache.py`. The fake source models the model's source: a bar exists from the moment it opens, its `close` is its progress `min(now - ts, step)` (so `close == step` means final), it answers half-open windows, oldest N with `start`, newest N without, and it caps every answer at `cap` rows.

```python
import random
import threading
import time

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from lse_terminal.contracts import CANDLE_COLUMNS, TIMEFRAME_SECONDS, Provider, to_epoch
from lse_terminal.engine import cache as cachemod
from lse_terminal.engine.cache import CachedProvider, CandleCache
from lse_terminal.engine.cache_store import MemorySnapshots, SqliteStore

T0 = 1_700_000_040          # a 1m grid line (divisible by 60), after 1e9


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Fake(Provider):
    name = "fake"
    title = "Fake"
    timeframes = list(TIMEFRAME_SECONDS)

    def __init__(self, clock, bars, cap=5000, lag=0, delay=0.0):
        self.clock, self.bars, self.cap, self.lag, self.delay = clock, bars, cap, lag, delay
        self.calls, self.ok, self.restated, self.empty_once = [], True, {}, set()
        self.drop = set()        # ts the newest-N path loses (cx_skip)

    def configured(self):
        return self.ok

    def search(self, query, limit=50):
        return []

    def candles(self, symbol, timeframe, limit=500, start=None, end=None):
        if not self.ok:
            raise RuntimeError("not configured")
        if self.delay:
            time.sleep(self.delay)
        step = TIMEFRAME_SECONDS[timeframe]
        now = self.clock() - self.lag
        s, e = to_epoch(start), to_epoch(end)
        self.calls.append((symbol, timeframe, limit, s, e))
        if (s, e) in self.empty_once:            # a failure answered as empty
            self.empty_once.discard((s, e))
            return pd.DataFrame(columns=CANDLE_COLUMNS)
        ts = [t for t in self.bars.get(symbol, []) if t <= now
              and (s is None or t >= s) and (e is None or t < e)]
        if s is None:
            ts = [t for t in ts if t not in self.drop]
        n = min(limit, self.cap)
        ts = ts[:n] if s is not None else ts[-n:]
        rows = [(t, 1.0, 1.0, 1.0, float(min(now - t, step)) + self.restated.get(t, 0.0), 1.0)
                for t in ts]
        df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
        df["ts"] = df["ts"].astype("int64")
        return df


def grid(start, n, step=60):
    return [start + i * step for i in range(n)]


@pytest.fixture()
def env(tmp_path):
    clock = Clock(T0 + 100 * 60 + 30)
    store = SqliteStore(tmp_path / "c.sqlite")
    cache = CandleCache(store=store, snapshots=store, clock=clock)
    fake = Fake(clock, {"S": grid(T0, 200)})
    return clock, cache, fake, CachedProvider(fake, cache)


def same(wrapped, fake, tf="1m", symbol="S", **kw):
    """I3: the wrapped answer equals the unwrapped one at the same instant.
    The unwrapped reference call is removed from fake.calls, so call-count
    assertions see only the wrapper's calls."""
    got = wrapped.candles(symbol, tf, **kw).reset_index(drop=True)
    n = len(fake.calls)
    want = fake.candles(symbol, tf, **kw).reset_index(drop=True)
    del fake.calls[n:]
    assert_frame_equal(got, want, check_dtype=False)
    return got
```

  Write these tests. Each one names its spec scenario or counter-example in its docstring.

  1. `test_repeat_window_is_one_tail_call` (spec "Repeat poll"): `same(w, fake, start=T0, limit=5000)` twice at the same clock; the second adds exactly one source call, with `s == edge` where `edge = clock() - 60 - 60 + 1`.
  2. `test_closed_edge_nothing_stored_at_or_past_edge` (spec "Closed edge", `cx_edge`): `tf="5m"`, bars on a 300 s grid, clock `G + 30` for a 300 s grid line `G`. Then `edge = G + 30 - 60 - 300 + 1 = G - 329`. After a windowed call from far back: `store.read(key, 0, 2**62)["ts"].max() < edge`, every coverage end is `<= edge`, and the bar at `G - 300` (complete at the source only at `G`, inside the settle window) is neither stored nor covered. The response still equals the unwrapped answer, including that bar and the forming bar at `G`.
  3. `test_gap_between_two_covered_windows` (spec): fill `[T0, T0+10m)` and `[T0+20m, T0+30m)`, then ask `[T0, T0+30m)`: exactly one new source call, `s == T0+10m`, `e == T0+20m`.
  4. `test_truncated_answer_pages_from_last_ts_plus_one` (spec, `cx_trunc`): `monkeypatch.setattr(cachemod, "PAGE", 3)`, `fake.cap = 3`, ask `start=T0, end=T0+8*60, limit=4`: response `ts` equals `grid(T0, 4)`; the second source call starts at `T0 + 2*60 + 1`.
  5. `test_off_grid_bar_does_not_over_reach` (spec, `cx_irr`): `tf="1h"`, `PAGE=1`, `fake.cap=1`, bars at `H+1800, H+3600, H+7200` with `H` a 1h grid line; clock far ahead; one windowed call from `H`: the first page's coverage ends at `H+1801`; the bar at `H+3600` is stored after the call.
  6. `test_holiday_is_covered_then_heals` (spec "Holiday", "Empty answer heals", `cx_transient`): bars with a 10-minute hole; `fake.empty_once.add((g, h))` for the first gap request so the source answers empty once; within 3600 s a repeat call makes no call for that gap; after `clock.t += 3601` the next call asks for the gap again and the response equals the unwrapped answer.
  7. `test_limit_only_cold_then_warm` (spec "First and second call"): `same(w, fake, limit=50)` twice: the first source call has `s is None, limit == 50`; the second adds only one call, `s == edge`.
  8. `test_hit_test_counts_only_the_run_at_the_edge` (spec, `cx_count`): build coverage with a hole (windowed call on `[T0, T0+5m)`, then a limit-only call that covers only the newest bars); a limit-only call with a limit larger than the edge run equals the unwrapped answer (no splice across the hole).
  9. `test_one_minute_chart_keeps_second_newest_bar` (spec, `cx_tail`): `settle = step = 60`; a fully covered key polled with `limit=5000`: `same(...)` passes and the response holds the bar at `edge` and the forming bar.
  10. `test_same_key_one_source_call` (spec "Two threads, one key"): `fake.delay = 0.2`; two threads ask the same uncovered window; the source sees one call for that window (the other thread's later call is only its tail).
  11. `test_two_keys_do_not_wait` (spec "Two keys do not serialise"): a second fake symbol `"T"`; make the source slow (`delay = 2.0`) only for `"S"` by subclassing; thread on `"T"` completes in under 1 s while `"S"` is in flight.
  12. `test_clear_waits_for_fill_and_fill_keeps_its_bars` (spec "Clear during a fill", `cx_lock`): `delay = 0.3`; start a fill in a thread, then call `cache.clear("fake", "S", "1m")` from the main thread once the fill holds the lock; the clear returns after the fill; the fill's response equals the unwrapped answer; afterwards the store is empty for the key.
  13. `test_clear_one_key_refetches_full_window` (spec "Clear one key").
  14. `test_bypass_tick_unknown_tf_end_only_and_off`: `tf="tick"`, `tf="7m"`, `end` without `start`: each call reaches the source unchanged and `store.keys() == []`; a `CandleCache(store=None, snapshots=MemorySnapshots(), clock=clock)` passes every candles call through.
  15. `test_sign_out_passes_through` (spec "Covered window after sign-out"): cover a window, then `fake.ok = False`: the call raises `RuntimeError` from the source.
  16. `test_wrapper_is_transparent` (spec "Wrapper is transparent"): `w.name`, `w.title`, `w.timeframes`, `w.configured()`, `w.stream` equal the fake's; `w.capabilities() == {"search", "candles"}`; `hasattr(w, "is_custom")`.
  17. `test_locks_survive_reregistration`: `cache.lock(("fake","S","1m")) is cache.lock(("fake","S","1m"))` across two `CachedProvider` instances over two `Fake` objects with the same name.
  18. `test_cx_skip_accepted_hole`: `fake.drop = {T0 + 50*60}`; a limit-only cold call: the documented accepted outcome is a covered tick with no stored bar; assert the tick is inside a coverage range and absent from `store.read`.
  19. `test_cx_lag_accepted_partial_bar`: `fake.lag = 120` (> settle 60): after a windowed call some stored bar has `close < 60` (partial). Accepted risk; the test pins it.
  20. `test_cx_restate_served_until_clear`: fill, then `fake.restated[T0] = 5.0`; the wrapped answer still has the old close for `T0`; after `cache.clear("fake","S","1m")` it has the new one.
  21. `test_random_calls_match_unwrapped` (I3 over many states): seed `random.Random(7)`; bars on a 1m grid with three random holes; 200 iterations; each picks `clock.t += rng.choice([0, 1, 30, 59, 60, 61, 600])`, and one of: limit-only with a random limit in `[1, 300]`, windowed with a random start in the bar range, random end or `None`, random limit; with `PAGE` patched to 7 and `cap` 7. Each call: `same(...)`.

  Snapshot tests (D8):

  22. `test_snapshot_chain_key_is_canonical` (spec "Chain within TTL"): a `Fake` subclass with `option_chain` counting calls; `w.option_chain("spy")` then `w.option_chain(" SPY ")`: one source call.
  23. `test_snapshot_ttl_default_and_override` (spec "Provider override"): default `option_chain` TTL 15 s: a call at `+14` is cached, at `+16` reaches the source; with `snapshot_ttl = {"option_chain": 60}` a call at `+59` is cached.
  24. `test_snapshot_survives_restart` (spec): a new `CandleCache` over a new `SqliteStore` on the same file serves the stored body.
  25. `test_snapshot_sign_out_passes_through`: `fake.ok = False` → the source method is called (and its error surfaces).
  26. `test_snapshot_memory_backend`: the same TTL behaviour with `CandleCache(store=None, snapshots=MemorySnapshots(), clock=clock)`.
  27. `test_snapshot_errors_are_not_cached`: a source method that raises once, then answers: the first call raises, the second reaches the source.

- [ ] **3.2 Run:** expected import errors, then failures.
- [ ] **3.3 Implement** `lse_terminal/engine/cache.py`:

```python
"""Read-through market-data cache (design D5 to D8).

CachedProvider wraps one provider. It is NOT a Provider subclass: every
attribute it does not define comes from the wrapped provider through
__getattr__, so name, timeframes, configured(), capabilities(), quote
and stream are the source's own. The registry owns one CandleCache that
holds the stores, the per-key locks and the clock; every wrapper shares
it, so a re-registered provider (set_key) keeps its locks.

Fill rules are D7, checked by openspec/changes/data-source-contract-cache/
design.tla. Change them only with the model.
"""

from __future__ import annotations

import json
import threading
import time

import pandas as pd

from lse_terminal.contracts import CANDLE_COLUMNS, TIMEFRAME_SECONDS, to_epoch

PAGE = 5000            # rows the wrapper asks the source for per gap page (L)
EMPTY_TTL = 3600       # an empty answer's coverage expires after this (D6)

DEFAULT_TTL = {        # D8
    "options_underlyings": 60, "option_chain": 15, "options_flow": 15,
    "economic_calendar": 60, "macro_catalog": 60, "macro_series": 600,
    "logos": 3600,
}


# ── pure range arithmetic over active coverage ───────────────────────
def runs(cov) -> list[tuple[int, int]]:
    """Maximal contiguous runs from active coverage rows (start, end, eu)."""
    out: list[list[int]] = []
    for s, e, _ in sorted(cov):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def gaps(rs, a: int, b: int) -> list[tuple[int, int]]:
    out, cur = [], a
    for s, e in rs:
        if e <= cur or s >= b:
            continue
        if s > cur:
            out.append((cur, min(s, b)))
        cur = max(cur, e)
        if cur >= b:
            break
    if cur < b:
        out.append((cur, b))
    return out


def run_end(rs, a: int) -> int:
    """End of the run that contains a, else a."""
    for s, e in rs:
        if s <= a < e:
            return e
    return a


def _empty() -> pd.DataFrame:
    df = pd.DataFrame(columns=CANDLE_COLUMNS)
    df["ts"] = df["ts"].astype("int64")
    return df


def _cat(*frames) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return _empty()
    return pd.concat(frames, ignore_index=True)[CANDLE_COLUMNS]


class CandleCache:
    def __init__(self, store, snapshots, clock=time.time):
        self.store = store              # SqliteStore, or None: candles pass through
        self.snapshots = snapshots      # SqliteStore or MemorySnapshots
        self.clock = clock
        self._locks: dict[tuple, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock(self, key: tuple) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def clear(self, provider=None, symbol=None, timeframe=None) -> int:
        """Drop matching candle keys, each under its own lock (D7.8, PR 3
        amendment 4). Snapshots only when no symbol/timeframe filter."""
        n = 0
        if self.store is not None:
            for key in sorted(self.store.keys()):
                if ((provider is None or key[0] == provider)
                        and (symbol is None or key[1] == symbol)
                        and (timeframe is None or key[2] == timeframe)):
                    with self.lock(key):
                        self.store.clear(key)
                    n += 1
        if symbol is None and timeframe is None:
            self.snapshots.snapshot_clear(provider)
        return n


class CachedProvider:
    def __init__(self, inner, cache: CandleCache):
        self._inner = inner
        self._cache = cache

    def __getattr__(self, name):            # only for names not defined here
        return getattr(self._inner, name)

    # ── candles (D7) ──────────────────────────────────────────────────
    def candles(self, symbol: str, timeframe: str, limit: int = 500,
                start: str | None = None, end: str | None = None) -> pd.DataFrame:
        inner, c = self._inner, self._cache
        step = TIMEFRAME_SECONDS.get(timeframe)
        ws, we = to_epoch(start), to_epoch(end)
        if (c.store is None or step is None or not inner.configured()
                or (we is not None and ws is None)):                        # D7.1
            return inner.candles(symbol, timeframe, limit=limit, start=start, end=end)
        key = (inner.name, symbol, timeframe)
        with c.lock(key):                                                    # D7.7
            now = int(c.clock())
            settle = min(step, int(inner.settle_seconds))
            edge = now - settle - step + 1
            if ws is None:
                return self._limit_only(key, symbol, timeframe, int(limit), now, edge)
            return self._windowed(key, symbol, timeframe, ws, we, int(limit), now, edge)

    def _fill(self, key, symbol, tf, g, h, now, ws, limit, max_pages=None):
        """Fill gap [g, h) page by page (D7.3). Stops when the stored bars in
        [ws, g) reach limit, when the gap is covered, or after max_pages."""
        st, pages = self._cache.store, 0
        while g < h and (max_pages is None or pages < max_pages):
            if len(st.read(key, ws, g)) >= limit:
                return
            df = self._inner.candles(symbol, tf, limit=PAGE, start=g, end=h)
            df = df[(df["ts"] >= g) & (df["ts"] < h)][CANDLE_COLUMNS]   # contract guard
            pages += 1
            if df.empty:
                st.write_page(key, df, g, h, now + EMPTY_TTL, now)          # D7.5
                return
            if len(df) >= PAGE:                                             # truncated
                last = int(df["ts"].iloc[-1])
                st.write_page(key, df, g, last + 1, None, now)
                g = last + 1
            else:
                st.write_page(key, df, g, h, None, now)
                return

    def _tail(self, symbol, tf, frm, end, limit):
        if limit <= 0:
            return _empty()
        return self._inner.candles(symbol, tf, limit=limit, start=frm, end=end)  # D7.6

    def _windowed(self, key, symbol, tf, ws, we, limit, now, edge):
        st = self._cache.store
        wc = edge if we is None else min(we, edge)
        if ws < wc:
            for g, h in gaps(runs(st.coverage(key, now)), ws, wc):
                self._fill(key, symbol, tf, g, h, now, ws, limit)
                if len(st.read(key, ws, h)) >= limit:
                    break
        rend = run_end(runs(st.coverage(key, now)), ws) if ws < wc else ws
        body = st.read(key, ws, min(rend, wc))
        tail = None
        if (we is None or we > edge) and rend >= wc:
            tail = self._tail(symbol, tf, max(edge, ws), we, limit - len(body))
        return _cat(body, tail).head(limit).reset_index(drop=True)

    def _limit_only(self, key, symbol, tf, limit, now, edge):
        st = self._cache.store
        rs = runs(st.coverage(key, now))
        if rs and rs[-1][1] < edge:                                          # D7.4
            self._fill(key, symbol, tf, rs[-1][1], edge, now, rs[-1][1], PAGE, max_pages=1)
            rs = runs(st.coverage(key, now))
        if rs and rs[-1][1] == edge:
            run = st.read(key, rs[-1][0], edge)
            if len(run) >= limit:
                tail = self._tail(symbol, tf, edge, None, limit)
                return _cat(run.tail(limit), tail).tail(limit).reset_index(drop=True)
        df = self._inner.candles(symbol, tf, limit=limit)
        closed = df[df["ts"] < edge][CANDLE_COLUMNS]
        if len(closed):
            st.write_page(key, closed, int(closed["ts"].iloc[0]), edge, None, now)
        return df.reset_index(drop=True)

    # ── snapshots (D8) ────────────────────────────────────────────────
    def _snap(self, method: str, canon: list, call):
        inner, c = self._inner, self._cache
        if not inner.configured():                                           # D5 sign-out
            return call()
        key = json.dumps([inner.name, method, *canon], default=str)
        now = c.clock()
        hit, body = c.snapshots.snapshot_get(key, now)
        if hit:
            return body
        body = call()
        ttl = dict(inner.snapshot_ttl or {}).get(method, DEFAULT_TTL[method])
        c.snapshots.snapshot_put(key, body, now, ttl)
        return body

    def options_underlyings(self):
        return self._snap("options_underlyings", [], self._inner.options_underlyings)

    def option_chain(self, underlying, type=None, expiry=None, limit=5000):
        return self._snap("option_chain", [underlying.strip().upper(), type, expiry, limit],
                          lambda: self._inner.option_chain(underlying, type=type,
                                                           expiry=expiry, limit=limit))

    def options_flow(self, underlying=None, type=None, min_premium=None, limit=300):
        u = (underlying or "").strip().upper() or None
        return self._snap("options_flow", [u, type, min_premium, limit],
                          lambda: self._inner.options_flow(underlying=underlying, type=type,
                                                           min_premium=min_premium, limit=limit))

    def economic_calendar(self, region=None, event=None, start=None, end=None,
                          released_only=False, order="asc", limit=5000):
        return self._snap("economic_calendar",
                          [region, event, start, end, released_only, order, limit],
                          lambda: self._inner.economic_calendar(
                              region=region, event=event, start=start, end=end,
                              released_only=released_only, order=order, limit=limit))

    def macro_catalog(self):
        return self._snap("macro_catalog", [], self._inner.macro_catalog)

    def macro_series(self, symbol, dataset=None, start=None, end=None, order="asc", limit=5000):
        return self._snap("macro_series", [symbol, dataset, start, end, order, limit],
                          lambda: self._inner.macro_series(symbol, dataset=dataset, start=start,
                                                           end=end, order=order, limit=limit))

    def logos(self):
        return self._snap("logos", [], self._inner.logos)
```

  Check before you finish:
  - The `MemorySnapshots` in hosted and off mode keeps `jsonable_encoder` out of the path, so bodies stay the source's own objects, as the PR 2 route cache did.
  - `__getattr__` is not called for `_inner` once it is set; a missing attribute raises `AttributeError` from the inner provider, which keeps `hasattr` and `getattr(p, x, default)` honest.
  - Read `design-review.md` §1.5 and confirm each amended flag maps to a line: FixEdge (`edge`), FixTail (`_tail` from `max(edge, ws)`, nothing stored), FixTrunc (`last + 1`, paging), FixCount (`rs[-1]` only), FixExtend (one page from `rs[-1][1]`), FixLock (everything under `c.lock(key)`; `clear` takes it).
- [ ] **3.4 Run** `tests/test_cache.py`, then the full suite.
- [ ] **3.5 Tick 3.2, 3.3, 3.6. Commit.**

### Task 4: Registry and server wiring

**Files:** `lse_terminal/engine/registry.py`, `lse_terminal/engine/server.py`, `tests/test_routing.py`, `tests/test_registry.py`, `tests/test_api.py` (only if a test relies on the old helper).

- [ ] **4.1 Write the failing tests.**
  - `tests/test_registry.py`: `Registry(cache=c).register(p)` with `p.cacheable = True` gives a `CachedProvider` whose `name` is `p.name`; with `cacheable = False` it gives `p` itself; `Registry()` (no cache) never wraps; `load_plugins` still records the shadowing error with a wrapped existing provider.
  - `tests/test_routing.py`: replace the tests that monkeypatch the route helper's `time.time` with the same assertions through `app.state.cache.clock` (set `app.state.cache.clock = clock` in the fixture). Keep: TTL 15 s for the chain, 600 s for macro series, a cache hit still carries `X-Source`, sign-out inside the TTL answers 409.
  - New in `tests/test_routing.py`: `GET /api/cache/stats` returns counts and `bytes`; `DELETE /api/cache?provider=stub` empties stub snapshots (the next chain call reaches the stub); both answer 403 in hosted mode (follow the existing hosted tests for how they build a hosted app); with `LSE_TERMINAL_CACHE=off` no `cache.sqlite` is created in the config dir and the chain TTL still holds.
- [ ] **4.2 Implement the registry:**

```python
class Registry:
    def __init__(self, cache=None):
        self._providers: dict[str, Provider] = {}
        self._engines: dict = {}
        self.plugin_errors: list[str] = []
        self.cache = cache          # CandleCache; None = never wrap (tests, tools)

    def register(self, provider: Provider) -> None:
        if not provider.name:
            raise ValueError(f"{type(provider).__name__} has an empty name")
        if self.cache is not None and provider.cacheable:
            from lse_terminal.engine.cache import CachedProvider  # noqa: PLC0415
            provider = CachedProvider(provider, self.cache)
        self._providers[provider.name] = provider
```

- [ ] **4.3 Wire `create_app`:** move the `hosted = ...` line above `reg = Registry()`. Then:

```python
    from lse_terminal.engine.cache import CandleCache            # noqa: PLC0415
    from lse_terminal.engine.cache_store import MemorySnapshots, SqliteStore  # noqa: PLC0415
    cache_on = (not hosted
                and os.environ.get("LSE_TERMINAL_CACHE", "").strip().lower() != "off")
    _store = SqliteStore(config_dir() / "cache.sqlite") if cache_on else None
    cache = CandleCache(store=_store, snapshots=_store or MemorySnapshots())
    reg = Registry(cache=cache)
    app.state.cache = cache
```

  `app` must exist before `app.state.cache`; place the assignment after `app = FastAPI(...)`. Import `config_dir` from `lse_terminal.engine.config` if `server.py` does not already.
- [ ] **4.4 Remove the route helper:** delete `_SNAP_BOUND`, `snap_cache` and `_snap`. Each snapshot route calls the method through `_call(fetch, what)` with the same canonical arguments it passes today, for example `_call(lambda: p.option_chain(und, type=type or None, expiry=expiry or None, limit=lim), "options request")`. The wrapper owns the TTL now. Keep every 404/502 text.
- [ ] **4.5 Add the cache routes** next to the other local-only routes:

```python
    @app.get("/api/cache/stats")
    def cache_stats():
        deny_hosted()
        c = app.state.cache
        return {"enabled": c.store is not None, **(c.store.stats() if c.store else {})}

    @app.delete("/api/cache")
    def cache_clear(provider: str | None = None, symbol: str | None = None,
                    timeframe: str | None = None):
        deny_hosted()
        return {"cleared": app.state.cache.clear(provider, symbol, timeframe)}
```

- [ ] **4.6 Run** the full suite. Expected: baseline plus new tests; every PR 2 routing test green.
- [ ] **4.7 Tick 3.4, 3.5 (routes half), 3.9. Commit.**

### Task 5: CLI and README

**Files:** `lse_terminal/cli.py`, `README.md`, `tests/test_cli_cache.py` (new).

- [ ] **5.1 Write the failing tests** (`tests/test_cli_cache.py`): with a monkeypatched `urllib.request.urlopen` that records the request, `main(["cache", "clear", "--provider", "lse", "--port", "7787"])` sends `DELETE http://127.0.0.1:7787/api/cache?provider=lse` and returns 0; when `urlopen` raises `URLError`, `main(["cache", "clear"])` opens `config_dir()/cache.sqlite` directly (sandbox `LSE_TERMINAL_CONFIG_DIR` to `tmp_path`, pre-fill one key) and the key is gone; `main(["cache", "stats"])` prints JSON with `bars`.
- [ ] **5.2 Implement:** at the top of `main`, before the `--run-script` branch, intercept `argv[0] == "cache"`:

```python
    if argv and argv[0] == "cache":
        return _cache_cmd(argv[1:])
```

```python
def _cache_cmd(argv: list[str]) -> int:
    """`lset cache clear|stats`: through the running engine (it holds the
    key locks), straight to the file only when no engine answers (D7.7)."""
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    ap = argparse.ArgumentParser(prog="lset cache")
    ap.add_argument("action", choices=["clear", "stats"])
    ap.add_argument("--provider"); ap.add_argument("--symbol"); ap.add_argument("--timeframe")
    ap.add_argument("--port", type=int, default=7787)
    a = ap.parse_args(argv)
    q = urllib.parse.urlencode({k: v for k, v in (("provider", a.provider),
                                ("symbol", a.symbol), ("timeframe", a.timeframe)) if v})
    url = f"http://127.0.0.1:{a.port}/api/cache" + ("/stats" if a.action == "stats" else "")
    req = urllib.request.Request(url + (f"?{q}" if q and a.action == "clear" else ""),
                                 method="GET" if a.action == "stats" else "DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(r.read().decode())
            return 0
    except urllib.error.URLError:
        pass   # no engine on that port: no reader can be in flight, use the file
    from lse_terminal.engine.cache import CandleCache
    from lse_terminal.engine.cache_store import SqliteStore
    from lse_terminal.engine.config import config_dir
    st = SqliteStore(config_dir() / "cache.sqlite")
    if a.action == "stats":
        print(json.dumps(st.stats()))
    else:
        print(json.dumps({"cleared": CandleCache(st, st).clear(a.provider, a.symbol, a.timeframe)}))
    return 0
```

  `URLError` covers a refused connection. An `HTTPError` (a subclass) from a running engine must not fall back to the file: catch `urllib.error.HTTPError` first, print its body, and return 1.
- [ ] **5.3 README:** one paragraph under the data-source section, in STE: the file `~/.config/lse-terminal/cache.sqlite`; closed candles and seven snapshot methods; `LSE_TERMINAL_CACHE=off` turns the file off; `lset cache clear [--provider P --symbol S --timeframe T]` and `lset cache stats`; a restated bar stays until a clear.
- [ ] **5.4 Run** the full suite. **Tick 3.5 (CLI half), 3.7. Commit.**

### Task 6: Review and merge

- [ ] **6.1** Run the gate again (`tlc design.tla -config design.cfg`) and tick 3.8.
- [ ] **6.2** Opus pre-merge review over `git diff develop...feat/pr3-cache`, given the spec and this plan, not conclusions.
- [ ] **6.3** Fold the findings. Re-run the suite.
- [ ] **6.4** `openspec validate data-source-contract-cache`. Fast-forward `develop`, push `origin/develop`, remove the worktree. Archive the change (`openspec archive`) only after the user confirms PR 4 does not need it open.

---

## Design review amendments (2026-11-05, before Task 3)

An Opus review checked the plan's Task 3 code against `design.tla` (scratch model with plan flags) and against probes on the plan's own code. These amendments override the Task 3, 4 and 5 text above where they differ.

- **R1 (HIGH) Limit-only path: tail from the newest run, hit on run + tail.** The plan's hit test `len(run) >= limit` almost never holds after a miss, and its hit tail `candles(start=edge, limit=limit)` returns the wrong end (TLC `plan.cfg`: I3 violated). Replace `_limit_only` with:
  1. `rs = runs(coverage)`. If `rs` and `rs[-1][1] <= edge`: `c0, c = rs[-1]`; `tail = inner.candles(symbol, tf, limit=PAGE, start=c)`.
  2. If `len(tail) < PAGE` (complete since `c`): store the closed bars (`ts < edge`) with coverage `[c, edge)` only when there is at least one closed bar (never an empty range here). `pool = store.read(key, c0, edge)` + `tail[ts >= edge]`. If `len(pool) >= limit` or `c0 == HISTORY`, serve `pool.tail(limit)`.
  3. Otherwise (no coverage, truncated tail, or too few bars): miss. `df = inner.candles(symbol, tf, limit=limit)`; store `df[ts < edge]` with coverage from `HISTORY` when `len(df) < limit` (a short answer is the whole history, contract clause), else from `first_ts`, to `edge`; serve `df` as is.
  `HISTORY = -(2**62)`. TLC passes this (`fixed.cfg` 11.45M states, `tfr.cfg` 26.95M, step-2 variants). One source call per poll.
- **R2 (HIGH) Fail open.** `create_app`: when `SqliteStore(...)` raises, log it and use `store=None` + `MemorySnapshots`. `CachedProvider`: any `sqlite3.Error` in the cached path passes the call through to the source (I3-safe); same for snapshot get/put.
- **R3 (MEDIUM) Lock timeout.** `lock.acquire(timeout=30)`; on timeout pass the call through to the source.
- **R4 (MEDIUM) CLI finds the engine.** The desktop app starts the engine on the first free port from 7799 (`desktop/main.js`), the CLI defaults to 7787. The engine process writes `config_dir()/engine.json` = `{"port", "pid"}` at start; `lset cache` reads it (falls back to `--port`); it uses the file directly only when no engine answers.
- **R5 (MEDIUM-LOW) Logos leave the snapshot cache.** `LseProvider.logos()` returns `{}` on a failed fetch and memoizes only success; the wrapper would pin that `{}` for an hour. PR 2 never cached logos. Six snapshot methods, not seven.
- **R6 (MEDIUM, accepted) Windowed empty-range churn.** A windowed poll's gap `[prev_edge, edge)` is often empty off-hours and is stored with a unique `empty_until`, so rows do not merge. Expired rows are pruned on write; growth is bounded by about one row per poll-minute per key for an hour. Accepted for v1 with a `ponytail:` comment; the chart path (limit-only) has no churn after R1.
- **R7 (LOW)** `__getattr__` raises `AttributeError` for `_inner` (copy/pickle recursion); other underscore names still delegate (`_lse_only()._lse()` needs `_lse`). Store gets `count(key, a, b)` (one `COUNT(*)`), used by the paging stop. `write_page` builds its rows before `BEGIN IMMEDIATE`.
- **R8 (LOW) Tests.** Test 7's expectation holds only with R1. Test 21: `cap` unbounded (a capping source violates the contract and caps the reference call too), `PAGE = 7`.
- **R9 (LOW) Model and spec.** Fold the review's flags into `design.tla`; `design.cfg` runs the implemented rules. Restate L1: it holds when `limit` is at least the number of bars in the window. Spec scenarios "Limit-only fill", "Invalidation", "Snapshot cache", "Repeat poll", "First and second call" follow R1, R5 and amendment 5.
