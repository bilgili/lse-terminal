## Why

The terminal has a `Provider` contract and a plugin loader, but only candles, search, quote and stream go through it. Options, the economic calendar, macro series, the screener, the price board and logos call the LSE provider by name. A third-party source (moomoo, IBKR, yfinance) cannot serve those surfaces, and the directory gate hides it from the UI. Every candle read also goes to the network, so a backtest or a chart re-pulls history the terminal already saw.

## What Changes

- Lift the six LSE-only surfaces into the `Provider` base class as optional methods. Each raises `NotSupported` by default. `capabilities()` reports the ones a provider overrides.
- Add one source resolver in the engine. A route takes an optional `provider` query parameter. Without it, the engine picks the first configured source that has the capability. The resolver replaces every `reg.get("lse")` except the databank download, which stays an LSE product feature.
- Move the timeframe-to-seconds table into the contract module. `demo` and `userdata` drop their private copies.
- Default `is_custom = True` for every provider that arrives through an entry point, so the directory gate never hides a plugin.
- Add a read-through cache in front of every cacheable provider. SQLite file, closed bars only, coverage intervals, per-key fill lock, TTL snapshots for the dict-shaped surfaces. The four TTL dicts in the server delete. One switch turns the cache off.
- Extend the compliance harness with one check per lifted capability.
- Ship a moomoo provider as a separate pip package on the OpenD SDK. It serves search, candles, quote, stream, prices and the options chain. **Note:** that package is a separate repository. This change tracks it as a task, not as a spec of this repository.

## Capabilities

### New Capabilities
- `data-provider-contract`: the `Provider` base class, its optional methods, its attributes, the shared timeframe table and the compliance harness.
- `data-source-routing`: how an engine route picks the provider for a capability, the error codes it returns, and how plugins reach the UI.
- `market-data-cache`: the read-through cache for candles and the TTL cache for snapshot surfaces, the store schema, the fill rules and the off switch.

### Modified Capabilities
<!-- openspec/specs/ is empty. No existing capability changes. -->

## Impact

- `lse_terminal/contracts/provider.py`, `types.py`: base class and shared table.
- `lse_terminal/engine/registry.py`: plugin default, cache wrapping.
- `lse_terminal/engine/cache.py`: new module.
- `lse_terminal/engine/server.py`: one resolver, twelve route edits, four cache dicts removed, two cache routes added.
- `lse_terminal/providers/demo.py`, `userdata.py`: drop the private timeframe table, set `cacheable = False`.
- `lse_terminal/engine/broker_hub.py`: `is_broker` becomes a contract attribute.
- `lse_terminal/testing.py`: per-capability checks.
- `lse_terminal/ui/static/app.js`: one helper, nine call sites.
- `lse_terminal/cli.py`: `lset cache clear|stats`.
- New dependency: none. SQLite is stdlib.
- New file on disk: `<config_dir>/cache.sqlite`.
- Separate repository: `lse-terminal-moomoo`, depends on `lse-terminal` and `moomoo-api`.
