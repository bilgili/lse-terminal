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
`Provider` SHALL define `is_custom` (default `False`), `is_broker` (default `False`) and `cacheable` (default `True`) as class attributes.

#### Scenario: Built-in local sources opt out of the cache
- **WHEN** `DemoProvider`, `UserDataProvider` or `BrokerProvider` is inspected
- **THEN** `cacheable` is `False`

### Requirement: Shared timeframe table
`lse_terminal.contracts.types` SHALL export `TIMEFRAME_SECONDS`, a mapping from timeframe label to bar length in seconds. The table SHALL contain `1s, 30s, 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w` and SHALL NOT contain `tick`. `demo` and `userdata` SHALL use this table and SHALL NOT keep a private copy.

#### Scenario: Tick is not a fixed-length bar
- **WHEN** code looks up `tick` in `TIMEFRAME_SECONDS`
- **THEN** the lookup fails with `KeyError`

### Requirement: Required row keys per surface
The docstring of each lifted method SHALL name the keys a returned row MUST contain. `option_chain` rows MUST contain `ticker, type, strike, expiry, bid, ask, last, iv, volume, oi`. `economic_calendar` rows MUST contain `ts, region, event, actual, previous, consensus, importance`. `prices` rows MUST contain `symbol, price, bid, ask, ts`. `options_underlyings`, `macro_catalog` rows MUST contain `symbol, name`. `macro_series` rows MUST contain `ts, value`. `logos` MUST map symbol to `{light, dark}`. `screener` MUST return `{columns, rows}`.

#### Scenario: A plugin returns a row with a missing key
- **WHEN** `check_provider` calls a claimed `option_chain` and a row lacks `strike`
- **THEN** the harness reports a violation that names the method and the key

### Requirement: Compliance harness covers claimed capabilities
`check_provider` SHALL call each optional method the provider claims in `capabilities()`, SHALL check the required row keys, SHALL check that `limit` is respected where the method takes one, and SHALL report a violation when a claimed method raises `NotSupported`.

#### Scenario: Claimed capability raises
- **WHEN** a provider overrides `screener` with a method that raises `NotSupported`
- **THEN** `check_provider` returns a violation for `screener`

#### Scenario: Unclaimed capability is not called
- **WHEN** a provider does not override `options_flow`
- **THEN** `check_provider` does not call `options_flow`
