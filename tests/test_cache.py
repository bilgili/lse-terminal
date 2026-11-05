import copy
import logging
import random
import sqlite3
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
KEY = ("fake", "S", "1m")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Fake(Provider):
    name = "fake"
    title = "Fake"

    def __init__(self, clock, bars, cap=5000, lag=0, delay=0.0):
        self.timeframes = list(TIMEFRAME_SECONDS)
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
        step = TIMEFRAME_SECONDS.get(timeframe, 1)
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


class SnapFake(Fake):
    """Fake with two snapshot methods that count their source calls."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.snap_calls, self.fail_next = [], False

    def option_chain(self, underlying, type=None, expiry=None, limit=5000):
        if not self.ok:
            raise RuntimeError("not configured")
        self.snap_calls.append(("option_chain", underlying))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        return [{"u": underlying.strip().upper(), "n": len(self.snap_calls)}]

    def macro_catalog(self):
        if not self.ok:
            raise RuntimeError("not configured")
        self.snap_calls.append(("macro_catalog",))
        return [{"id": "GDP", "n": len(self.snap_calls)}]


def grid(start, n, step=60):
    return [start + i * step for i in range(n)]


@pytest.fixture()
def env(tmp_path):
    clock = Clock(T0 + 100 * 60 + 30)
    store = SqliteStore(tmp_path / "c.sqlite")
    cache = CandleCache(store=store, snapshots=store, clock=clock)
    fake = Fake(clock, {"S": grid(T0, 200)})
    return clock, cache, fake, CachedProvider(fake, cache)


@pytest.fixture()
def senv(tmp_path):
    clock = Clock(T0)
    store = SqliteStore(tmp_path / "c.sqlite")
    cache = CandleCache(store=store, snapshots=store, clock=clock)
    fake = SnapFake(clock, {})
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


def edge_1m(clock):
    return int(clock()) - 60 - 60 + 1


def covered(store, key, ts, now):
    return any(s <= ts < e for s, e, _ in store.coverage(key, now))


# ── D7 rules ─────────────────────────────────────────────────────────────
def test_repeat_window_is_one_tail_call(env):
    """Spec "Repeat poll": a covered window costs one tail call from the edge."""
    clock, _cache, fake, w = env
    same(w, fake, start=T0, limit=5000)
    n = len(fake.calls)
    same(w, fake, start=T0, limit=5000)
    assert len(fake.calls) == n + 1
    assert fake.calls[-1][3] == edge_1m(clock)


def test_closed_edge_nothing_stored_at_or_past_edge(env):
    """Spec "Closed edge", cx_edge: nothing at or past the edge is stored or covered."""
    clock, cache, fake, w = env
    G = (T0 // 300 + 20) * 300
    fake.bars = {"S": grid(G - 300 * 40, 46, 300)}
    clock.t = G + 30
    edge = G + 30 - 60 - 300 + 1
    got = same(w, fake, tf="5m", start=G - 300 * 40, limit=5000)
    assert G - 300 in set(got["ts"]) and G in set(got["ts"])
    key = ("fake", "S", "5m")
    stored = cache.store.read(key, 0, 2**62)
    assert stored["ts"].max() < edge
    assert G - 300 not in set(stored["ts"])
    cov = cache.store.coverage(key, clock())
    assert cov and all(e <= edge for _, e, _ in cov)
    assert not covered(cache.store, key, G - 300, clock())


def test_gap_between_two_covered_windows(env):
    """Spec "Gap between two covered windows": only the gap is fetched."""
    _clock, _cache, fake, w = env
    same(w, fake, start=T0, end=T0 + 600, limit=5000)
    same(w, fake, start=T0 + 1200, end=T0 + 1800, limit=5000)
    n = len(fake.calls)
    same(w, fake, start=T0, end=T0 + 1800, limit=5000)
    assert len(fake.calls) == n + 1
    assert fake.calls[-1][3:] == (T0 + 600, T0 + 1200)


def test_truncated_answer_pages_from_last_ts_plus_one(env, monkeypatch):
    """Spec "Truncated answer", cx_trunc: the next page starts at last_ts + 1."""
    _clock, _cache, fake, w = env
    monkeypatch.setattr(cachemod, "PAGE", 3)
    fake.cap = 3
    got = w.candles("S", "1m", start=T0, end=T0 + 8 * 60, limit=4)
    assert list(got["ts"]) == grid(T0, 4)
    assert fake.calls[1][3] == T0 + 2 * 60 + 1


def test_off_grid_bar_does_not_over_reach(env, monkeypatch):
    """Spec "Off-grid bar", cx_irr: coverage ends at last_ts + 1, not last_ts + step."""
    clock, cache, fake, w = env
    monkeypatch.setattr(cachemod, "PAGE", 1)
    fake.cap = 1
    H = (T0 // 3600 + 1) * 3600
    fake.bars = {"S": [H + 1800, H + 3600, H + 7200]}
    clock.t = H + 10 * 3600
    got = w.candles("S", "1h", start=H, limit=5000)
    assert list(got["ts"]) == [H + 1800, H + 3600, H + 7200]
    assert fake.calls[1][3] == H + 1801
    assert H + 3600 in set(cache.store.read(("fake", "S", "1h"), 0, 2**62)["ts"])


def test_holiday_is_covered_then_heals(env):
    """Spec "Holiday", "Empty answer heals", cx_transient."""
    clock, _cache, fake, w = env
    fake.bars = {"S": [t for t in grid(T0, 200) if not T0 + 1800 <= t < T0 + 2400]}
    # holiday: a real empty answer is covered and not asked again within the hour
    assert same(w, fake, start=T0 + 1800, end=T0 + 2400, limit=5000).empty
    n = len(fake.calls)
    clock.t += 60
    same(w, fake, start=T0 + 1800, end=T0 + 2400, limit=5000)
    assert len(fake.calls) == n
    # a failure answered as empty: served empty until the range expires, then heals
    fake.empty_once.add((T0 + 3000, T0 + 3600))
    assert w.candles("S", "1m", start=T0 + 3000, end=T0 + 3600, limit=5000).empty
    n = len(fake.calls)
    assert w.candles("S", "1m", start=T0 + 3000, end=T0 + 3600, limit=5000).empty
    assert len(fake.calls) == n
    clock.t += 3601
    got = same(w, fake, start=T0 + 3000, end=T0 + 3600, limit=5000)
    assert len(got) == 10
    assert ("S", "1m", cachemod.PAGE, T0 + 3000, T0 + 3600) in fake.calls[n:]


def test_limit_only_cold_then_warm(env):
    """Spec "First and second call" (R1): cold = newest-N, warm = one call from the run end."""
    clock, _cache, fake, w = env
    same(w, fake, limit=50)
    assert len(fake.calls) == 1
    assert fake.calls[0][2:] == (50, None, None)
    same(w, fake, limit=50)
    assert len(fake.calls) == 2
    assert fake.calls[1][3] == edge_1m(clock)


def test_hit_test_counts_only_the_run_at_the_edge(env):
    """cx_count: a hole in coverage is never spliced over."""
    _clock, _cache, fake, w = env
    same(w, fake, start=T0, end=T0 + 300, limit=5000)
    same(w, fake, limit=10)
    same(w, fake, limit=100)


def test_one_minute_chart_keeps_second_newest_bar(env):
    """cx_tail: settle = step = 60; the bar at the edge and the forming bar are served."""
    _clock, _cache, fake, w = env
    same(w, fake, start=T0, limit=5000)
    for _ in range(2):
        got = same(w, fake, limit=5000)
        assert {T0 + 99 * 60, T0 + 100 * 60} <= set(got["ts"])
    n = len(fake.calls)
    same(w, fake, limit=5000)
    assert len(fake.calls) == n + 1


def test_same_key_one_source_call(env):
    """Spec "Two threads, one key": one source call for the window."""
    _clock, _cache, fake, w = env
    fake.delay = 0.2
    out = []
    kw = {"start": T0, "end": T0 + 1800, "limit": 5000}
    ts = [threading.Thread(target=lambda: out.append(w.candles("S", "1m", **kw)))
          for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(fake.calls) == 1
    want = fake.candles("S", "1m", **kw)
    for got in out:
        assert_frame_equal(got.reset_index(drop=True), want, check_dtype=False)


def test_two_keys_do_not_wait(env):
    """Spec "Two keys do not serialise"."""
    clock, cache, _, _ = env
    started = threading.Event()

    class SlowS(Fake):
        def candles(self, symbol, *a, **k):
            if symbol == "S":
                started.set()
                time.sleep(2.0)
            return super().candles(symbol, *a, **k)

    fake = SlowS(clock, {"S": grid(T0, 200), "T": grid(T0, 200)})
    w = CachedProvider(fake, cache)
    th = threading.Thread(target=lambda: w.candles("S", "1m", start=T0, limit=50))
    th.start()
    assert started.wait(2)
    t = time.monotonic()
    w.candles("T", "1m", start=T0, limit=50)
    assert time.monotonic() - t < 1.0
    th.join()


def test_clear_waits_for_fill_and_fill_keeps_its_bars(env):
    """Spec "Clear during a fill", cx_lock."""
    clock, cache, fake, w = env
    same(w, fake, start=T0, end=T0 + 600, limit=5000)   # the key exists: clear must lock it
    fake.delay = 0.3
    kw = {"start": T0, "end": T0 + 1800, "limit": 5000}
    res = {}

    def fill():
        res["df"] = w.candles("S", "1m", **kw)
        res["done"] = time.monotonic()

    th = threading.Thread(target=fill)
    th.start()
    deadline = time.monotonic() + 2
    while not cache.lock(KEY).locked() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert cache.lock(KEY).locked()
    cache.clear("fake", "S", "1m")
    cleared = time.monotonic()
    th.join()
    assert res["done"] <= cleared
    fake.delay = 0
    want = fake.candles("S", "1m", **kw)
    assert_frame_equal(res["df"].reset_index(drop=True), want, check_dtype=False)
    assert cache.store.read(KEY, 0, 2**62).empty
    assert cache.store.coverage(KEY, clock()) == []


def test_clear_one_key_refetches_full_window(env):
    """Spec "Clear one key"."""
    _clock, cache, fake, w = env
    same(w, fake, start=T0, end=T0 + 1800, limit=5000)
    assert cache.clear("fake", "S", "1m") == 1
    n = len(fake.calls)
    same(w, fake, start=T0, end=T0 + 1800, limit=5000)
    assert fake.calls[n:] == [("S", "1m", cachemod.PAGE, T0, T0 + 1800)]


def test_bypass_tick_unknown_tf_end_only_and_off(env):
    """D7.1 bypass: tick, unknown timeframe, end without start, cache off."""
    clock, cache, fake, w = env
    same(w, fake, tf="tick", limit=10)
    assert fake.calls[-1] == ("S", "tick", 10, None, None)
    same(w, fake, tf="7m", start=T0, limit=10)
    assert fake.calls[-1] == ("S", "7m", 10, T0, None)
    same(w, fake, limit=10, end=T0 + 600)
    assert fake.calls[-1] == ("S", "1m", 10, None, T0 + 600)
    assert len(fake.calls) == 3
    assert cache.store.keys() == []
    off = CachedProvider(fake, CandleCache(store=None, snapshots=MemorySnapshots(), clock=clock))
    for kw in ({"start": T0, "limit": 50}, {"start": T0, "limit": 50}, {"limit": 50}):
        n = len(fake.calls)
        same(off, fake, **kw)
        assert fake.calls[n:] == [("S", "1m", 50, kw.get("start"), None)]


def test_sign_out_passes_through(env):
    """Spec "Covered window after sign-out"."""
    _clock, _cache, fake, w = env
    same(w, fake, start=T0, end=T0 + 1800, limit=5000)
    fake.ok = False
    with pytest.raises(RuntimeError, match="not configured"):
        w.candles("S", "1m", start=T0, end=T0 + 1800, limit=5000)


def test_wrapper_is_transparent(env):
    """Spec "Wrapper is transparent"."""
    _clock, _cache, fake, w = env
    assert (w.name, w.title, w.timeframes) == (fake.name, fake.title, fake.timeframes)
    assert w.configured() is True
    assert w.stream == fake.stream
    assert w.capabilities() == {"search", "candles"}
    assert hasattr(w, "is_custom")
    assert not hasattr(w, "no_such_attribute")


def test_locks_survive_reregistration(env):
    clock, cache, fake, w = env
    f2 = Fake(clock, {"S": grid(T0, 200)})
    w2 = CachedProvider(f2, cache)
    assert cache.lock(KEY) is cache.lock(KEY)
    same(w, fake, start=T0, end=T0 + 1800, limit=5000)
    same(w2, f2, start=T0, end=T0 + 1800, limit=5000)
    assert f2.calls == []


def test_cx_skip_accepted_hole(env):
    """cx_skip (accepted): a newest-N answer that drops a bar leaves a covered hole."""
    clock, cache, fake, w = env
    fake.drop = {T0 + 50 * 60}
    w.candles("S", "1m", limit=60)
    assert covered(cache.store, KEY, T0 + 50 * 60, clock())
    assert cache.store.read(KEY, T0 + 50 * 60, T0 + 50 * 60 + 1).empty


def test_cx_lag_accepted_partial_bar(env):
    """cx_lag (accepted): a source lagging more than settle gets a partial bar stored."""
    _clock, cache, fake, w = env
    fake.lag = 120
    w.candles("S", "1m", start=T0, limit=5000)
    assert (cache.store.read(KEY, 0, 2**62)["close"] < 60).any()


def test_cx_restate_served_until_clear(env):
    """cx_restate (accepted): a restated bar is served stale until a clear."""
    _clock, cache, fake, w = env
    same(w, fake, start=T0, end=T0 + 1800, limit=5000)
    fake.restated[T0] = 5.0
    got = w.candles("S", "1m", start=T0, end=T0 + 1800, limit=5000)
    assert got["close"].iloc[0] == 60.0
    cache.clear("fake", "S", "1m")
    assert same(w, fake, start=T0, end=T0 + 1800, limit=5000)["close"].iloc[0] == 65.0


def test_random_calls_match_unwrapped(env, monkeypatch):
    """I3 over many states: PAGE 7, a source that honours the contract (no cap)."""
    clock, _cache, fake, w = env
    monkeypatch.setattr(cachemod, "PAGE", 7)
    rng = random.Random(7)
    fake.cap = 10**9
    bars = set(grid(T0, 1200))
    for _ in range(3):
        a = rng.randint(0, 1100)
        bars -= set(grid(T0 + a * 60, rng.randint(1, 30)))
    fake.bars = {"S": sorted(bars)}
    for _ in range(400):
        clock.t += rng.choice([0, 1, 30, 59, 60, 61, 600])
        if rng.random() < 0.5:
            same(w, fake, limit=rng.randint(1, 300))
        else:
            ws = T0 + rng.randint(-120, 1200 * 60)
            we = None if rng.random() < 0.4 else ws + rng.randint(1, 400 * 60)
            same(w, fake, start=ws, end=we, limit=rng.randint(1, 300))


def test_repeat_chart_poll_is_one_call_per_poll(env):
    """R1: a 1m chart poll with limit > history costs one source call per poll."""
    clock, _cache, fake, w = env
    for _ in range(30):
        n = len(fake.calls)
        same(w, fake, limit=5000)
        assert len(fake.calls) == n + 1
        clock.t += 60


def test_daily_short_history_hits_from_second_call(env):
    """R1: a short newest-N answer is the whole history, so the next call is a hit."""
    clock, _cache, fake, w = env
    D0 = (T0 // 86400 - 299) * 86400
    fake.bars = {"S": grid(D0, 300, 86400)}
    clock.t = D0 + 299 * 86400 + 3600
    edge = int(clock()) - 60 - 86400 + 1
    same(w, fake, tf="1d", limit=5000)
    assert len(fake.calls) == 1
    for _ in range(3):
        n = len(fake.calls)
        same(w, fake, tf="1d", limit=5000)
        assert len(fake.calls) == n + 1
        assert fake.calls[-1][3] == edge


# ── fail open (R2, R3) and the _inner guard (R7) ─────────────────────────
class Broken:
    def __getattr__(self, name):
        def fail(*a, **k):
            raise sqlite3.OperationalError("disk I/O error")
        return fail


def test_store_errors_fail_open(caplog):
    clock = Clock(T0 + 100 * 60 + 30)
    fake = SnapFake(clock, {"S": grid(T0, 200)})
    w = CachedProvider(fake, CandleCache(store=Broken(), snapshots=Broken(), clock=clock))
    with caplog.at_level(logging.WARNING, logger="lse_terminal"):
        same(w, fake, start=T0, limit=50)
        same(w, fake, limit=50)
        assert w.option_chain("spy") == [{"u": "SPY", "n": 1}]
        assert w.option_chain("spy") == [{"u": "SPY", "n": 2}]
    assert len([r for r in caplog.records if "cache" in r.getMessage()]) == 1


def test_lock_timeout_passes_through(env, monkeypatch):
    _clock, cache, fake, w = env
    monkeypatch.setattr(cachemod, "LOCK_TIMEOUT", 0.1)
    held, release = threading.Event(), threading.Event()

    def hold():
        with cache.lock(KEY):
            held.set()
            release.wait(5)

    th = threading.Thread(target=hold)
    th.start()
    assert held.wait(2)
    try:
        same(w, fake, start=T0, limit=50)
        assert fake.calls == [("S", "1m", 50, T0, None)]
        assert cache.store.keys() == []
    finally:
        release.set()
        th.join()


def test_inner_guard_copy_does_not_recurse(env):
    _clock, _cache, _fake, w = env
    c = copy.copy(w)
    assert c.name == "fake"
    with pytest.raises(AttributeError):
        _ = CachedProvider.__new__(CachedProvider).name


# ── snapshots (D8) ───────────────────────────────────────────────────────
def test_snapshot_chain_key_is_canonical(senv):
    """Spec "Chain within TTL"."""
    _clock, _cache, fake, w = senv
    a = w.option_chain("spy")
    assert w.option_chain(" SPY ") == a
    assert len(fake.snap_calls) == 1


def test_snapshot_ttl_default_and_override(senv):
    """Spec "Provider override"."""
    clock, _cache, fake, w = senv
    w.option_chain("SPY")
    clock.t = T0 + 14
    w.option_chain("SPY")
    assert len(fake.snap_calls) == 1
    clock.t = T0 + 16
    w.option_chain("SPY")
    assert len(fake.snap_calls) == 2
    fake.snapshot_ttl = {"option_chain": 60}
    w.option_chain("QQQ")
    clock.t = T0 + 16 + 59
    w.option_chain("QQQ")
    assert len(fake.snap_calls) == 3


def test_snapshot_survives_restart(senv):
    """Spec "Snapshot survives restart"."""
    clock, cache, _fake, w = senv
    body = w.macro_catalog()
    f2 = SnapFake(clock, {})
    store2 = SqliteStore(cache.store.path)
    w2 = CachedProvider(f2, CandleCache(store=store2, snapshots=store2, clock=clock))
    clock.t += 30
    assert w2.macro_catalog() == body
    assert f2.snap_calls == []


def test_snapshot_sign_out_passes_through(senv):
    _clock, _cache, fake, w = senv
    w.option_chain("SPY")
    fake.ok = False
    with pytest.raises(RuntimeError, match="not configured"):
        w.option_chain("SPY")


def test_snapshot_memory_backend(senv):
    clock, _, fake, _ = senv
    w = CachedProvider(fake, CandleCache(store=None, snapshots=MemorySnapshots(), clock=clock))
    a = w.option_chain("SPY")
    clock.t = T0 + 14
    assert w.option_chain("SPY") is a          # the source's own object, not a copy
    clock.t = T0 + 16
    w.option_chain("SPY")
    assert len(fake.snap_calls) == 2


def test_snapshot_errors_are_not_cached(senv):
    _clock, _cache, fake, w = senv
    fake.fail_next = True
    with pytest.raises(RuntimeError, match="boom"):
        w.option_chain("SPY")
    assert w.option_chain("SPY") == [{"u": "SPY", "n": 2}]
    assert len(fake.snap_calls) == 2
