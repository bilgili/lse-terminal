## Context

The terminal reads market data through `lse_terminal.contracts.provider.Provider`. The registry loads built-in providers and plugins from the `lse_terminal.providers` entry-point group. The compliance harness `check_provider()` checks `search()` and `candles()`.

Today the contract covers four methods. The LSE provider also implements `prices`, `screener`, `logos`, `options_underlyings`, `option_chain`, `options_flow`, `economic_calendar`, `macro_catalog` and `macro_series`. The server calls those through `reg.get("lse")` or through `getattr`. The server also holds four in-memory TTL dictionaries for options, the calendar and macro data. No layer caches candles.

The Markets UI is `lse_terminal/ui/static/app.js`. It tracks the active source in `state.provider` and the source list in `state.providers`. The source list carries each provider's `capabilities`.

A TLA+ model of the cache rules (`design.tla`, `design.cfg`, `design-review.md` beside this file) found seven ways the first draft of the fill rules served or kept wrong bars. This design carries the amended rules. Each `design.cx_*.cfg` reproduces one defect of the first draft; `design.cfg` passes with the rules below.

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
- Caching of `tick`, `prices`, `screener`, and calls with `end` and no `start`.
- A change to the databank download. It stays on the LSE SDK.
- The 100k candle ceiling on remote backtests. It stays.
- Cache entitlement by API key or plan. The cache key is the provider name. After a key change to a lower plan, bars fetched under the higher plan stay served. Accepted for single-user local mode.

## Decisions

### D1. Lift the surfaces into the base class, not into Protocols

The base class gains nine optional methods. Each raises `NotSupported`. `capabilities()` detects an override with `type(self).m is not Provider.m`, the same test `quote` and `stream` use today. Capability names: `prices`, `screener`, `logos`, `options` (underlyings and chain), `flow` (the options tape), `calendar`, `macro`. `flow` is separate from `options` so a source with a chain but no tape (moomoo) lets the flow route fall back.

Rows stay dicts at runtime. The required keys per surface live once, as `TypedDict` classes in `contracts/types.py` (`total=False` for optional keys). The harness reads `__required_keys__`. The docstring of each method names its row type. Typed values can land later in the same place without churn.

The key lists come from two sources and nowhere else: a recorded LSE fixture (`tests/fixtures/lse_rows.json`, one row per surface, captured once with a real key) and the keys `app.js` reads. The first draft of this design invented the lists; the LSE provider would have failed its own harness (calendar rows carry `datetime` and `region_code`, not `ts` and `importance`; `macro_series` rows carry `date`). The lists are built in PR 1 from the fixture, not written from memory.

Discarded: one Protocol class per capability. The override test already exists. Protocols add types the UI never reads back.

### D2. One resolver in the engine, explicit source never falls back

```python
def _source(cap: str, provider: str | None) -> Provider
```

Explicit `provider`: 404 when unknown or when the provider lacks `cap`, 409 when the provider is not configured. The resolver never replaces an explicit provider with another source.

No `provider`: the resolver uses the default source for `cap`. The default source is the first registered provider that has `cap`, whether or not it is configured. When it is not configured, the route answers 409. When no provider has `cap`, the route answers 404. `/api/logos` skips the `configured()` check, because the LSE logo map needs no key.

A route is **global** when its answer carries no symbol argument: `screener`, `logos`, `options_underlyings`, `economic_calendar`, `macro_catalog`, and `options_flow` without an underlying. A route is **symbol-scoped** when its answer is about a symbol: `prices`, `option_chain`, `macro_series`, and `options_flow` with an underlying. Both kinds use the same resolver.

The default source does not depend on `configured()`, so it does not change during a session. `register` replaces an existing name in place, so `set_key` and `clear_key` keep LSE at its position. LSE is built in, registers before every plugin and broker, and claims every lifted capability. The default source is therefore LSE for every capability, which is today's behaviour: LSE rows, or 409 without a key. A plugin serves a route only when the caller names it.

Every capability route answers with the header `X-Source: <provider name>`. A page stores the source of a listing response and sends it as an explicit `provider` on each symbol call that comes from that listing (D10).

Amended in PR 2 twice. The first draft answered 400 for a symbol-scoped call without `provider`. The React pages (`frontend/src/pages/EconomicCalendar.tsx`) call `/api/macro/series` without `provider`, and D10 keeps them unchanged, so the 400 rule would break the macro views for LSE-only users. The second draft picked the first *configured* provider. The routing model (`design_routing.tla`) found two counter-examples against it: the React catalog, which is cached for the life of the page, came from a plugin before `set_key` and the series came from LSE after it; and a tape click sent a symbol listed by the `flow` default to the `options` source. `design_routing_fixed.cfg` passes with both rules above (static default source, `X-Source` pinning); each rule alone fails (`design_routing_regonly.cfg`, `design_routing_pinonly.cfg`). Discarded: change the React pages, because the bundle is a build artifact.

**Error text.** The 409 detail for an unconfigured LSE keeps today's per-route text word for word. A 409 for a plugin says `<name> is not configured`. The 502 detail keeps the route prefix and `str(e)`, because `app.js` matches on the upstream text.

Registration order is `userdata`, `demo`, `lse`, then plugins sorted by entry-point name. `demo` claims none of the lifted capabilities, so LSE stays the default source while a key is set. Entry-point discovery order differs between environments, so the sort makes the default source stable.

`configured()` MUST do no I/O. The resolver calls it on every request for every provider.

The resolver replaces `_lse_for_options`, `_lse_for_macro` and every `reg.get("lse")` except the databank download. The download drives the LSE SDK's bulk `history()` into parquet. It is an LSE product feature, not a data surface. A named `_lse_only()` helper marks that one exception. The quant fit source `lse-options` keeps an explicit `provider="lse"`, so a fit does not change vendor when a key is removed.

**Route snapshot cache in PR 2.** The four route caches (`econ_cache`, `macro_cat_cache`, `macro_series_cache`, `opt_cache`) merge into one TTL helper. The key is the resolved provider name, a route tag and the canonical arguments; it never uses the raw `provider` query parameter. The resolver, with its `configured()` check, runs before the cache lookup, so after sign-out no cached row is served. Each route tag keeps its own bound (calendar 64, catalog 1, series 256, options 64). The TTL values do not change. PR 3 replaces the helper with the wrapper snapshot cache (D8). PR 3 must keep a TTL when the engine is hosted or `LSE_TERMINAL_CACHE=off`, because today both modes have the route caches. Discarded: delete the route caches in PR 2. Between PR 2 and PR 3 every options poll and calendar filter change would then reach the network.

### D3. Shared timeframe table and bound parser

`TIMEFRAME_SECONDS` moves to `contracts/types.py`. `userdata.py` imports it. `demo.py` uses `{**TIMEFRAME_SECONDS, "tick": 1}`, because the demo walk serves `tick` as a one-second grid and must keep that timeframe. `tick` is absent from the shared table. The cache treats a timeframe outside the table as not cacheable.

`to_epoch(bound)` lives next to the table. It turns an ISO string with offset, a date-only string, an epoch string or an int into epoch seconds UTC. `LseProvider._gate_ts`, the wrapper and the harness use the shared parser.

A string of exactly eight ASCII digits with no sign is a basic ISO date (`YYYYMMDD`, UTC midnight), not epoch seconds. An eight-digit epoch is a moment in 1970 to 1973, which no caller means. `_gate_ts` already reads `"20240630"` as a date on Python 3.11 and later. The shared rule uses `strptime`, so the answer does not depend on the Python version. An eight-digit string that is not a real date (`"99999999"`) raises `ValueError`. Synthetic clocks in tests start at or after epoch 1e9, so their string bounds are never eight digits. Through `to_epoch`, `_gate_ts` no longer accepts float strings; it passes them to the gate unchanged, as it passes every string it cannot parse. Decided in PR 2 (task 2.14).

### D4. Plugins are custom sources

`load_plugins` sets `is_custom = True` on every entry-point provider that leaves the attribute at `False`. `is_custom` and `is_broker` become contract attributes. The directory gate in `/api/providers` already exempts custom sources.

### D5. Cache wrapper at the registry, cache state owned by the registry

The registry owns one `CandleCache` object. It holds the store, the per-key locks and the hosted flag. `Registry(cache=...)` receives it at construction; `create_app` builds it, because the hosted flag is computed there today. `Registry.register()` wraps a provider in `CachedProvider(inner, cache)` when `inner.cacheable` is `True`, the switch `LSE_TERMINAL_CACHE` is not `off`, and the engine is not hosted. `demo`, `userdata` and `broker:*` set `cacheable = False`.

`CachedProvider` is a plain class, not a `Provider` subclass. It delegates every attribute it does not define through `__getattr__`, so `name`, `title`, `timeframes`, `capabilities()`, `configured()`, `quote` and `stream` come from `inner`. A subclass would shadow all of those with the base defaults and would make `capabilities()` claim every snapshot method for every provider. No code checks `isinstance(p, Provider)` today; the spec states the wrapper is not one.

The locks live in the registry's `CandleCache`, not in the wrapper. `set_key` and `clear_key` register a fresh `LseProvider`. Wrapper-owned locks would give one key two locks across that swap.

**Sign-out pass-through.** When `inner.configured()` is `False`, the wrapper passes every call straight to `inner`, candles and snapshots alike. After sign-out, nothing LSE is served from disk, and the unwrapped error surfaces.

Discarded: a cache inside each provider. Every plugin author would write one. Discarded: a cache in the routes. That is the status quo, and the assistant tools and the backtester bypass it.

### D6. Store schema

One SQLite file at `<config_dir>/cache.sqlite`, WAL mode, stdlib `sqlite3`.

```sql
bars      (provider, symbol, tf, ts, open, high, low, close, volume)
          PRIMARY KEY (provider, symbol, tf, ts) WITHOUT ROWID
coverage  (provider, symbol, tf, start, end, empty_until)
          PRIMARY KEY (provider, symbol, tf, start)   -- half-open [start, end)
snapshots (key, at, expires, body)  PRIMARY KEY (key) -- json body
```

`coverage.empty_until` is `NULL` for coverage that came from an answer with bars. Coverage that came from an empty answer carries `empty_until = now + EMPTY_TTL` (3600 s). After that time the gap computation treats the range as uncovered again. A holiday costs one cheap empty call per hour. A source that answered empty by mistake heals within the hour. Ranges with different `empty_until` do not merge.

`RangeStore` interface: `read(key, ws, we)`, `upsert(key, df)`, `coverage(key, now)`, `add_coverage(key, s, e, empty_until)`, `clear(key)`. `add_coverage` merges overlapping and adjacent ranges of the same kind. A TimescaleDB store implements the same five methods later.

SQLite threading: one connection per thread (`threading.local`), `busy_timeout` 5 s, `PRAGMA journal_mode=WAL`. A write transaction never spans a source call. Each page of a fill is one transaction after the source answers, bars first, then coverage.

Snapshot bodies pass through FastAPI's `jsonable_encoder` before storage, so rows with `datetime` or `NaN` round-trip. Every `snapshot_put` deletes rows whose `expires` has passed, so the table stays bounded like the dicts it replaces.

### D7. Candle fill rules

**Definitions.** The wrapper samples `now` once, at lock acquire, before the first source call. `step = TIMEFRAME_SECONDS[tf]`. `settle = min(step, inner.settle_seconds)`; `settle_seconds` is a contract attribute, default 60, the source's publication delay. `horizon = now - settle`. `edge = horizon - step + 1`. A bar with `ts < edge` has `ts + step <= horizon` and is closed. Coverage never contains a tick at or past `edge`. "The window reaches the present" means `end is None or to_epoch(end) > edge`.

`L` is the page size the wrapper asks the source for, 5000.

1. **Bypass.** `tick`, a timeframe outside the table, `cacheable = False`, `inner.configured()` false, or a call with `end` and no `start` pass straight to `inner`. The end-only shape comes from a backtest with a "to" date and no "from" date; it is rare and the newest-N-before-`end` rule would add a fourth path for it.
2. **Bounds.** `ws = to_epoch(start)`, `we = to_epoch(end)` or `None`. Half-open `[ws, we)`. The clipped end is `wc = min(we, edge)` or `edge` when `we` is `None`.
3. **Windowed call** (`start` given). Under the key lock: `gaps = [ws, wc) minus coverage`, ascending. For each gap `[g, h)`, loop: call `inner.candles(start=g, end=h, limit=L)`. If the answer has exactly `L` rows (truncated): upsert, add coverage `[g, last_ts + 1)`, set `g = last_ts + 1`, continue while the bars collected for the response are fewer than `limit`. If the answer has fewer than `L` rows: upsert, add coverage `[g, h)`, stop. If the answer is empty: add coverage `[g, h)` with `empty_until`. The bound `last_ts + 1` is correct on any grid, including a half-hour session-open bar; `last_ts + step` over-reaches on an off-grid bar and under-reaches on a complete answer. The response is the stored bars of the contiguous covered run that starts at `ws`, read under the lock. Then the live tail (rule 6). Then `head(limit)` over the whole frame, matching `lse.py`.
4. **Limit-only call** (no `start`, no `end`). Under the key lock: let `c` be the end of the covered run that would end at `edge`, or `None` when no run ends there. If coverage exists and `c < edge`, run rule 3 on `[c, edge)` with at most one page. If after that a covered run ends at `edge` and holds at least `limit` stored bars, serve the newest `limit` bars of that run plus the live tail. Otherwise: call `inner.candles(limit=limit)`, upsert the bars with `ts < edge`, add coverage `[first_ts, edge)`, serve those bars plus the live tail. The hit test counts only the run that ends at `edge`; counting every stored bar of the key splices across a hole.
5. **Holiday or failure.** Coverage records what was asked and answered. An empty answer records coverage with `empty_until` (D6). The contract says `candles` raises on failure and an empty frame means "no bars in the range"; a plugin that maps an SDK error to an empty frame violates the contract, and `empty_until` limits the damage to one hour.
6. **Live tail.** When the window reaches the present, the wrapper calls `inner.candles(start=edge, end=we, limit=limit - len(response))` and appends every returned bar to the response. It stores none of them: a tail bar is never closed by definition. The first draft fetched `limit=2`; with `settle = step` on timeframes up to one minute, both bars sit inside the settle window and the chart lost its second-newest bar.
7. **Locks.** `CandleCache.locks` holds one `threading.Lock` per `(provider, symbol, tf)`. The lock covers the gap computation, every fill page, and the response read. `clear(key)` takes the same lock. A global clear takes every lock in sorted order. The CLI `lset cache clear` calls `DELETE /api/cache` on the running engine; when no engine runs, it opens the file directly, because no reader can be in flight.
8. **Invalidation.** `clear(key)` drops both tables for the key. No eviction. `# ponytail: no eviction, add LRU by (key, last_read) when the file passes 1 GB`.

Discarded: a trading-hours multiplier to turn `limit` into a window. The multiplier differs per asset class. Rule 4 avoids the guess. Discarded: a generation number in SQLite for cross-process clear. The CLI-through-engine rule covers it with no schema.

### D8. Snapshot TTLs

| Method | Default TTL |
|---|---|
| `options_underlyings` | 60 s |
| `option_chain` | 15 s |
| `options_flow` | 15 s |
| `economic_calendar` | 60 s |
| `macro_catalog` | 60 s |
| `macro_series` | 600 s |
| `logos` | 3600 s |

Seven methods. The wrapper owns the mechanism. A provider may override a value with a `snapshot_ttl: dict[str, int]` attribute, because the TTL is a property of the vendor's data. Key = `(provider, method, canonical args)`. The wrapper upper-cases and strips the underlying before it builds the key, as the routes do today. `prices` and `screener` pass through. The UI polls prices every second and the LSE screener revalidates with ETag. Sign-out pass-through (D5) applies to snapshots.

### D9. Routes

Each migrated route becomes: resolve the source, call the method, map `NotSupported` to 404 and any other exception to 502. The 409 for a missing key moves into the resolver's `configured()` check and replaces the `RuntimeError` mapping in `/api/screener`. `DELETE /api/cache?provider=&symbol=&timeframe=` and `GET /api/cache/stats` are local-mode only.

### D10. UI

One helper `capParam(cap)` in `app.js` returns `provider=<active>` when the active source has `cap`, else an empty string. A page uses `capParam` only for its listing calls. A symbol call that comes from a listing sends the listing's `X-Source` value as an explicit `provider`. This includes a tape click that opens the chain. When that source lacks the capability, the answer is 404 and the page shows "not supported by this source". The options page saves `{und, src}`, and drops a saved `und` when its source is no longer the source of the rail. The trade dock keys its underlyings list by source and reloads it when `capParam("options")` changes. Call sites that name `provider=lse` today keep it. The React pages and the backtest dialog stay unchanged; without `provider` they always reach the default source.

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

Contract items the plugin must honour for the cache:
- Page `request_history_kline` until `limit` rows or the end of the range, so a short answer means "complete".
- Map `ret != RET_OK` to an exception, never to an empty frame.
- Convert moomoo's inclusive `end` to the half-open contract.
- Cache the TCP probe so `configured()` does no I/O; refresh it from a background thread every 30 s.
- `settle_seconds` set from observed publication delay, default 60.

Time: kline `time_key` is a naive string in the exchange's local zone. The plugin maps market to zone with `zoneinfo` and converts to epoch UTC. **Verify before code:** whether an intraday `time_key` marks bar open or bar close. One test pins the answer.

Adjustment: `AuType.NONE`. The cache invariant I2 needs closed bars to stay immutable. Adjusted bars change on every split.

Threading: one `OpenQuoteContext`, one lock around every SDK call, rebuild on a dead socket.

### Invariants, checked by `design.tla`

- **I1.** No stored bar is partial at the source.
- **I2.** For every covered tick of a key, a closed source bar is stored with the source's value, and a tick with no source bar has no stored bar.
- **I3.** The served frame equals what the unwrapped provider serves for the same call at the same instant.
- **L1.** With windowed calls and fairness, every closed tick of every key becomes covered.

`design.cfg` runs all amended rules. The `design.cx_*.cfg` runs each flip one rule back to the first draft and reproduce one violation: `cx_tail`, `cx_edge`, `cx_trunc`, `cx_count`, `cx_lock`, `cx_irr`, `cx_transient`, `cx_skip`, `cx_lag`, `cx_restate`. The review gate runs `tlc design.tla -config design.cfg` whenever D7 changes.

## Risks / Trade-offs

- **server.py surgery.** Twelve edits in a 7555-line file. Mitigation: a stub provider that claims every capability drives one route test per migrated endpoint. Steps land in four pull requests, each green before the next.
- **Source restates history.** A vendor corrects a closed bar. The cache serves the old bar until `clear`. `cx_restate` confirms it. Accepted for v1. Daily bars rarely change; the clear path exists.
- **Clock skew above `settle`.** `settle` absorbs a source lag up to `settle` (`ok_lag_eq_settle`). A larger lag stores a partial bar and covers bars the source has not published (`cx_lag`). The live tail does not repair it. Mitigation: `settle_seconds` is a provider attribute, so a slow vendor declares a larger value. Accepted beyond that.
- **Interior hole in a newest-N answer.** A source that drops a page inside a limit-only answer leaves a covered tick with no bar (`cx_skip`). The contiguity clause in the contract forbids it. Accepted beyond the contract.
- **Dict rows.** A plugin can return a row that lacks a key the UI reads. The harness checks `__required_keys__`. The UI already renders missing fields as blank.
- **moomoo quota.** `request_history_kline` counts distinct symbols per 30 days. The cache keeps repeat reads off the gateway. A heavy screener walk still burns slots. The plugin shows the quota in its status text.
- **Lock granularity.** Per-key locks hold a dict of locks that grows with symbols used. Bounded by the symbols a user touches in one process. Accepted.
- **Symbol-scoped routes return 400 without a provider.** Old UI builds that omit the parameter break on those routes. The UI ships in the same PR as the routes.
