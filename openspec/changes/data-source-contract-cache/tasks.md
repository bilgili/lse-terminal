## 1. Contract, registry, harness (PR 1, no behaviour change)

- [x] 1.1 Add `TIMEFRAME_SECONDS` and `to_epoch()` to `contracts/types.py`. `userdata.py` imports the table. `demo.py` uses `{**TIMEFRAME_SECONDS, "tick": 1}` and keeps `tick` in its timeframes.
- [x] 1.2 Add `is_custom`, `is_broker`, `cacheable`, `settle_seconds`, `snapshot_ttl` attributes to `Provider`. `BrokerProvider` sets `is_broker` through the attribute. `demo`, `userdata`, `BrokerProvider` set `cacheable = False`.
- [x] 1.3 Record `tests/fixtures/lse_rows.json`: one row per surface from a real LSE key. Build the `TypedDict` row classes in `contracts/types.py` from the fixture and from the keys `app.js` reads.
- [x] 1.4 Add the nine optional methods to `Provider` with `NotSupported` defaults and docstrings that name the row type. Add the candle clauses (half-open, complete, contiguous, raise on failure) to the `candles` docstring.
- [x] 1.5 Extend `capabilities()` with `prices`, `screener`, `logos`, `options`, `flow`, `calendar`, `macro`.
- [x] 1.6 `load_plugins`: sort entry points by name; default `is_custom = True`.
- [x] 1.7 `check_provider`: one block per claimed capability with `__required_keys__`; candle checks for `ts < end`, ascending, and prefix-under-larger-limit; a claimed method that raises `NotSupported` is a violation.
- [x] 1.8 Tests: `test_demo_provider.py` still green, including `tick`. New `test_contract.py`: capability detection, harness rejects a claimed-but-raising method, inclusive end is a violation, LSE fixture passes, `TIMEFRAME_SECONDS` has no `tick`, `to_epoch` on the four input shapes.

## 2. Server routes and UI (PR 2, no behaviour change for LSE-only users)

- [x] 2.1 Add `_source(cap, provider)` and `_lse_only()` to `server.py`.
- [x] 2.2 Migrate the global routes `/api/screener`, `/api/logos`, `/api/options/underlyings`, `/api/economic-calendar`, `/api/macro/catalog`, and `/api/options/flow` without underlying. Add the optional `provider` parameter.
- [x] 2.3 Migrate the symbol-scoped routes `/api/prices`, `/api/options/chain`, `/api/macro/series`, and `/api/options/flow` with underlying. Optional `provider`; without it, the default source (D2: first registered with the capability, configured or not). An explicit provider never falls back. Every capability route sets `X-Source`.
- [x] 2.4 Merge `econ_cache`, `macro_cat_cache`, `macro_series_cache` and `opt_cache` into one TTL helper keyed by resolved provider, route tag and arguments; resolver before lookup; per-tag bounds. Keep the TTL values. Delete `_opt_cached`, `_lse_for_options`, `_lse_for_macro`.
- [x] 2.5 Migrate `_ml_econ_frame`, the quant fit-info flag, and the quant fit `lse-options` source with explicit `provider="lse"`. The assistant `get_candles` and backtest defaults name a candle source, not a capability; they stay.
- [x] 2.6 Keep the databank download on `_lse_only()`.
- [ ] 2.7 `app.js`: add `capParam(cap)` for listing calls. Symbol calls from a listing send its `X-Source` as `provider`, tape clicks included. The options page saves `{und, src}`; the trade dock keys its list by source. Render 404 as "not supported by this source". Call sites with `provider=lse` keep it.
- [x] 2.8 Tests: `FullStubProvider` in `tests/`. One route test per migrated endpoint: explicit provider, default source without provider (LSE even when a configured plugin has the capability), `X-Source` header, sign-out inside the TTL answers 409, 404 on missing capability, 409 on unconfigured, no cross-source call on a scoped 404. `/api/providers` shows the stub as custom with the full capability list. Grep gate for `reg.get("lse")`. Plugin sort order.
- [x] 2.9 `LseProvider._gate_ts` calls `to_epoch` and only formats the result. Today it parses bounds with its own rules, so `"20240630"` is a date there and epoch seconds in `to_epoch`.
- [x] 2.10 `engine/quant_fit.py` uses `TIMEFRAME_SECONDS` instead of its own timeframe table, which lacks `30s`.
- [x] 2.11 `server.py` reads `is_custom`, `is_broker` and `logos` as attributes, not through `getattr(..., default)`. The base class defines all three since PR 1.
- [x] 2.12 `load_plugins` records a `plugin_errors` entry when a plugin replaces an existing provider name (not `Registry.register`: `set_key`, `clear_key` and broker reconnects replace providers on purpose), so a silent override of `lse` or `userdata` is visible. `load_plugins` also states in a `plugin_errors` entry when it cannot set `is_custom` on a plugin (`__slots__` or a read-only property), so the load failure names its cause.
- [ ] 2.13 Deferred: `BrokerProvider.candles` returns the oldest N bars for a start-anchored call. The adapter protocol `bars.history` takes only `count`, so the fix needs an anchor field in the adapter protocol first. The `ponytail:` comment stays.
- [x] 2.14 `to_epoch` reads exactly eight unsigned ASCII digits as `YYYYMMDD` UTC (design D3). Test it.

## 3. Cache (PR 3, behind `LSE_TERMINAL_CACHE`)

- [ ] 3.1 `engine/cache.py`: `SqliteStore` with the three tables, per-thread connections, `busy_timeout`, the five-method `RangeStore` interface with `empty_until`, `snapshot_get`/`snapshot_put` with pruning and `jsonable_encoder`.
- [ ] 3.2 `CandleCache`: store, per-key locks, hosted flag, `clear(key)` and global clear under locks.
- [ ] 3.3 `CachedProvider` as a plain class with `__getattr__`: rules D7.1 to D7.8 (bypass, bounds, windowed fill with `last_ts + 1` paging, limit-only with run counting, empty coverage, tail from `edge`, read under lock), sign-out pass-through, seven snapshot methods with TTL override and canonical keys.
- [ ] 3.4 `Registry(cache=...)`; `register` wraps when `cacheable`, switch on, not hosted. `create_app` builds the `CandleCache`.
- [ ] 3.5 Routes `DELETE /api/cache` and `GET /api/cache/stats`, local mode only. CLI `lset cache clear|stats` through the engine, direct file only when no engine runs.
- [ ] 3.6 Tests `tests/test_cache.py`, fake source with a clock and a call counter. The ten listed cases: repeat window, no partial bar, gap fill, holiday coverage, truncation, limit-only, same-key lock, cross-key no wait, clear, bypass and off switch. Plus one test per model counter-example: `cx_tail`, `cx_edge`, `cx_trunc`, `cx_count`, `cx_lock`, `cx_irr`, `cx_transient`, `cx_skip`, `cx_lag`, `cx_restate`. Plus wrapper transparency, lock survives re-registration, sign-out pass-through, end-only bypass, empty coverage heals, snapshot canonical key and provider override.
- [ ] 3.7 README: one paragraph on the cache file, the switch, and `lset cache clear`.
- [ ] 3.8 Review gate: run `tlc design.tla -config design.cfg` and every `design.cx_*.cfg` whenever D7 changes; the `cx_*` runs must still fail.
- [ ] 3.9 Delete the PR 2 route snapshot helper when the wrapper snapshot cache lands. Keep a TTL for the seven snapshot methods when the engine is hosted or `LSE_TERMINAL_CACHE=off`.

## 4. moomoo plugin (separate repository `lse-terminal-moomoo`)

- [ ] 4.1 Verify against a live OpenD: intraday `time_key` marks bar open or bar close. Record the answer in the plugin README and pin it with a test.
- [ ] 4.2 Package skeleton: `pyproject.toml` with the entry point, deps `lse-terminal`, `moomoo-api`.
- [ ] 4.3 `MoomooProvider`: config, `configured()` from a cached TCP probe refreshed by a background thread, quota text, lazy context with lock and rebuild, `settle_seconds`.
- [ ] 4.4 `search` with watchlist groups and market catalogs, 1 h in-memory TTL, prefix then substring ranking.
- [ ] 4.5 `candles` with paging to `limit`, `AuType.NONE`, inclusive-to-half-open end conversion, zone conversion per market, SDK error to exception.
- [ ] 4.6 `quote`, `prices`, `stream` with the asyncio bridge and `QuoteSynthesizer`.
- [ ] 4.7 `options_underlyings`, `option_chain` with snapshot enrichment.
- [ ] 4.8 Tests: `check_provider` against a fake `OpenQuoteContext`, zone test per market, reconnect test, error-to-exception test, live smoke test gated on `MOOMOO_OPEND`.
- [ ] 4.9 README: OpenD setup, config, quota, which surfaces fall back to LSE and which show "not supported".

## 5. Close

- [ ] 5.1 `openspec validate data-source-contract-cache` clean.
- [ ] 5.2 Claude review subagent over `git diff main...` per PR, Opus for PR 1 to 3, findings folded.
- [ ] 5.3 `openspec archive data-source-contract-cache` after PR 3 merges.
