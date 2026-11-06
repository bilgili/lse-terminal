"""The data provider contract.

A provider is the terminal's window onto one data source: the LSE vault, a
folder of CSVs, an exchange, a broker. Implement this class in any pip
package, expose it under the ``lse_terminal.providers`` entry-point group,
and the terminal discovers it automatically.

Only ``search`` and ``candles`` are mandatory. Everything else degrades
gracefully in the UI when it raises :class:`NotSupported`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from types import MappingProxyType

import pandas as pd

from lse_terminal.contracts.types import Instrument, Quote


class NotSupported(Exception):
    """Raised by optional provider methods the source cannot serve."""


# Capability -> the words after the provider name in the NotSupported
# message of its default method. The engine reuses them for its 404.
UNSUPPORTED: Mapping[str, str] = MappingProxyType({
    "prices": "has no price board",
    "screener": "has no screener feed",
    "logos": "has no logos",
    "options": "has no options",
    "flow": "has no options flow",
    "calendar": "has no economic calendar",
    "macro": "has no macro data",
    "stream": "does not stream",
})


class Provider(ABC):
    # Machine identifier, unique across installed providers (used in URLs).
    name: str = ""
    # Human label shown in the UI.
    title: str = ""
    # Timeframes this source can serve, in UI display order.
    timeframes: list[str] = ["1m", "5m", "15m", "1h", "4h", "1d"]
    # Providers that return identical data for identical calls (used by the
    # compliance harness to enable determinism checks).
    deterministic: bool = False
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
    # Read-only default: a subclass sets its own mapping, never mutates this.
    snapshot_ttl: Mapping[str, int] = MappingProxyType({})

    @abstractmethod
    def search(self, query: str = "", limit: int = 50) -> list[Instrument]:
        """Instruments matching ``query`` (empty query = a sensible default list).

        Presentation contract (the translator layer): the UI renders whatever
        arrives here with NO source-specific rules of its own, so a provider
        adapting a new broker/vendor must normalize to this shape:
        - ``category`` is a display-ready group label ("Forex", "US Stocks").
        - ``name`` is the instrument's human display name ("Euro / US Dollar",
          "Apple Inc."); empty only when the source truly has none.
        - For an empty query, results arrive grouped contiguously by category,
          groups and rows already in the order the source wants them shown.
          The sidebar renders that order verbatim, one collapsible folder per
          category.
        """

    @abstractmethod
    def candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
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

    def quote(self, symbol: str) -> Quote:
        raise NotSupported(f"{self.name} does not serve quotes")

    def stream(self, symbols: list[str]) -> AsyncIterator[dict]:
        """Async iterator of tick dicts: {symbol, price, ts, bid?, ask?, volume?}.

        Must not catch ``CancelledError`` and continue: the engine cancels the
        task that waits in ``__anext__`` when the tab closes, then awaits it.
        """
        raise NotSupported(f"{self.name} {UNSUPPORTED['stream']}")

    def prices(self, symbols: list[str]) -> list[dict]:
        """Latest price per symbol. Rows: ``PriceRow``."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['prices']}")

    def screener(self) -> dict:
        """Cross-sectional snapshot ``{"columns": [...], "data": [...]}`` (plus ``as_of``, ``count``)."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['screener']}")

    def logos(self) -> dict:
        """Symbol -> ``{"light": url, "dark": url}``."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['logos']}")

    def options_underlyings(self) -> list[dict]:
        """Optionable underlyings. Rows: ``UnderlyingRow``."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['options']}")

    def option_chain(self, underlying: str, type: str | None = None,
                     expiry: str | None = None, limit: int = 5000) -> list[dict]:
        """Current chain for one underlying. Rows: ``ChainRow``."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['options']}")

    def options_flow(self, underlying: str | None = None, type: str | None = None,
                     min_premium: float | None = None, limit: int = 300) -> list[dict]:
        """Recent option prints, newest first. Rows: ``FlowRow``."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['flow']}")

    def economic_calendar(self, region=None, event: str | None = None,
                          start: str | None = None, end: str | None = None,
                          released_only: bool = False, order: str = "asc",
                          limit: int = 5000) -> list[dict]:
        """Macro release events. Rows: ``CalendarRow``."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['calendar']}")

    def macro_catalog(self) -> list[dict]:
        """Every macro series the source holds. Rows: ``MacroCatalogRow``."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['macro']}")

    def macro_series(self, symbol: str, dataset: str | None = None,
                     start: str | None = None, end: str | None = None,
                     order: str = "asc", limit: int = 5000) -> list[dict]:
        """One macro series. Rows: ``MacroSeriesRow``."""
        raise NotSupported(f"{self.name} {UNSUPPORTED['macro']}")

    def setup_hint(self, cap: str) -> str:
        """The text the UI shows when ``cap`` answers 409 (source not configured).

        Must do no I/O. Override to give a per-capability instruction.
        """
        return f"{self.name} is not configured"

    def configured(self) -> bool:
        """False when the source needs setup (an API key, a path) it doesn't have.

        Must do no I/O: the engine calls it on every request.
        """
        return True

    # Capability name -> methods that must ALL be overridden to claim it.
    _CAPABILITY_METHODS = MappingProxyType({
        "quote": ("quote",),
        "stream": ("stream",),
        "prices": ("prices",),
        "screener": ("screener",),
        "logos": ("logos",),
        "options": ("options_underlyings", "option_chain"),
        "flow": ("options_flow",),
        "calendar": ("economic_calendar",),
        "macro": ("macro_catalog", "macro_series"),
    })

    def capabilities(self) -> set[str]:
        """Which optional surfaces this provider actually implements.

        A method counts when the provider's class overrides it; attributes set
        on an instance are ignored.
        """
        t = type(self)
        caps = {"search", "candles"}
        for cap, methods in self._CAPABILITY_METHODS.items():
            if all(getattr(t, m) is not getattr(Provider, m) for m in methods):
                caps.add(cap)
        return caps
