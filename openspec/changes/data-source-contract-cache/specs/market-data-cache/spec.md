## ADDED Requirements

### Requirement: Cache wrapper at registration
`Registry.register` SHALL wrap a provider in `CachedProvider` when the provider's `cacheable` is `True`, the environment variable `LSE_TERMINAL_CACHE` is not `off`, and the engine is not in hosted mode. The wrapper SHALL delegate every attribute it does not override to the wrapped provider.

#### Scenario: Cache off
- **WHEN** `LSE_TERMINAL_CACHE=off` and the registry registers `LseProvider`
- **THEN** `reg.get("lse")` is the unwrapped provider

#### Scenario: Wrapper is transparent
- **WHEN** the registry wraps a provider
- **THEN** `name`, `title`, `timeframes`, `capabilities()`, `configured()` and `stream` return what the wrapped provider returns

### Requirement: Store schema
The store SHALL be one SQLite file at `<config_dir>/cache.sqlite` in WAL mode with tables `bars`, `coverage` and `snapshots` as defined in the design. `coverage` ranges SHALL be half-open `[start, end)` and SHALL merge overlapping or adjacent ranges on insert.

#### Scenario: Adjacent ranges merge
- **WHEN** coverage holds `[0, 100)` and the store adds `[100, 200)`
- **THEN** coverage holds one range `[0, 200)`

### Requirement: Only closed bars are stored
With `horizon = now - min(step, 60)`, the wrapper SHALL store a bar only when `ts + step <= horizon`. Coverage SHALL never extend past `horizon`.

#### Scenario: Forming bar is not stored
- **WHEN** the source returns a bar with `ts + step > horizon`
- **THEN** the bar is in the response and not in `bars`

### Requirement: Live tail is read from the source
When the request window reaches the present, the wrapper SHALL call the source with `limit=2`, SHALL upsert the closed bar, and SHALL append the open bar to the response.

#### Scenario: Repeat poll
- **WHEN** a chart polls the same symbol and timeframe twice within one bar
- **THEN** the second poll makes exactly one source call, with `limit=2`

### Requirement: Windowed gap fill
For a call with `start`, the wrapper SHALL compute the gaps between `[start, end)` and coverage, SHALL make one source call per gap with `limit=5000`, SHALL record coverage `[gap_start, min(gap_end, last_ts + step, horizon))`, and SHALL return the store's bars in the window with `head(limit)` applied.

#### Scenario: Gap between two covered windows
- **WHEN** coverage holds `[0, 100)` and `[200, 300)` and a call asks `[0, 300)`
- **THEN** the wrapper makes one source call for `[100, 200)`

#### Scenario: Truncated answer
- **WHEN** the source returns 5000 bars for a gap that spans more
- **THEN** coverage ends at `last_ts + step` and the remainder stays a gap

#### Scenario: Holiday
- **WHEN** the source returns zero bars for a gap
- **THEN** coverage records the gap and the next call makes no source call for it

### Requirement: Limit-only fill
For a call without `start`, the wrapper SHALL serve from the store when coverage reaches `horizon` and the store holds at least `limit` closed bars. Otherwise it SHALL call the source with the same `limit`, SHALL upsert the closed bars, SHALL record coverage `[first_ts, horizon)`, and SHALL apply `tail(limit)`.

#### Scenario: First and second call
- **WHEN** a backtest asks `limit=5000` twice for a cold key
- **THEN** the first call reaches the source with `limit=5000` and the second reaches it only for the live tail

### Requirement: Per-key fill lock
The wrapper SHALL hold one lock per `(provider, symbol, timeframe)` around gap computation and fill. Bars and coverage SHALL write in one transaction, bars first.

#### Scenario: Two threads, one key
- **WHEN** two threads request the same uncovered window at once
- **THEN** the source receives one call for that window

#### Scenario: Two threads, two keys
- **WHEN** two threads request different symbols at once
- **THEN** neither waits for the other's source call

### Requirement: Bypass
The wrapper SHALL pass `tick` and any timeframe absent from `TIMEFRAME_SECONDS` straight to the source without reading or writing the store.

#### Scenario: Tick passthrough
- **WHEN** a call asks for `tick`
- **THEN** the store is not touched

### Requirement: Snapshot cache
The wrapper SHALL cache `options_underlyings` (60 s), `option_chain` (15 s), `options_flow` (15 s), `economic_calendar` (60 s), `macro_catalog` (60 s), `macro_series` (600 s) and `logos` (3600 s) in `snapshots`, keyed by provider, method and sorted arguments. `prices` and `screener` SHALL pass through.

#### Scenario: Chain within TTL
- **WHEN** the options board asks the same chain twice within 15 s
- **THEN** the source receives one call

#### Scenario: Snapshot survives restart
- **WHEN** the engine restarts within a snapshot's TTL
- **THEN** the next call serves the stored snapshot

### Requirement: Invalidation
`DELETE /api/cache` with optional `provider`, `symbol`, `timeframe` SHALL drop matching rows from `bars`, `coverage` and `snapshots`. `GET /api/cache/stats` SHALL return row counts per table and the file size. Both routes SHALL be local-mode only. `lset cache clear` and `lset cache stats` SHALL call the same functions.

#### Scenario: Clear one key
- **WHEN** `DELETE /api/cache?provider=lse&symbol=AAPL&timeframe=1d` runs
- **THEN** the next `1d` call for `AAPL` on `lse` reaches the source for the full window
