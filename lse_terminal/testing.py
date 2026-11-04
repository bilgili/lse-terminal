"""Compliance harness for third-party (and built-in) providers.

Plugin authors run this against their provider before publishing::

    from lse_terminal.testing import check_provider
    problems = check_provider(MyProvider())
    assert not problems, problems

The same checks gate built-in providers in this repo's test suite, so the
contract can't drift from what the harness enforces.
"""

from __future__ import annotations

from lse_terminal.contracts import (
    CANDLE_COLUMNS,
    ROW_TYPES,
    Instrument,
    NotSupported,
    Provider,
)


def check_provider(provider: Provider, symbol: str | None = None,
                   timeframe: str | None = None) -> list[str]:
    """Returns a list of human-readable contract violations (empty = compliant)."""
    problems: list[str] = []

    if not provider.name:
        problems.append("provider.name is empty")
    if not provider.timeframes:
        problems.append("provider.timeframes is empty")

    try:
        results = provider.search("", limit=5)
    except Exception as e:
        return problems + [f"search() raised: {e}"]
    if not results:
        return problems + ["search('') returned nothing; a default list is required"]
    for ins in results:
        if not isinstance(ins, Instrument):
            problems.append(f"search() returned non-Instrument: {ins!r}")

    sym = symbol or results[0].symbol
    tf = timeframe or provider.timeframes[0]
    try:
        df = provider.candles(sym, tf, limit=50)
    except Exception as e:
        return problems + [f"candles({sym!r}, {tf!r}) raised: {e}"]

    if list(df.columns) != CANDLE_COLUMNS:
        problems.append(f"candles columns {list(df.columns)} != {CANDLE_COLUMNS}")
        return problems
    if len(df) == 0:
        problems.append(f"candles({sym!r}, {tf!r}) returned zero rows")
        return problems
    if len(df) > 50:
        problems.append(f"limit=50 not respected: got {len(df)} rows")
    ts = df["ts"]
    if not ts.is_monotonic_increasing:
        problems.append("ts is not ascending")
    if ts.duplicated().any():
        problems.append("duplicate ts values")
    if df[["open", "high", "low", "close"]].isna().any().any():
        problems.append("NaN in ohlc")
    bad_hi = (df["high"] + 1e-12 < df[["open", "close"]].max(axis=1)).sum()
    bad_lo = (df["low"] - 1e-12 > df[["open", "close"]].min(axis=1)).sum()
    if bad_hi:
        problems.append(f"{bad_hi} bars with high < max(open, close)")
    if bad_lo:
        problems.append(f"{bad_lo} bars with low > min(open, close)")

    if provider.deterministic:
        df2 = provider.candles(sym, tf, limit=50)
        if not df.equals(df2):
            problems.append("deterministic=True but two identical calls differ")

    problems += _check_candle_clauses(provider, sym, tf, df)
    problems += _check_capabilities(provider, sym)
    return problems


def _check_candle_clauses(provider: Provider, sym: str, tf: str, df) -> list[str]:
    """Half-open bounds and prefix completeness, the clauses the cache needs."""
    out: list[str] = []
    if len(df) < 3:
        return out
    ts = [int(t) for t in df["ts"]]
    s, e = ts[0], ts[2]
    try:
        win = provider.candles(sym, tf, limit=50, start=str(s), end=str(e))
    except Exception as ex:  # noqa: BLE001 - harness reports, never crashes
        return [f"windowed candles raised: {ex}"]
    got = {int(t) for t in win["ts"]}
    if e in got:
        out.append("end is exclusive: a bar at ts == end was returned")
    if s not in got:
        out.append("start is inclusive: the bar at ts == start is missing")
    try:
        small = provider.candles(sym, tf, limit=2, start=str(s))
        big = provider.candles(sym, tf, limit=10, start=str(s))
    except Exception as ex:  # noqa: BLE001
        return out + [f"windowed candles raised: {ex}"]
    if list(small["ts"]) != list(big["ts"])[:len(small)]:
        out.append("windowed answer with a smaller limit is not a prefix "
                   "of the same call with a larger limit")
    return out


def _probe(provider: Provider, method: str, sym: str):
    """One call per method; the first listed underlying/series feeds the per-symbol calls."""
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


_LIMITED = ("options_flow", "economic_calendar", "option_chain", "macro_series")


def _check_capabilities(provider: Provider, sym: str) -> list[str]:
    out: list[str] = []
    caps = provider.capabilities()
    for cap, methods in Provider._CAPABILITY_METHODS.items():
        if cap in ("quote", "stream") or cap not in caps:
            continue
        for m in methods:
            try:
                res = _probe(provider, m, sym)
            except NotSupported:
                out.append(f"{m}: claimed via capability {cap!r} but raised NotSupported")
                continue
            except Exception as ex:  # noqa: BLE001 - harness reports, never crashes
                out.append(f"{m}: raised {type(ex).__name__}: {ex}")
                continue
            if m == "screener":
                if not isinstance(res, dict) or not {"columns", "data"} <= set(res):
                    out.append("screener: must return a dict with 'columns' and 'data'")
                continue
            if m == "logos":
                if not isinstance(res, dict):
                    out.append("logos: must return a symbol -> {light, dark} map")
                continue
            if not isinstance(res, list):
                out.append(f"{m}: must return a list of rows")
                continue
            if m in _LIMITED and len(res) > 5:
                out.append(f"{m}: limit=5 not respected: got {len(res)} rows")
            req = ROW_TYPES[m].__required_keys__
            for row in res[:5]:
                missing = req - set(row)
                if missing:
                    out.append(f"{m}: row lacks required keys {sorted(missing)}")
                    break
    return out
