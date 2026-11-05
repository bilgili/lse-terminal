## ADDED Requirements

### Requirement: Cache wrapper at registration
`Registry.register` SHALL wrap a provider in `CachedProvider` when the provider's `cacheable` is `True`, in every mode. The `CandleCache` store SHALL be a `SqliteStore` only when the engine is local and `LSE_TERMINAL_CACHE` is not `off`; otherwise the store SHALL be `None` and `candles` SHALL pass through. The registry SHALL own one `CandleCache` that holds the store, the snapshot backend and the per-key locks; every wrapper SHALL share it. `CachedProvider` SHALL NOT subclass `Provider`. It SHALL delegate every attribute it does not define to the wrapped provider.

#### Scenario: Cache off
- **WHEN** `LSE_TERMINAL_CACHE=off` and the registry registers `LseProvider`
- **THEN** `candles` reaches the unwrapped provider unchanged, no cache file is created, and the seven snapshot methods use an in-memory TTL

#### Scenario: Hosted mode
- **WHEN** the engine is hosted and the registry registers `LseProvider`
- **THEN** `candles` reaches the unwrapped provider unchanged, no cache file is created, and the seven snapshot methods use an in-memory TTL

#### Scenario: Wrapper is transparent
- **WHEN** the registry wraps a provider that overrides only `search` and `candles`
- **THEN** the wrapper's `name`, `title`, `timeframes`, `configured()` and `stream` come from the wrapped provider, and `capabilities()` is exactly `{search, candles}`

#### Scenario: Locks survive re-registration
- **WHEN** `set_key` registers a fresh `LseProvider` while a fill for key K is in flight
- **THEN** the next call for K waits on the same lock the in-flight fill holds

### Requirement: Sign-out pass-through
When the wrapped provider's `configured()` is `False`, the wrapper SHALL pass every call, candles and snapshots alike, straight to the wrapped provider without reading the store.

#### Scenario: Covered window after sign-out
- **WHEN** a window for `lse` is fully covered and the LSE key is removed
- **THEN** a call for that window reaches the unwrapped provider and surfaces its error

### Requirement: Store schema
The store SHALL be one SQLite file at `<config_dir>/cache.sqlite` in WAL mode with tables `bars`, `coverage` and `snapshots` as defined in the design. `coverage` ranges SHALL be half-open `[start, end)` and SHALL merge overlapping or adjacent ranges that share the same `empty_until`. The store SHALL use one connection per thread, SHALL set `busy_timeout`, and SHALL NOT hold a write transaction across a source call.

#### Scenario: Adjacent ranges merge
- **WHEN** coverage holds `[0, 100)` and the store adds `[100, 200)` with the same `empty_until`
- **THEN** coverage holds one range `[0, 200)`

#### Scenario: Two keys do not serialise on the store
- **WHEN** two threads fill different keys and one source call takes two seconds
- **THEN** the other thread's fill completes without waiting for that call

### Requirement: Closed edge
With `now` sampled once at lock acquire, `settle = min(step, settle_seconds)`, `horizon = now - settle` and `edge = horizon - step + 1`, the wrapper SHALL store a bar only when `ts < edge` and SHALL never record coverage at or past `edge`.

#### Scenario: Forming bar is not stored
- **WHEN** the source returns a bar with `ts >= edge`
- **THEN** the bar is not in `bars`

#### Scenario: Bar inside the horizon window is not covered
- **WHEN** `step` is 300, `now` is 30 s past a grid line, and a fill runs
- **THEN** the bar that opened 4 min 30 s ago is neither covered nor stored

### Requirement: Live tail from the closed edge
When the window reaches the present (`end` is `None` or `to_epoch(end) > edge`), the wrapper SHALL call the source with `start=edge`, the original `end`, and the remaining limit, SHALL append every returned bar to the response, and SHALL store none of them.

#### Scenario: One-minute chart keeps its second-newest bar
- **WHEN** `step` is 60, `settle` is 60, and a chart polls a fully covered key
- **THEN** the response holds every bar the unwrapped provider would return, including the bar that closed within the last 60 s

#### Scenario: Repeat poll
- **WHEN** a chart polls the same covered key twice within one bar
- **THEN** the second poll makes exactly one source call, the tail call from `edge`

### Requirement: Windowed gap fill
For a call with `start`, under the key lock the wrapper SHALL compute the gaps between `[start, min(end, edge))` and coverage, ascending. For each gap it SHALL call the source with `limit=5000`. On exactly 5000 rows it SHALL record coverage `[gap_start, last_ts + 1)` and continue from `last_ts + 1` while the response holds fewer than `limit` bars. On fewer rows it SHALL record coverage for the whole gap. On zero rows it SHALL record coverage for the whole gap with `empty_until = now + 3600`. The response SHALL be the stored bars of the contiguous covered run that starts at `start`, read under the lock, plus the live tail, with `head(limit)` applied.

#### Scenario: Gap between two covered windows
- **WHEN** coverage holds `[0, 100)` and `[200, 300)` and a call asks `[0, 300)`
- **THEN** the wrapper makes one source call for `[100, 200)`

#### Scenario: Truncated answer does not leave a hole in the response
- **WHEN** a call asks `[0, 8)` with `limit=4`, the source caps at 3 rows per call and holds bars at every tick
- **THEN** the wrapper makes a second call from `last_ts + 1` and the response is `{0, 1, 2, 3}`

#### Scenario: Off-grid bar does not over-reach coverage
- **WHEN** the source holds a session-open bar at `ts = 1` with `step = 2`, then bars at 2, 4, 6, and the source caps at 1 row
- **THEN** after the first page coverage ends at 2, not at 3

#### Scenario: Holiday
- **WHEN** the source returns zero bars for a gap
- **THEN** coverage records the gap with `empty_until`, and a call within the hour makes no source call for it

#### Scenario: Empty answer heals
- **WHEN** a gap was recorded from an empty answer and 3600 s pass
- **THEN** the next call treats the range as a gap and asks the source again

### Requirement: Limit-only fill
For a call without `start` and without `end`, under the key lock the wrapper SHALL first run one page of windowed fill from the end of the covered run that ends at `edge`, when such a run exists and ends before `edge`. If a covered run then ends at `edge` and holds at least `limit` stored bars, the wrapper SHALL serve the newest `limit` bars of that run plus the live tail. Otherwise it SHALL call the source with `limit`, SHALL store the bars with `ts < edge`, SHALL record coverage `[first_ts, edge)`, and SHALL serve those bars plus the live tail.

#### Scenario: First and second call
- **WHEN** a backtest asks `limit=5000` twice for a cold key
- **THEN** the first call reaches the source with `limit=5000` and the second reaches it only for the tail

#### Scenario: Hit test counts only the run at the edge
- **WHEN** coverage holds `[0, 5)` with 4 stored bars and `[6, 7)` with 1 stored bar, and a call asks `limit=4`
- **THEN** the wrapper does not splice across tick 5; it fills `[5, 7)` or falls to the newest-N path

### Requirement: Bypass
The wrapper SHALL pass `tick`, any timeframe absent from `TIMEFRAME_SECONDS`, and any call with `end` and no `start` straight to the source without reading or writing the store.

#### Scenario: Tick passthrough
- **WHEN** a call asks for `tick`
- **THEN** the store is not touched

#### Scenario: End-only passthrough
- **WHEN** a backtest asks with `end` and no `start`
- **THEN** the call reaches the source unchanged

### Requirement: Per-key lock covers fill, read and clear
The cache SHALL hold one lock per `(provider, symbol, timeframe)`. The lock SHALL cover the gap computation, every fill page, and the response read. `clear(key)` SHALL take the same lock. A global clear SHALL clear each key under its own lock, in sorted key order. Bars and coverage for one page SHALL write in one transaction, bars first.

#### Scenario: Two threads, one key
- **WHEN** two threads request the same uncovered window at once
- **THEN** the source receives one call for that window

#### Scenario: Clear during a fill
- **WHEN** a fill for key K holds the lock and a clear for K arrives
- **THEN** the clear waits, and the fill's response contains the bars it fetched

### Requirement: Snapshot cache
The wrapper SHALL cache `options_underlyings`, `option_chain`, `options_flow`, `economic_calendar`, `macro_catalog`, `macro_series` and `logos` in `snapshots`, keyed by provider, method and canonical arguments (underlying upper-cased and stripped), with the default TTLs from the design or the provider's `snapshot_ttl` override. Bodies SHALL pass through `jsonable_encoder` before storage. Every write SHALL delete expired rows. `prices` and `screener` SHALL pass through.

#### Scenario: Chain within TTL
- **WHEN** the options board asks the same chain twice within 15 s, once as `spy` and once as `SPY`
- **THEN** the source receives one call

#### Scenario: Snapshot survives restart
- **WHEN** the engine restarts within a snapshot's TTL
- **THEN** the next call serves the stored snapshot

#### Scenario: Provider override
- **WHEN** a provider sets `snapshot_ttl = {"option_chain": 60}`
- **THEN** the chain is served from the store for 60 s

### Requirement: Invalidation
`DELETE /api/cache` with optional `provider`, `symbol`, `timeframe` SHALL drop matching rows from `bars`, `coverage` and `snapshots` under the matching locks. `GET /api/cache/stats` SHALL return row counts per table and the file size. Both routes SHALL be local-mode only. `lset cache clear` SHALL call `DELETE /api/cache` on a running engine, and SHALL open the file directly only when no engine runs.

#### Scenario: Clear one key
- **WHEN** `DELETE /api/cache?provider=lse&symbol=AAPL&timeframe=1d` runs
- **THEN** the next `1d` call for `AAPL` on `lse` reaches the source for the full window

### Requirement: Regression cases from the model
`tests/test_cache.py` SHALL carry one test per counter-example in `design-review.md`: `cx_tail`, `cx_edge`, `cx_trunc`, `cx_count`, `cx_lock`, `cx_irr`, `cx_transient`, `cx_skip`, `cx_lag`, `cx_restate`. The last three SHALL assert the documented, accepted outcome.

#### Scenario: Model and tests agree
- **WHEN** `tlc design.tla -config design.cfg` passes
- **THEN** every `cx_*` test passes against the implementation
