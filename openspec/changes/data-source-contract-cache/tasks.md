## 1. Contract, registry, harness (PR 1, no behaviour change)

- [ ] 1.1 Add `TIMEFRAME_SECONDS` to `contracts/types.py`. Import it in `demo.py` and `userdata.py`. Delete their private tables.
- [ ] 1.2 Add `is_custom`, `is_broker`, `cacheable` attributes to `Provider`. Set `is_broker = True` in `BrokerProvider` through the attribute. Set `cacheable = False` in `demo`, `userdata`, `BrokerProvider`.
- [ ] 1.3 Add the nine optional methods to `Provider` with `NotSupported` defaults and docstrings that name the required row keys.
- [ ] 1.4 Extend `capabilities()` with the six new names.
- [ ] 1.5 `load_plugins`: default `is_custom = True` for entry-point providers.
- [ ] 1.6 `check_provider`: one block per claimed capability. Required keys, `limit` respected, `NotSupported` from a claimed capability is a violation.
- [ ] 1.7 Tests: `test_demo_provider.py` still green. New `test_contract.py`: capability detection, harness rejects a claimed-but-raising method, `TIMEFRAME_SECONDS` has no `tick`.

## 2. Server routes and UI (PR 2, no behaviour change for LSE-only users)

- [ ] 2.1 Add `_source(cap, provider)` and `_lse_only()` to `server.py`.
- [ ] 2.2 Migrate `/api/prices`, `/api/screener`, `/api/logos` to `_source`.
- [ ] 2.3 Migrate `/api/economic-calendar`, `/api/macro/catalog`, `/api/macro/series`, `/api/options/underlyings`, `/api/options/chain`, `/api/options/flow`. Add the optional `provider` parameter. Delete `econ_cache`, `macro_cat_cache`, `macro_series_cache`, `opt_cache`, `_opt_cached`, `_lse_for_options`, `_lse_for_macro`.
- [ ] 2.4 Migrate the assistant tool defaults, `_ml_econ_frame`, the quant fit-info flag, and the quant fit `lse-options` source.
- [ ] 2.5 Keep the databank download on `_lse_only()`.
- [ ] 2.6 `app.js`: add `capParam(cap)`. Apply at the nine call sites and the two probes.
- [ ] 2.7 Tests: `FullStubProvider` in `tests/`. One route test per migrated endpoint: explicit provider, fallback, 404 on missing capability, 409 on unconfigured. `/api/providers` shows the stub as custom with the full capability list.

## 3. Cache (PR 3, behind `LSE_TERMINAL_CACHE`)

- [ ] 3.1 `engine/cache.py`: `SqliteStore` with the three tables and the five-method `RangeStore` interface, plus `snapshot_get`/`snapshot_put`.
- [ ] 3.2 `CachedProvider`: `__getattr__` delegation, `candles` with rules D7.1 to D7.8, the six snapshot methods with TTLs from D8, per-key locks.
- [ ] 3.3 `Registry.register` wraps when `cacheable`, switch on, not hosted. Switch: `LSE_TERMINAL_CACHE=off`.
- [ ] 3.4 Routes `DELETE /api/cache` and `GET /api/cache/stats`, local mode only. CLI `lset cache clear|stats`.
- [ ] 3.5 Tests `tests/test_cache.py`, the ten cases from the design review: repeat window, no partial bar, gap fill, holiday coverage, truncation, limit-only, same-key lock, cross-key no wait, clear, bypass and off switch.
- [ ] 3.6 README: one paragraph on the cache file and the switch.

## 4. moomoo plugin (separate repository `lse-terminal-moomoo`)

- [ ] 4.1 Verify against a live OpenD: intraday `time_key` marks bar open or bar close. Record the answer in the plugin README and pin it with a test.
- [ ] 4.2 Package skeleton: `pyproject.toml` with the entry point, deps `lse-terminal`, `moomoo-api`.
- [ ] 4.3 `MoomooProvider`: config, `configured()` with TCP probe and quota text, lazy context with lock and rebuild.
- [ ] 4.4 `search` with watchlist groups and market catalogs, 1 h in-memory TTL, prefix then substring ranking.
- [ ] 4.5 `candles` with paging, `AuType.NONE`, zone conversion per market.
- [ ] 4.6 `quote`, `prices`, `stream` with the asyncio bridge and `QuoteSynthesizer`.
- [ ] 4.7 `options_underlyings`, `option_chain` with snapshot enrichment.
- [ ] 4.8 Tests: `check_provider` against a fake `OpenQuoteContext`, zone test per market, reconnect test, live smoke test gated on `MOOMOO_OPEND`.
- [ ] 4.9 README: OpenD setup, config, quota, which surfaces fall back to LSE.

## 5. Close

- [ ] 5.1 `openspec validate data-source-contract-cache` clean.
- [ ] 5.2 Claude review subagent over `git diff main...` per PR, Opus for PR 1 to 3, findings folded.
- [ ] 5.3 `openspec archive data-source-contract-cache` after PR 3 merges.
