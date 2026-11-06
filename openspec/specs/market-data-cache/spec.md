# market-data-cache Specification

## Purpose
TBD - created by archiving change data-source-contract-cache. Update Purpose after archive.
## Requirements
### Requirement: Cache wrapper at registration
`Registry.register` SHALL wrap a provider in `CachedProvider` when the provider's `cacheable` is `True`, in every mode. The `CandleCache` store SHALL be a `SqliteStore` only when the engine is local and `LSE_TERMINAL_CACHE` is not `off`; otherwise the store SHALL be `None` and `candles` SHALL pass through. The registry SHALL own one `CandleCache` that holds the store, the snapshot backend and the per-key locks; every wrapper SHALL share it. `CachedProvider` SHALL NOT subclass `Provider`. It SHALL delegate every attribute it does not define to the wrapped provider, except `_inner`, for which it SHALL raise `AttributeError`.

#### Scenario: Cache off
- **WHEN** `LSE_TERMINAL_CACHE=off` and the registry registers `LseProvider`
- **THEN** `candles` reaches the unwrapped provider unchanged, no cache file is created, and the six snapshot methods use an in-memory TTL

#### Scenario: Hosted mode
- **WHEN** the engine is hosted and the registry registers `LseProvider`
- **THEN** `candles` reaches the unwrapped provider unchanged, no cache file is created, and the six snapshot methods use an in-memory TTL

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
The store SHALL be one SQLite file at `<config_dir>/cache.sqlite` in WAL mode with tables `bars`, `coverage` and `snapshots` as defined in the design. `coverage` ranges SHALL be half-open `[start, end)` and SHALL merge overlapping or adjacent ranges that share the same `empty_until`. The `coverage` table SHALL have no primary key, SHALL have an index on `(provider, symbol, tf, start)`, and SHALL delete rows by `rowid`, because a real range and an empty range can share a `start`. The store SHALL use one connection per thread, SHALL set `busy_timeout`, and SHALL NOT hold a write transaction across a source call.

#### Scenario: Adjacent ranges merge
- **WHEN** coverage holds `[0, 100)` and the store adds `[100, 200)` with the same `empty_until`
- **THEN** coverage holds one range `[0, 200)`

#### Scenario: A real range and an empty range share a start
- **WHEN** coverage holds an empty range `[100, 200)` with `empty_until` set and a fill records a real range `[100, 150)`
- **THEN** both rows exist, and the expiry of the empty range deletes only its own row

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
When a windowed call reaches the present (`end` is `None` or `to_epoch(end) > edge`), the wrapper SHALL call the source with `start=edge`, the original `end`, and the remaining limit, SHALL append every returned bar to the response, and SHALL store none of them. A limit-only call SHALL get its tail from the Limit-only fill requirement.

#### Scenario: One-minute chart keeps its second-newest bar
- **WHEN** `step` is 60, `settle` is 60, and a chart polls a fully covered key
- **THEN** the response holds every bar the unwrapped provider would return, including the bar that closed within the last 60 s

#### Scenario: Repeat poll
- **WHEN** a chart asks `limit=5000` with no `start` twice within one bar for the same key
- **THEN** the second poll makes exactly one source call, `start` at the end of the newest covered run with `limit=5000`

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
For a call without `start` and without `end`, under the key lock, when the newest covered run `[c0, c)` exists and `c <= edge`, the wrapper SHALL call the source once with `start=c` and `limit=5000` (the tail). When the tail holds fewer than 5000 rows, the wrapper SHALL store the tail bars with `ts < edge`, SHALL record coverage `[c, edge)` only when there is at least one such bar, and SHALL NOT record an empty range. When the stored bars of `[c0, edge)` plus the tail bars with `ts >= edge` hold at least `limit` bars, or `c0` is `HISTORY`, the wrapper SHALL serve the newest `limit` bars of that pool. Otherwise (no coverage, a truncated tail, or too few bars) the wrapper SHALL call the source with `limit` alone, SHALL serve that answer as it is, SHALL store its bars with `ts < edge`, and SHALL record coverage to `edge` from `HISTORY` when the answer holds fewer than `limit` rows, else from `first_ts`. The wrapper SHALL record that coverage only when its start is before `edge`, and SHALL record no coverage for an empty answer.

#### Scenario: First and second call
- **WHEN** a backtest asks `limit=5000` twice for a cold key
- **THEN** the first call reaches the source once with `limit=5000` and no `start`, and the second call reaches the source once, with `start` at the end of the newest covered run

#### Scenario: Limit-only hit serves the newest bars
- **WHEN** step is 1, coverage holds `[1, 4)` with bars 1 and 2, `edge` is 4, the source holds bars 4, 5 and 6 at or after `edge`, and a call asks `limit=2`
- **THEN** the response is bars 5 and 6, the unwrapped answer, and not bars 4 and 5

#### Scenario: Short answer covers the history
- **WHEN** a cold key has 300 bars at the source and a call asks `limit=5000`
- **THEN** the wrapper records coverage from `HISTORY` to `edge`, and a later call with `limit=1000` is served from the store and the tail with no call that has `limit` alone

#### Scenario: Short history heals within 24 h
- **WHEN** a cold key answers 300 bars for `limit=5000`, and the vendor later backfills older bars
- **THEN** coverage `[first_ts, edge)` is real, the range `[HISTORY, first_ts)` expires after 24 h, and the next call after expiry fetches the older bars

#### Scenario: Limit below 1 bypasses
- **WHEN** a call asks `limit=0`
- **THEN** the call passes to the source and the store is not touched

#### Scenario: Hit test counts only the run at the edge
- **WHEN** coverage holds `[0, 5)` with 4 stored bars and `[6, 7)` with 1 stored bar, and a call asks `limit=4`
- **THEN** the wrapper does not splice across tick 5; the pool holds only the run `[6, 7)` and the tail, and with fewer than 4 bars the call takes the newest-N path

### Requirement: Fail open
A store error SHALL NOT stop a call that the source can answer. When `SqliteStore` cannot open, `create_app` SHALL log the error and SHALL build the `CandleCache` with no store and with `MemorySnapshots`. When the cached path raises `sqlite3.Error`, the wrapper SHALL pass the call through to the source. Errors in `snapshot_get` and `snapshot_put` SHALL do the same.

#### Scenario: Store file cannot open
- **WHEN** `cache.sqlite` is not writable at engine start
- **THEN** the engine starts, `candles` passes through, and the snapshot methods use an in-memory TTL

#### Scenario: Store error during a call
- **WHEN** a store read raises `sqlite3.Error` during a `candles` call
- **THEN** the call reaches the source unchanged and returns the unwrapped answer

### Requirement: Bypass
The wrapper SHALL pass `tick`, any timeframe absent from `TIMEFRAME_SECONDS`, and any call with `end` and no `start` straight to the source without reading or writing the store.

#### Scenario: Tick passthrough
- **WHEN** a call asks for `tick`
- **THEN** the store is not touched

#### Scenario: End-only passthrough
- **WHEN** a backtest asks with `end` and no `start`
- **THEN** the call reaches the source unchanged

### Requirement: Per-key lock covers fill, read and clear
The cache SHALL hold one lock per `(provider, symbol, timeframe)`. The lock SHALL cover the gap computation, every fill page, and the response read. `clear(key)` SHALL take the same lock. A global clear SHALL clear each key under its own lock, in sorted key order. Bars and coverage for one page SHALL write in one transaction, bars first. The wrapper SHALL wait at most 30 s for a key lock; on timeout it SHALL pass the call through to the source without reading or writing the store.

#### Scenario: Two threads, one key
- **WHEN** two threads request the same uncovered window at once
- **THEN** the source receives one call for that window

#### Scenario: Clear during a fill
- **WHEN** a fill for key K holds the lock and a clear for K arrives
- **THEN** the clear waits, and the fill's response contains the bars it fetched

#### Scenario: Lock timeout
- **WHEN** a call for key K holds the lock for more than 30 s and a second call for K arrives
- **THEN** after 30 s the second call reaches the source unchanged and returns the unwrapped answer

### Requirement: Snapshot cache
The wrapper SHALL cache `options_underlyings`, `option_chain`, `options_flow`, `economic_calendar`, `macro_catalog` and `macro_series` in `snapshots`, keyed by provider, method and canonical arguments (underlying upper-cased and stripped), with the default TTLs from the design or the provider's `snapshot_ttl` override. Bodies SHALL pass through `jsonable_encoder` before storage. Every write SHALL delete expired rows. `prices`, `screener` and `logos` SHALL pass through.

#### Scenario: Chain within TTL
- **WHEN** the options board asks the same chain twice within 15 s, once as `spy` and once as `SPY`
- **THEN** the source receives one call

#### Scenario: Snapshot survives restart
- **WHEN** the engine restarts within a snapshot's TTL
- **THEN** the next call serves the stored snapshot

#### Scenario: Provider override
- **WHEN** a provider sets `snapshot_ttl = {"option_chain": 60}`
- **THEN** the chain is served from the store for 60 s

#### Scenario: Logos pass through
- **WHEN** `logos()` returns `{}` after a failed fetch and the UI asks again
- **THEN** the second call reaches the provider, and no `snapshots` row holds the empty map

### Requirement: Invalidation
`DELETE /api/cache` with optional `provider`, `symbol`, `timeframe` SHALL drop matching rows from `bars` and `coverage` under the matching locks. It SHALL drop `snapshots` rows, for the named provider or for all providers, only when the call gives no `symbol` and no `timeframe`. `GET /api/cache/stats` SHALL return row counts per table and the file size. Both routes SHALL be local-mode only. `DELETE /api/cache` SHALL return `{"cleared": n, "skipped": [[provider, symbol, tf], ...]}`; `skipped` lists the keys whose lock timed out. Both routes SHALL answer HTTP 503 with a short message when the store raises `sqlite3.Error`. Each engine process SHALL write `config_dir()/engines/engine-<pid>.json` with `{"port", "pid"}` before it serves, and SHALL remove only its own file on exit. `lset cache` SHALL ask each listed engine over HTTP with a 10 s timeout. The first engine that answers SHALL handle the command. A refused connection SHALL delete that engine file. A timeout SHALL print a message and exit 1 without opening the cache file. The CLI SHALL open the file directly only when no engine answers, none timed out, and `--port` also refuses.

#### Scenario: Clear one key
- **WHEN** `DELETE /api/cache?provider=lse&symbol=AAPL&timeframe=1d` runs
- **THEN** the next `1d` call for `AAPL` on `lse` reaches the source for the full window, and the `snapshots` rows stay

#### Scenario: Clear one provider
- **WHEN** `DELETE /api/cache?provider=lse` runs
- **THEN** the `bars`, `coverage` and `snapshots` rows of `lse` are gone, and the rows of other providers stay

#### Scenario: CLI finds the desktop engine
- **WHEN** the desktop app runs the engine on port 7800 and `lset cache clear` runs without `--port`
- **THEN** the CLI reads port 7800 from `engines/engine-<pid>.json` and calls `DELETE /api/cache` on that engine, and does not open the file

#### Scenario: CLI with two engine files
- **WHEN** two engine files exist, the first engine refuses the connection, and the second answers
- **THEN** the CLI deletes the first file and uses the second engine

#### Scenario: CLI with a hung engine
- **WHEN** an engine file lists an engine that times out
- **THEN** the CLI prints that the engine did not answer, exits 1, leaves the engine file and does not open the cache file

#### Scenario: Cache routes on store failure
- **WHEN** the store raises `sqlite3.Error` during `GET /api/cache/stats` or `DELETE /api/cache`
- **THEN** the route answers HTTP 503 with a short message

#### Scenario: Key change clears LSE rows
- **WHEN** the user saves an LSE key that differs from the previous one, or saves a key when none was set
- **THEN** the `lse` rows are cleared; saving the same key again, or signing out, clears nothing

### Requirement: Regression cases from the model
`tests/test_cache.py` SHALL carry one test per counter-example in `design-review.md`: `cx_tail`, `cx_edge`, `cx_trunc`, `cx_count`, `cx_lock`, `cx_irr`, `cx_transient`, `cx_skip`, `cx_lag`, `cx_restate`. The last three SHALL assert the documented, accepted outcome.

#### Scenario: Model and tests agree
- **WHEN** `tlc design.tla -config design.cfg` passes
- **THEN** every `cx_*` test passes against the implementation

