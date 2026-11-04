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

PROBE_LIMIT = 5


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
    # Without an explicit timeframe, take the first that serves rows: some sources
    # only serve each symbol's native timeframe.
    tfs = [timeframe] if timeframe else list(provider.timeframes) or [""]
    first_err = None
    for tf in tfs:
        try:
            df = provider.candles(sym, tf, limit=50)
        except Exception as e:  # noqa: BLE001 - harness reports, never crashes
            first_err = first_err or f"candles({sym!r}, {tf!r}) raised: {e}"
            continue
        if len(df) >= 1 or tf == tfs[-1] or timeframe:
            first_err = None
            break
    if first_err:
        return problems + [first_err]

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
        try:
            df2 = provider.candles(sym, tf, limit=50)
        except Exception as ex:  # noqa: BLE001 - harness reports, never crashes
            problems.append(f"candles({sym!r}, {tf!r}, limit=50) raised on the "
                            f"determinism re-call: {ex!r}")
        else:
            if not df.equals(df2):
                problems.append("deterministic=True but two identical calls differ")

    problems += _check_candle_clauses(provider, sym, tf, df)
    problems += _check_capabilities(provider, sym)
    return problems


def _check_candle_clauses(provider: Provider, sym: str, tf: str, df) -> list[str]:
    """Half-open bounds and prefix completeness, the clauses the cache needs."""
    if len(df) < 3:
        msg = (f"candle clauses not checked: need at least 3 bars for {sym} {tf}; "
               "pass symbol=/timeframe= with a longer series")
        return [msg]
    out: list[str] = []
    ts = [int(t) for t in df["ts"]]
    s, e = ts[0], ts[2]
    call = f"candles({sym!r}, {tf!r}, limit=50, start={s}, end={e})"
    try:
        got = {int(t) for t in provider.candles(
            sym, tf, limit=50, start=str(s), end=str(e))["ts"]}
    except Exception as ex:  # noqa: BLE001 - harness reports, never crashes
        return [f"{call} raised: {ex!r}"]
    if e in got:
        out.append("end is exclusive: a bar at ts == end was returned")
    if s not in got:
        out.append("start is inclusive: the bar at ts == start is missing")
    call = f"candles({sym!r}, {tf!r}, limit=2 and limit=10, start={s})"
    try:
        small = list(provider.candles(sym, tf, limit=2, start=str(s))["ts"])
        big = list(provider.candles(sym, tf, limit=10, start=str(s))["ts"])
    except Exception as ex:  # noqa: BLE001
        return out + [f"{call} raised: {ex!r}"]
    if small != big[:len(small)]:
        out.append("windowed answer with a smaller limit is not a prefix "
                   "of the same call with a larger limit")
    return out


# dependent method -> listing method that supplies its first symbol
_DEPENDS_ON = {"option_chain": "options_underlyings", "macro_series": "macro_catalog"}
_LISTINGS = ("options_underlyings", "macro_catalog")
_LIMIT_CHECKED = ("options_flow", "economic_calendar", "option_chain", "macro_series")


def _probe(provider: Provider, method: str, sym: str, listed: str | None = None):
    """One call per method. `listed` is the first symbol of the dependent's listing."""
    arg = listed or sym
    if method == "prices":
        return provider.prices([sym])
    if method == "option_chain":
        return provider.option_chain(arg, limit=PROBE_LIMIT)
    if method == "macro_series":
        return provider.macro_series(arg, limit=PROBE_LIMIT)
    if method in ("options_flow", "economic_calendar"):
        return getattr(provider, method)(limit=PROBE_LIMIT)
    return getattr(provider, method)()


def _check_capabilities(provider: Provider, sym: str) -> list[str]:
    out: list[str] = []
    caps = provider.capabilities()
    # listing method -> first symbol; "" when the listing is empty, None when unusable
    first_symbol: dict[str, str | None] = {}
    for cap, methods in Provider._CAPABILITY_METHODS.items():
        if cap in ("quote", "stream") or cap not in caps:
            continue
        for m in methods:
            listed = None
            dep = _DEPENDS_ON.get(m)
            if dep:
                if first_symbol.get(dep, None) is None:
                    out.append(f"{m}: not checked: {dep} returned no usable symbol")
                    continue
                listed = first_symbol[dep] or None
            try:
                res = _probe(provider, m, sym, listed)
            except NotSupported:
                out.append(f"{m}: claimed via capability {cap!r} but raised NotSupported")
                first_symbol[m] = None
                continue
            except Exception as ex:  # noqa: BLE001 - harness reports, never crashes
                out.append(f"{m}: raised {type(ex).__name__}: {ex}")
                first_symbol[m] = None
                continue
            if m == "screener":
                if not isinstance(res, dict) or not {"columns", "data"} <= set(res):
                    out.append("screener: must return a dict with 'columns' and 'data'")
                continue
            if m == "logos":
                if not isinstance(res, dict):
                    out.append("logos: must return a dict")
                continue
            if not isinstance(res, list):
                out.append(f"{m}: must return a list of rows")
                first_symbol[m] = None
                continue
            if m in _LIMIT_CHECKED and len(res) > PROBE_LIMIT:
                out.append(f"{m}: limit={PROBE_LIMIT} not respected: got {len(res)} rows")
            req = ROW_TYPES[m].__required_keys__
            ok = True
            for row in res[:PROBE_LIMIT]:
                if not isinstance(row, dict):
                    out.append(f"{m}: row is not a dict: {row!r}")
                    ok = False
                    break
                missing = req - set(row)
                if missing:
                    out.append(f"{m}: row lacks required keys {sorted(missing)}")
                    ok = False
                    break
            if m in _LISTINGS:
                if not res:
                    first_symbol[m] = ""
                elif ok and "symbol" in res[0]:
                    first_symbol[m] = str(res[0]["symbol"])
                else:
                    first_symbol[m] = None
    return out
