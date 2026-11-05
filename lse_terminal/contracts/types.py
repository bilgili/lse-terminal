"""Shared data types passed between providers, the engine, and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping, TypedDict

# The candle DataFrame contract. Every provider returns exactly these columns,
# in this order. `ts` is the bar-open time as integer epoch seconds (UTC);
# epoch integers rather than datetimes because they serialize losslessly and
# are what charting libraries consume directly.
CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]

# Bar length per fixed-length timeframe, in UI display order. "tick" is not
# here: a tick has no length, and the cache treats any label outside this
# table as not cacheable. Providers that serve tick add it themselves.
TIMEFRAME_SECONDS: Mapping[str, int] = MappingProxyType({
    "1s": 1, "30s": 30,
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
})


def to_epoch(bound: int | float | str | None) -> int | None:
    """A start/end bound as integer epoch seconds UTC.

    Accepts an int/float, None, or a string. A string is an ASCII digit
    string (optional leading "-"), a date-only string, or what
    datetime.isoformat() emits, plus a "Z" suffix. Exactly 8 ASCII digits
    are a YYYYMMDD UTC date, not epoch seconds (strptime, so this does not
    depend on the Python version); any other digit string is epoch seconds.
    Float strings are not accepted. A naive value is UTC. Empty or whitespace-only is None.
    """
    if isinstance(bound, bool):
        raise TypeError("bound must not be bool")
    if bound is None:
        return None
    if isinstance(bound, (int, float)):
        return int(bound)
    s = str(bound).strip()
    if not s:
        return None
    if len(s) == 8 and s.isascii() and s.isdigit():
        return int(datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())
    d = s.lstrip("-")
    if d.isascii() and d.isdigit():
        return int(s)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"unparseable bound: {bound!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())



@dataclass
class Instrument:
    """One tradeable/chartable instrument as a provider exposes it."""

    symbol: str
    name: str = ""
    category: str = ""
    provider: str = ""
    # Provider-private extras (dataset hints, exchange codes, ...). The engine
    # passes this back on candle/stream calls but never interprets it.
    meta: dict = field(default_factory=dict)


@dataclass
class Quote:
    """A point-in-time price for one instrument."""

    symbol: str
    price: float
    ts: float
    bid: float | None = None
    ask: float | None = None


# Row types, one per data surface. Required keys are the keys the UI or
# engine reads that the recorded LSE fixture (tests/fixtures/lse_rows.json)
# carries; the compliance harness reads __required_keys__. A private
# total=True base holds the required keys and the public total=False class
# the optional ones, so type checkers and the runtime agree.
class _PriceRequired(TypedDict):
    symbol: str
    price: float


class PriceRow(_PriceRequired, total=False):
    bid: float | None
    ask: float | None
    ts: float


class _UnderlyingRequired(TypedDict):
    symbol: str


class UnderlyingRow(_UnderlyingRequired, total=False):
    name: str


class _ChainRequired(TypedDict):
    contract_type: str      # "call" | "put"
    strike: float
    expiry: str             # YYYY-MM-DD
    last_price: float


class ChainRow(_ChainRequired, total=False):
    ticker: str
    # The chain feed carries greeks_underlying_price; the UI reads
    # underlying_price and guards the miss. Declared so a source that has it can send it.
    underlying_price: float
    iv: float
    volume_today: float
    premium_today: float


class _FlowRequired(TypedDict):
    contract_type: str
    strike: float
    expiry: str
    premium: float


class FlowRow(_FlowRequired, total=False):
    ticker: str
    iv: float
    underlying_price: float


class _CalendarRequired(TypedDict):
    datetime: str
    event: str


class CalendarRow(_CalendarRequired, total=False):
    region_code: str
    actual: str | None
    consensus: str | None
    forecast: str | None


class MacroCatalogRow(TypedDict):
    symbol: str
    name: str


class MacroSeriesRow(TypedDict):
    date: str
    value: float


ROW_TYPES: Mapping[str, type] = MappingProxyType({
    "prices": PriceRow,
    "options_underlyings": UnderlyingRow,
    "option_chain": ChainRow,
    "options_flow": FlowRow,
    "economic_calendar": CalendarRow,
    "macro_catalog": MacroCatalogRow,
    "macro_series": MacroSeriesRow,
})


# The screener snapshot is a dict with exactly these top-level keys.
SCREENER_KEYS = frozenset({"columns", "data"})


class LogoRow(TypedDict):
    """One value of logos(): the logo URLs for a symbol."""
    light: str
    dark: str
