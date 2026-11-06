## ADDED Requirements

### Requirement: Optional data surfaces on the Provider base class
The `Provider` base class SHALL define `prices`, `screener`, `logos`, `options_underlyings`, `option_chain`, `options_flow`, `economic_calendar`, `macro_catalog` and `macro_series` as optional methods. Each default implementation SHALL raise `NotSupported`. The signatures SHALL match the LSE provider's signatures at the time of this change.

#### Scenario: A provider does not implement a surface
- **WHEN** the engine calls `option_chain` on a provider that does not override it
- **THEN** the call raises `NotSupported`

#### Scenario: The LSE provider keeps its surfaces
- **WHEN** `LseProvider` is loaded after this change
- **THEN** every lifted method on it overrides the base method with the same signature

### Requirement: Capability detection
`capabilities()` SHALL return `search` and `candles` always, and SHALL add `quote`, `stream`, `prices`, `screener`, `logos`, `options`, `flow`, `calendar` and `macro` for each method group the provider overrides. `options` SHALL mean `options_underlyings` and `option_chain` are both overridden. `flow` SHALL mean `options_flow` is overridden. `macro` SHALL mean `macro_catalog` and `macro_series` are both overridden.

#### Scenario: Partial options support
- **WHEN** a provider overrides `option_chain` and not `options_underlyings`
- **THEN** `capabilities()` does not contain `options`

#### Scenario: Full surface
- **WHEN** a provider overrides every optional method
- **THEN** `capabilities()` contains all eleven names

### Requirement: Contract attributes
`Provider` SHALL define `is_custom` (default `False`), `is_broker` (default `False`), `cacheable` (default `True`), `settle_seconds` (default `60`) and `snapshot_ttl` (default empty mapping) as class attributes.

#### Scenario: Built-in local sources opt out of the cache
- **WHEN** `DemoProvider`, `UserDataProvider` or `BrokerProvider` is inspected
- **THEN** `cacheable` is `False`

### Requirement: configured() does no I/O
`configured()` SHALL answer from state the provider already holds. It SHALL NOT open a socket, read a file or call a network service.

#### Scenario: Resolver calls every provider
- **WHEN** a route resolves a source and ten providers are registered
- **THEN** no provider performs I/O inside `configured()`

### Requirement: Candle bound semantics
`candles(symbol, timeframe, limit, start, end)` SHALL treat `start` as inclusive and `end` as exclusive. A bar SHALL be in the answer only when `start <= ts < end` for the bounds given. The answer SHALL be ascending by `ts` with no duplicate `ts`.

#### Scenario: Bar at end is excluded
- **WHEN** a provider holds a bar at `ts = 100` and the call has `end = 100`
- **THEN** the answer does not contain that bar

### Requirement: Candle answer completeness
A `candles` answer with fewer than `limit` rows SHALL contain every bar the source holds in the asked range. An answer SHALL be contiguous: the source SHALL NOT hold a bar between the first and the last `ts` of the answer that the answer omits. With `start` given, a truncated answer SHALL hold the oldest `limit` bars. Without `start`, a truncated answer SHALL hold the newest `limit` bars.

#### Scenario: Short answer is complete
- **WHEN** a call asks `limit=5000` for a range and the source holds 300 bars there
- **THEN** the answer holds exactly those 300 bars

#### Scenario: Truncated windowed answer is the oldest prefix
- **WHEN** a call asks `[0, 1000)` with `limit=10` and the source holds 50 bars there
- **THEN** the answer holds the 10 oldest bars of that range

### Requirement: Candles raise on failure
`candles` SHALL raise an exception when the source call fails. An empty frame SHALL mean the source holds no bars in the asked range.

#### Scenario: SDK error
- **WHEN** a vendor SDK returns an error code for a history request
- **THEN** the provider raises and does not return an empty frame

### Requirement: Shared timeframe table and bound parser
`lse_terminal.contracts.types` SHALL export `TIMEFRAME_SECONDS`, a mapping from timeframe label to bar length in seconds, and `to_epoch(bound)`, which converts an ISO string with offset, a date-only string, an epoch string or an int to epoch seconds UTC. The table SHALL contain `1s, 30s, 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w` and SHALL NOT contain `tick`. `userdata` SHALL use this table and SHALL NOT keep a private copy. `demo` SHALL extend it with `tick` and SHALL keep `tick` in its timeframes.

#### Scenario: Tick is not a fixed-length bar
- **WHEN** code looks up `tick` in `TIMEFRAME_SECONDS`
- **THEN** the lookup fails with `KeyError`

#### Scenario: Demo keeps tick
- **WHEN** `DemoProvider().timeframes` is read after this change
- **THEN** it contains `tick`

### Requirement: Required row keys per surface
`lse_terminal.contracts.types` SHALL define one `TypedDict` per surface row: `PriceRow`, `ChainRow`, `FlowRow`, `CalendarRow`, `MacroCatalogRow`, `MacroSeriesRow`, `UnderlyingRow`, and the shapes of `logos` (symbol to `{light, dark}`) and `screener` (`{columns, data}`, as recorded in the fixture). Required keys SHALL be the keys present in the recorded LSE fixture `tests/fixtures/lse_rows.json` that `app.js` or the engine reads. Each lifted method's docstring SHALL name its row type.

#### Scenario: LSE fixture passes
- **WHEN** `check_provider` runs against a provider that returns the fixture rows
- **THEN** it reports no violation

#### Scenario: A plugin returns a row with a missing key
- **WHEN** `check_provider` calls a claimed `option_chain` and a row lacks a required key
- **THEN** the harness reports a violation that names the method and the key

### Requirement: Compliance harness covers claimed capabilities and candle clauses
`check_provider` SHALL call each optional method the provider claims in `capabilities()`, SHALL check the required row keys, SHALL check that `limit` is respected where the method takes one, and SHALL report a violation when a claimed method raises `NotSupported`. For `candles` it SHALL check half-open bounds (`ts < end`), ascending `ts`, and that a windowed answer with `limit=k` is a prefix of the same call with a larger limit.

#### Scenario: Claimed capability raises
- **WHEN** a provider overrides `screener` with a method that raises `NotSupported`
- **THEN** `check_provider` returns a violation for `screener`

#### Scenario: Unclaimed capability is not called
- **WHEN** a provider does not override `options_flow`
- **THEN** `check_provider` does not call `options_flow`

#### Scenario: Inclusive end is a violation
- **WHEN** a provider returns a bar with `ts == end`
- **THEN** `check_provider` reports a violation for bound semantics
