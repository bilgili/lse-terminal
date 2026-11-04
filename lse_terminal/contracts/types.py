"""Shared data types passed between providers, the engine, and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypedDict

# The candle DataFrame contract. Every provider returns exactly these columns,
# in this order. `ts` is the bar-open time as integer epoch seconds (UTC);
# epoch integers rather than datetimes because they serialize losslessly and
# are what charting libraries consume directly.
CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]

# Bar length per fixed-length timeframe, in UI display order. "tick" is not
# here: a tick has no length, and the cache treats any label outside this
# table as not cacheable. Providers that serve tick add it themselves.
TIMEFRAME_SECONDS: dict[str, int] = {
    "1s": 1, "30s": 30,
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
}


def to_epoch(bound: int | float | str | None) -> int | None:
    """A start/end bound as integer epoch seconds UTC.

    Accepts an int/float, None, or a string. A string is an ASCII digit
    string (optional leading "-"), a date-only string, or what
    datetime.isoformat() emits, plus a "Z" suffix. Float strings are not
    accepted. A naive value is UTC. Empty or whitespace-only is None.
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


# Required keys per surface: the keys the UI or engine reads that the
# recorded LSE fixture (tests/fixtures/lse_rows.json) carries. total=False
# lets a row carry extras; __required_keys__ is what the harness checks.
# Set explicitly because Python 3.10 has no typing.Required.
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
