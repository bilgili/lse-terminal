## Context

The terminal reads market data through `lse_terminal.contracts.provider.Provider`. The registry loads built-in providers and plugins from the `lse_terminal.providers` entry-point group. The compliance harness `check_provider()` checks `search()` and `candles()`.

Today the contract covers four methods. The LSE provider also implements `prices`, `screener`, `logos`, `options_underlyings`, `option_chain`, `options_flow`, `economic_calendar`, `macro_catalog` and `macro_series`. The server calls those through `reg.get("lse")` or through `getattr`. The server also holds four in-memory TTL dictionaries for options, the calendar and macro data. No layer caches candles.

The Markets UI is `lse_terminal/ui/static/app.js`. It tracks the active source in `state.provider` and the source list in `state.providers`. The source list carries each provider's `capabilities`.

## Goals / Non-Goals

**Goals:**
- One contract covers every data surface the UI reads.
- A plugin author implements one class, runs one harness, and the UI shows the result.
- A route picks its source by capability, not by name.
- Closed candles are read once from the network and then from disk.
- The cache has one owner, one file, one off switch.
- The moomoo plugin proves the path with a real vendor.

**Non-Goals:**
- Cross-source symbol mapping ("AAPL on any source").
- A per-capability source picker in settings.
- TimescaleDB in this change. The store interface permits it later.
- Adjusted price series.
- Caching of `tick`, `prices` and `screener`.
- A change to the databank download. It stays on the LSE SDK.
- The 100k candle ceiling on remote backtests. It stays.

## Decisions

### D1. Lift the surfaces into the base class, not into Protocols

The base class gains nine optional methods. Each raises `NotSupported`. `capabilities()` detects an override with `type(self).m is not Provider.m`, the same test `quote` and `stream` use today. Capability names: `prices`, `screener`, `logos`, `options` (underlyings and chain), `flow` (the options tape), `calendar`, `macro`. `flow` is separate from `options` so a source with a chain but no tape (moomoo) lets `/api/options/flow` fall back to a source that has one.

Rows stay vendor dicts. The contract names the required keys per method in the docstring. Typed rows come when a second non-LSE provider makes the shapes stable.

Discarded: one Protocol class per capability. The override test already exists. Protocols add types the UI never reads back.

### D2. One resolver in the engine

```python
def _source(cap: str, provider: str | None = None) -> Provider
```

Explicit `provider`: 404 when unknown or when the provider lacks `cap`, 409 when the provider is not configured. No `provider`: the first registered provider that has `cap` and is configured, else 409. Registration order is `userdata`, `demo`, `lse`, then plugins. `demo` claims none of the lifted capabilities, so LSE stays the default while a key is set.

The resolver replaces `_lse_for_options`, `_lse_for_macro` and every `reg.get("lse")` except the databank download. The download drives the LSE SDK's bulk `history()` into parquet. It is an LSE product feature, not a data surface. A named `_lse_only()` helper marks that one exception.

### D3. Shared timeframe table

`TIMEFRAME_SECONDS` moves to `contracts/types.py`. `demo.py` and `userdata.py` import it. `tick` is absent from the table. The cache treats a timeframe outside the table as not cacheable.

### D4. Plugins are custom sources

`load_plugins` sets `is_custom = True` on every entry-point provider that leaves the attribute at `False`. `is_custom` and `is_broker` become contract attributes. The directory gate in `/api/providers` already exempts custom sources.

### D5. Cache wrapper at the registry

`Registry.register()` wraps a provider in `CachedProvider(inner, store)` when `inner.cacheable` is `True`, the cache switch is on and the engine is not hosted. `demo`, `userdata` and `broker:*` set `cacheable = False`. The wrapper delegates every attribute to `inner` through `__getattr__`. It overrides `candles` and the six snapshot methods. The server never sees the wrapper.

Discarded: a cache inside each provider. Every plugin author would write one. Discarded: a cache in the routes. That is the status quo, and the assistant tools and the backtester bypass it.

### D6. Store schema

One SQLite file at `<config_dir>/cache.sqlite`, WAL mode, stdlib `sqlite3`.

```sql
bars      (provider, symbol, tf, ts, open, high, low, close, volume)
          PRIMARY KEY (provider, symbol, tf, ts) WITHOUT ROWID
coverage  (provider, symbol, tf, start, end)
          PRIMARY KEY (provider, symbol, tf, start)   -- half-open [start, end)
snapshots (key, at, body)  PRIMARY KEY (key)          -- json body, TTL per call
```

`RangeStore` interface: `read(key, ws, we)`, `upsert(key, df)`, `coverage(key)`, `add_coverage(key, s, e)`, `clear(key)`. `add_coverage` merges overlapping and adjacent ranges. A TimescaleDB store implements the same five methods later.

### D7. Candle fill rules

Let `now` be one sample per call, `step = TIMEFRAME_SECONDS[tf]`, `settle = min(step, 60)`, `horizon = now - settle`.

1. **Closed bar.** A bar is closed when `ts + step <= horizon`. The store writes closed bars only. Coverage never extends past `horizon`.
2. **Live tail.** When the request window reaches the present, the wrapper calls `inner.candles(symbol, tf, limit=2)`. It upserts the closed bar of the two. It appends the open bar to the response and does not store it.
3. **Windowed call** (`start` given). `missing = [ws, we) minus coverage`. The wrapper makes one `inner.candles(start, end, limit=5000)` call per gap. It records coverage `[gap_start, min(gap_end, last_ts + step, horizon))`. A truncated answer leaves the remainder uncovered. The wrapper returns `store.read(ws, we)` and applies `head(limit)`.
4. **Limit-only call** (no `start`). If coverage reaches `horizon` and the store holds at least `limit` closed bars, the wrapper serves from the store. Otherwise it calls `inner.candles(limit=limit)`, upserts the closed bars, and records coverage `[first_ts, horizon)`. It applies `tail(limit)`.
5. **Holidays.** Coverage records what was asked and answered. An empty answer for a range still records coverage. The wrapper never infers coverage from stored bars.
6. **Locks.** One `threading.Lock` per `(provider, symbol, tf)`. The lock guards the gap computation and the fill. Bars and coverage write in one transaction, bars first.
7. **Bypass.** `tick`, a timeframe outside the table, or `cacheable = False` pass straight to `inner`.
8. **Invalidation.** `clear(key)` drops both tables for the key. `DELETE /api/cache` and `lset cache clear` call it. No eviction. `# ponytail: no eviction, add LRU by (key, last_read) when the file passes 1 GB`.

Discarded: a trading-hours multiplier to turn `limit` into a window. The multiplier differs per asset class. Rule 4 avoids the guess.

### D8. Snapshot TTLs

| Method | TTL |
|---|---|
| `options_underlyings` | 60 s |
| `option_chain` | 15 s |
| `options_flow` | 15 s |
| `economic_calendar` | 60 s |
| `macro_catalog` | 60 s |
| `macro_series` | 600 s |
| `logos` | 3600 s |

Key = `(provider, method, sorted args)`. `prices` and `screener` pass through. The UI polls prices every second and the LSE screener revalidates with ETag.

### D9. Routes

Each migrated route becomes: resolve the source, call the method, map `NotSupported` to 404 and any other exception to 502. The 409 text becomes one generic sentence. `DELETE /api/cache?provider=&symbol=&timeframe=` and `GET /api/cache/stats` are local-mode only.

### D10. UI

One helper `capParam(cap)` in `app.js` returns `provider=<active>` when the active source has `cap`, else an empty string. Nine call sites append it. The chain-or-flow URL builder passes `options` for the chain and `flow` for the tape. The React pages and the backtest dialog stay unchanged.

### D11. moomoo plugin

Separate repository `lse-terminal-moomoo`. One class `MoomooProvider`, `name = "moomoo"`, `is_custom = True`, entry point `lse_terminal.providers`. Transport: the OpenD gateway through `moomoo-api`. Config: `MOOMOO_OPEND=host:port`, default `127.0.0.1:11111`.

| Contract method | SDK call |
|---|---|
| `search` | `get_stock_basicinfo` per enabled market; watchlist groups from `get_user_security_group` and `get_user_security` |
| `candles` | `request_history_kline(code, start, end, ktype, autype=NONE, max_count=1000)` paged on `page_req_key` |
| `quote`, `prices` | `get_market_snapshot`, 400 codes per call |
| `stream` | `subscribe(codes, [TICKER])` and a `TickerHandlerBase`, bridged to asyncio as in `lse.py` |
| `options_underlyings` | catalog rows with `has_option` |
| `option_chain` | `get_option_expiration_date`, `get_option_chain`, then `get_market_snapshot` on the contract codes for IV, greeks, OI and volume |

Timeframes: `1m 5m 15m 30m 1h 1d 1w`. All other methods raise `NotSupported`.

Time: kline `time_key` is a naive string in the exchange's local zone. The plugin maps market to zone with `zoneinfo` and converts to epoch UTC. **Verify before code:** whether an intraday `time_key` marks bar open or bar close. One test pins the answer.

Adjustment: `AuType.NONE`. The cache invariant I2 needs closed bars to stay immutable. Adjusted bars change on every split.

Threading: one `OpenQuoteContext`, one lock around every SDK call, rebuild on a dead socket.

### Invariants for the model check

- **I1.** No stored bar has `ts + step > horizon` at the time of the write.
- **I2.** For every covered range of a key, the stored bars in that range equal the source's closed bars in that range.
- **I3.** The served frame equals what the unwrapped provider serves for the same call, except that the open bar may be one poll newer.
- **L1.** Every gap inside a requested window is filled after a finite number of calls, when the source answers.

## Risks / Trade-offs

- **server.py surgery.** Twelve edits in a 7555-line file. Mitigation: a stub provider that claims every capability drives one route test per migrated endpoint. Steps land in four pull requests, each green before the next.
- **Source restates history.** A vendor corrects a closed bar. The cache serves the old bar until `clear`. Accepted for v1. Daily bars rarely change; the clear path exists.
- **Clock skew.** The source's clock lags ours and a bar we call closed is still forming at the source. `settle` absorbs up to 60 s. A larger lag writes a partial bar once; the live-tail upsert on the next call corrects it, because the tail re-reads the last closed bar.
- **Dict rows.** A plugin can return a row that lacks a key the UI reads. The harness checks the required keys. The UI already renders missing fields as blank.
- **moomoo quota.** `request_history_kline` counts distinct symbols per 30 days. The cache keeps repeat reads off the gateway. A heavy screener walk still burns slots. The plugin shows the quota in its status text.
- **Lock granularity.** Per-key locks hold a dict of locks that grows with symbols used. Bounded by the symbols a user touches in one process. Accepted.
