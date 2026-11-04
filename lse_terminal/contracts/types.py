"""Shared data types passed between providers, the engine, and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

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
