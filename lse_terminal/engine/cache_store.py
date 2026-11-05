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

from lse_terminal.contracts import CANDLE_COLUMNS

Key = tuple[str, str, str]  # (provider, symbol, timeframe)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  provider TEXT, symbol TEXT, tf TEXT, ts INTEGER,
  open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (provider, symbol, tf, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS coverage (
  provider TEXT, symbol TEXT, tf TEXT, start INTEGER, "end" INTEGER,
  empty_until INTEGER);
CREATE INDEX IF NOT EXISTS coverage_key ON coverage (provider, symbol, tf, start);
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
        # SQLite stores NaN as NULL; read it back as NaN in float columns.
        df[CANDLE_COLUMNS[1:]] = df[CANDLE_COLUMNS[1:]].astype("float64")
        return df

    def count(self, key: Key, a: int, b: int) -> int:
        """Stored bars with a <= ts < b."""
        return self._conn().execute(
            "SELECT COUNT(*) FROM bars WHERE provider=? AND symbol=? AND tf=? "
            "AND ts>=? AND ts<?", (*key, a, b)).fetchone()[0]

    def coverage(self, key: Key, now: int) -> list[tuple[int, int, int | None]]:
        """Active ranges, ascending: empty_until NULL or still in the future."""
        return [tuple(r) for r in self._conn().execute(
            'SELECT start, "end", empty_until FROM coverage WHERE provider=? AND symbol=? '
            "AND tf=? AND (empty_until IS NULL OR empty_until>?) ORDER BY start, empty_until", (*key, now))]

    def write_page(self, key: Key, df: pd.DataFrame, s: int, e: int,
                   empty_until: int | None, now: int) -> None:
        """One transaction: upsert the page's bars, then add coverage [s, e)
        merged with overlapping or adjacent ranges of the same kind. Expired
        empty ranges of the key are dropped on the way.

        Prices and volume are coerced to float (a value that is not a number,
        such as None, is stored as NULL). A ts that is not an integer value
        raises sqlite3.InterfaceError, so the caller's fail-open path takes it."""
        ts = pd.to_numeric(df["ts"], errors="coerce")
        if not (ts % 1 == 0).all():                 # NaN and inf fail this too
            raise sqlite3.InterfaceError("bar ts is not an integer epoch second")
        vals = [pd.to_numeric(df[k], errors="coerce").astype("float64").tolist()
                for k in CANDLE_COLUMNS[1:]]
        rows = [(*key, *r) for r in zip(ts.astype("int64").tolist(), *vals)]
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            if rows:
                c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?)", rows)
            c.execute("DELETE FROM coverage WHERE provider=? AND symbol=? AND tf=? "
                      "AND empty_until IS NOT NULL AND empty_until<=?", (*key, now))
            same = ("empty_until IS NULL" if empty_until is None else "empty_until=?")
            args = () if empty_until is None else (empty_until,)
            rows = c.execute(
                f'SELECT rowid, start, "end" FROM coverage WHERE provider=? AND symbol=? AND tf=? '
                f'AND {same} AND start<=? AND "end">=?', (*key, *args, e, s)).fetchall()
            lo = min([s, *(r[1] for r in rows)])
            hi = max([e, *(r[2] for r in rows)])
            c.executemany("DELETE FROM coverage WHERE rowid=?", [(r[0],) for r in rows])
            c.execute("INSERT INTO coverage VALUES (?,?,?,?,?,?)",
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

    def snapshot_put(self, key: str, encoded: str, now: float, ttl: float) -> None:
        """encoded: the JSON text of the body (the wrapper encodes)."""
        c = self._conn()
        c.execute("DELETE FROM snapshots WHERE expires<=?", (now,))
        c.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
                  (key, now, now + ttl, encoded))

    def snapshot_clear(self, provider: str | None = None) -> None:
        if provider is None:
            self._conn().execute("DELETE FROM snapshots")
        else:
            # substr, not LIKE: provider names may hold the LIKE wildcards _ and %.
            p = json.dumps([provider])[:-1] + ","
            self._conn().execute("DELETE FROM snapshots WHERE substr(key, 1, ?) = ?",
                                 (len(p), p))

    def stats(self) -> dict:
        c = self._conn()
        out = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
               for t in ("bars", "coverage", "snapshots")}
        out["bytes"] = self.path.stat().st_size if self.path.exists() else 0
        return out


class MemorySnapshots:
    """Snapshot TTL cache for hosted mode and LSE_TERMINAL_CACHE=off: the
    same interface as SqliteStore's snapshot half, in process memory,
    bounded like the route caches it replaces. It keeps the JSON text and
    decodes a fresh copy on every get, so no caller shares a mutable body."""

    def __init__(self, bound: int = 512):
        self.bound = bound
        self._d: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def snapshot_get(self, key: str, now: float):
        with self._lock:
            hit = self._d.get(key)
        if hit is None or hit[0] <= now:
            return False, None
        return True, json.loads(hit[1])

    def snapshot_put(self, key: str, encoded: str, now: float, ttl: float) -> None:
        with self._lock:
            for k in [k for k, v in self._d.items() if v[0] <= now]:
                del self._d[k]
            if key not in self._d and len(self._d) >= self.bound:
                del self._d[min(self._d, key=lambda k: self._d[k][0])]
            self._d[key] = (now + ttl, encoded)

    def snapshot_clear(self, provider: str | None = None) -> None:
        prefix = None if provider is None else json.dumps([provider])[:-1] + ","
        with self._lock:
            for k in [k for k in self._d if prefix is None or k.startswith(prefix)]:
                del self._d[k]
