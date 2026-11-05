"""Read-through market-data cache (design D5 to D8).

CachedProvider wraps one provider. It is NOT a Provider subclass: every
attribute it does not define comes from the wrapped provider through
__getattr__, so name, timeframes, configured(), capabilities(), quote
and stream are the source's own. The registry owns one CandleCache that
holds the stores, the per-key locks and the clock; every wrapper shares
it, so a re-registered provider (set_key) keeps its locks.

Fill rules are D7, checked by openspec/changes/data-source-contract-cache/
design.tla (flags FixEdge, FixTail, FixTrunc, FixCount, FixLock, PlanWin,
PlanMissAsIs, TailFromRun, FixHit). Change them only with the model.

The cache fails open: a store error (sqlite3.Error), a bound the cache
cannot parse, or a key lock that does not come free in LOCK_TIMEOUT
seconds passes the call to the source.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time

import pandas as pd
from fastapi.encoders import jsonable_encoder

from lse_terminal.contracts import CANDLE_COLUMNS, TIMEFRAME_SECONDS, to_epoch

log = logging.getLogger("lse_terminal")

PAGE = 5000            # rows the wrapper asks the source for per page (model Cap)
EMPTY_TTL = 3600       # an empty answer's coverage expires after this (D6)
LOCK_TIMEOUT = 30      # seconds a call waits for its key lock, then passes through
HISTORY = -(2 ** 62)   # coverage start for "the source holds nothing older"
HISTORY_TTL = 86400    # a short newest-N answer's "nothing older" claim expires after this

DEFAULT_TTL = {        # D8; logos are not cached (PR 3 review R5)
    "options_underlyings": 60, "option_chain": 15, "options_flow": 15,
    "economic_calendar": 60, "macro_catalog": 60, "macro_series": 600,
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
        self._warned = False

    def lock(self, key: tuple) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def store_failed(self, exc: Exception) -> None:
        """A store error: the caller passes through. Logged once per cache."""
        if not self._warned:
            self._warned = True
            log.warning("market-data cache store failed, passing calls through: %s", exc)

    def clear(self, provider=None, symbol=None, timeframe=None) -> dict:
        """Drop matching candle keys, each under its own lock (D7.8, PR 3
        amendment 4). Snapshots only when no symbol/timeframe filter.

        Returns {"cleared": n, "skipped": [[provider, symbol, tf], ...]}.
        n counts keys the store listed; a key with only a lock (a first-ever
        fill) is cleared under its lock but not counted. A key whose lock
        does not come free in LOCK_TIMEOUT is skipped, not cleared."""
        n, skipped = 0, []
        if self.store is not None:
            listed = set(self.store.keys())
            with self._guard:
                filling = list(self._locks)       # a first-ever fill has a lock, no rows
            for key in sorted({*listed, *filling}):
                if ((provider is None or key[0] == provider)
                        and (symbol is None or key[1] == symbol)
                        and (timeframe is None or key[2] == timeframe)):
                    lk = self.lock(key)
                    if not lk.acquire(timeout=LOCK_TIMEOUT):                 # FixLock
                        skipped.append(list(key))
                        continue
                    try:
                        self.store.clear(key)
                    finally:
                        lk.release()
                    n += key in listed
        if symbol is None and timeframe is None:
            self.snapshots.snapshot_clear(provider)
        return {"cleared": n, "skipped": skipped}


class CachedProvider:
    def __init__(self, inner, cache: CandleCache):
        self._inner = inner
        self._cache = cache

    def __getattr__(self, name):            # only for names not defined here
        if name == "_inner":                # unset (copy/pickle): no recursion
            raise AttributeError(name)
        return getattr(self._inner, name)

    # ── candles (D7) ──────────────────────────────────────────────────
    def candles(self, symbol: str, timeframe: str, limit: int = 500,
                start: str | None = None, end: str | None = None) -> pd.DataFrame:
        inner, c = self._inner, self._cache
        step = TIMEFRAME_SECONDS.get(timeframe)
        if c.store is None or step is None or not inner.configured() or limit < 1:   # D7.1
            return inner.candles(symbol, timeframe, limit=limit, start=start, end=end)
        try:
            ws, we = to_epoch(start), to_epoch(end)
        except (ValueError, TypeError):     # the source owns its own bound errors
            return inner.candles(symbol, timeframe, limit=limit, start=start, end=end)
        if we is not None and ws is None:                                     # D7.1
            return inner.candles(symbol, timeframe, limit=limit, start=start, end=end)
        key = (inner.name, symbol, timeframe)
        lk = c.lock(key)
        if lk.acquire(timeout=LOCK_TIMEOUT):                                 # FixLock, R3
            try:
                now = int(c.clock())
                settle = min(step, int(inner.settle_seconds))
                edge = now - settle - step + 1                               # FixEdge
                if ws is None:
                    return self._limit_only(key, symbol, timeframe, int(limit), now, edge)
                return self._windowed(key, symbol, timeframe, ws, we, int(limit), now, edge)
            except sqlite3.Error as e:                                       # R2
                c.store_failed(e)
            finally:
                lk.release()
        else:
            log.warning("cache lock for %s busy for %ss, passing through", key, LOCK_TIMEOUT)
        return inner.candles(symbol, timeframe, limit=limit, start=start, end=end)

    def _ask(self, symbol, tf, *, limit, start=None, end=None):
        """The only source call on the cached paths. Returns (raw, df): raw is
        the source answer as returned; df is raw sorted by ts, deduplicated on
        ts (last wins) and restricted to [start, end) when given. Truncation is
        judged on raw; only df is ever stored."""
        raw = self._inner.candles(symbol, tf, limit=limit, start=start, end=end)
        df = raw[CANDLE_COLUMNS].sort_values("ts", kind="stable")
        df = df.drop_duplicates("ts", keep="last")
        if start is not None:
            df = df[df["ts"] >= start]
        if end is not None:
            df = df[df["ts"] < end]
        return raw, df.reset_index(drop=True)

    def _fill(self, key, symbol, tf, g, h, ws, limit, now):
        """Fill gap [g, h) page by page (D7.3)."""
        st = self._cache.store
        while g < h:
            if st.count(key, ws, g) >= limit:                                # PlanWin stop
                return
            raw, df = self._ask(symbol, tf, limit=PAGE, start=g, end=h)
            if len(raw) < PAGE:                                              # complete
                # ponytail: each empty range gets its own empty_until, so off-hours
                # polls add about one row per poll-minute per key until they expire
                # (pruned on write). Snap empty_until to a coarse grid if it grows.
                st.write_page(key, df, g, h, None if len(df) else now + EMPTY_TTL, now)  # D7.5
                return
            # FixTrunc: next page after the newest bar the source returned, not
            # the newest kept one (a source that floors start returns bars < g).
            last = min(int(raw["ts"].max()) + 1, h)
            if last <= g:          # a truncated page with nothing at or past g: no progress
                return
            st.write_page(key, df, g, last, None, now)
            g = last

    def _windowed(self, key, symbol, tf, ws, we, limit, now, edge):
        st = self._cache.store
        wc = edge if we is None else min(we, edge)
        if ws < wc:
            for g, h in gaps(runs(st.coverage(key, now)), ws, wc):
                self._fill(key, symbol, tf, g, h, ws, limit, now)
        rend = run_end(runs(st.coverage(key, now)), ws) if ws < wc else ws
        body = st.read(key, ws, min(rend, wc))                               # PlanWin
        tail = None
        if (we is None or we > edge) and rend >= wc and limit > len(body):  # FixTail
            _, tail = self._ask(symbol, tf, limit=limit - len(body),
                                start=max(edge, ws), end=we)
        return _cat(body, tail).head(limit).reset_index(drop=True)

    def _limit_only(self, key, symbol, tf, limit, now, edge):
        st = self._cache.store
        rs = runs(st.coverage(key, now))
        if rs and rs[-1][1] <= edge:                                         # FixCount
            c0, c = rs[-1]
            raw, tail = self._ask(symbol, tf, limit=PAGE, start=c)          # TailFromRun
            if len(raw) < PAGE:                        # complete since c
                closed = tail[tail["ts"] < edge][CANDLE_COLUMNS]
                if len(closed):                        # never an empty range here
                    st.write_page(key, closed, c, edge, None, now)
                pool = _cat(st.read(key, c0, edge if len(closed) else c),
                            tail[tail["ts"] >= edge])
                if len(pool) >= limit or c0 == HISTORY:                      # FixHit
                    return pool.tail(limit).reset_index(drop=True)
        raw, df = self._ask(symbol, tf, limit=limit)                         # PlanMissAsIs
        closed = df[df["ts"] < edge]
        if len(closed):
            first = int(closed["ts"].iloc[0])
            st.write_page(key, closed, first, edge, None, now)
            if len(raw) < limit:                                             # FixHit (raw, as H1)
                # A short answer is the whole history, but a source may backfill
                # later: claim "nothing older" only for HISTORY_TTL. runs() joins
                # this empty range to [first, edge), so c0 == HISTORY hits until it
                # expires; then the run starts at first and the next call misses
                # and heals. Expiry only removes coverage, so I1-I3 still hold.
                st.write_page(key, _empty(), HISTORY, first, now + HISTORY_TTL, now)
        return raw.reset_index(drop=True)

    # ── snapshots (D8) ────────────────────────────────────────────────
    def _snap(self, method: str, canon: list, call):
        inner, c = self._inner, self._cache
        if not inner.configured():                                           # D5 sign-out
            return call()
        key = json.dumps([inner.name, method, *canon], default=str)
        now = c.clock()
        try:
            hit, body = c.snapshots.snapshot_get(key, now)
        except (sqlite3.Error, ValueError) as e:                             # R2
            c.store_failed(e)
            return call()
        if hit:
            return body
        body = call()
        # One encode path for both backends: a miss returns the same decoded
        # types as a hit (datetimes as ISO strings, int keys as str).
        try:
            encoded = json.dumps(jsonable_encoder(body))
            c.snapshots.snapshot_put(key, encoded, now,
                                     (inner.snapshot_ttl or {}).get(method, DEFAULT_TTL[method]))
        except (sqlite3.Error, ValueError, TypeError) as e:                  # R2
            c.store_failed(e)
            return body
        return json.loads(encoded)

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
