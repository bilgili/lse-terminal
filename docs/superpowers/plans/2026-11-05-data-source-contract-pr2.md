# Data-source contract PR 2: routes and UI — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The engine picks the source of each capability route through one resolver, and no capability route names LSE.

**Architecture:** `server.py` gets one resolver `_source(cap, provider)` (design D2) and one bounded TTL helper `_snap(...)` keyed by provider name. Each capability route resolves its source, calls the contract method, and maps errors. `app.js` gets one helper `capParam(cap)` (design D10). Contract fixes from the PR 1 review land in the contract modules.

**Tech stack:** Python 3.10+, FastAPI, pytest, ruff, vanilla JS.

**Spec:** `openspec/changes/data-source-contract-cache/` (design D2, D3, D10; `specs/data-source-routing/spec.md`; tasks 2.1 to 2.14).

**Branch:** `feat/pr2-routes` in worktree `../lse-terminal-pr2`, cut from `develop`. Merge back into `develop`, not `main`.

**Test command:** `python3 -m pytest -q` from the worktree root. Baseline: 192 passed, 3 skipped, 2 known failures (`test_broker_list_names_each_broker_from_its_own_handshake`, `test_probe_does_not_open_a_session`; both need the `brue-connect` adapter that is not cloned). Lint: `ruff check lse_terminal tests`.

---

## File map

| File | Owner of | Change |
|---|---|---|
| `lse_terminal/contracts/types.py` | `to_epoch` | 8-digit rule (2.14) |
| `lse_terminal/providers/lse.py` | `_gate_ts` | parse through `to_epoch` (2.9) |
| `lse_terminal/engine/quant_fit.py` | `_tf_label` | iterate `TIMEFRAME_SECONDS` (2.10) |
| `lse_terminal/engine/registry.py` | `register`, `load_plugins` | name-overwrite and `is_custom` errors (2.12) |
| `lse_terminal/engine/server.py` | routes | resolver, snapshot helper, migrations (2.1 to 2.6, 2.11) |
| `lse_terminal/ui/static/app.js` | UI fetches | `capParam` (2.7) |
| `tests/test_contract.py`, `tests/test_registry.py` | | Task 1 tests |
| `tests/test_routing.py` (new) | | Task 2 tests (2.8) |

---

### Task 1: Contract fixes (2.9, 2.10, 2.12, 2.14)

**Files:** `contracts/types.py`, `providers/lse.py`, `engine/quant_fit.py`, `engine/registry.py`, `tests/test_contract.py`, `tests/test_registry.py`.

- [ ] **1.1 Write failing tests.**
  - `to_epoch("20240630") == 1719705600`. `to_epoch("-20240630")` stays epoch seconds (`-20240630`). `to_epoch("1719705600") == 1719705600`. `to_epoch("2024063")` (7 digits) stays epoch seconds.
  - `LseProvider._gate_ts` gives the same string as today for: `None`, `""`, `1719705600`, `"1719705600"`, `"2024-06-30"`, `"2024-06-30T12:00:00+00:00"`, `"2024-06-30T12:00:00Z"`, `"20240630"`. For an unparseable string it returns the string unchanged (the gate reports the error).
  - `quant_fit._tf_label(30.0) == "30s"`. The other labels do not change.
  - `Registry.register` of a second provider with an existing name appends one `plugin_errors` entry that names the provider and says it replaced an existing one. The new provider wins, as today.
  - `load_plugins` with a plugin whose `is_custom` cannot be set (a class with `__slots__` and no `is_custom` slot, or a read-only property) appends a `plugin_errors` entry that names the plugin and says `is_custom` could not be set. Monkeypatch `entry_points` as the PR 1 registry tests do.
- [ ] **1.2 Run the tests; they fail.**
- [ ] **1.3 Implement.**
  - `to_epoch`: before the digit branch, when `len(s) == 8 and s.isascii() and s.isdigit()`, return `int(datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())`. Update the docstring.
  - `_gate_ts`: `ts = to_epoch(value)` inside `try`; on `ValueError` return the stripped string; `None` gives `None`; else format `datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")`. Delete the private parse rules.
  - `_tf_label`: iterate `TIMEFRAME_SECONDS.items()` (ascending order in the table) instead of the literal tuple.
  - `register`: when `provider.name in self._providers`, append `f"provider {provider.name!r} replaced an existing provider of the same name"` to `plugin_errors` before the assignment.
  - `load_plugins`: wrap `provider.is_custom = True` in its own `try`; on `AttributeError`/`TypeError`, append `f"provider plugin {ep.name!r} failed to load: cannot set is_custom ({e})"` and skip the plugin.
- [ ] **1.4 Run the full suite and ruff.** Expected: baseline plus the new tests.
- [ ] **1.5 Tick 2.9, 2.10, 2.12, 2.14 in `tasks.md`. Commit.**

### Task 2: Resolver and route migration (2.1 to 2.6, 2.8, 2.11)

**Files:** `engine/server.py`, `tests/test_routing.py` (new), `tasks.md`.

Design: one owner. `_source` is the only place that turns `(cap, provider)` into a provider. `_snap` is the only route-level cache. Routes contain no source logic.

- [ ] **2.1 Write `tests/test_routing.py` first.** A `FullStubProvider(Provider)` with `name = "stub"`, `configured()` controlled by a flag, and every lifted method overridden, returning one fixture-shaped row (use `tests/fixtures/lse_rows.json`) and counting calls. Register it through a monkeypatched `load_plugins` in a `client` fixture like `tests/test_api.py`. Tests, one per migrated route where it applies:
  - Explicit `provider=stub` returns the stub rows.
  - Without `provider` and LSE unconfigured, the route uses `stub` (the default source).
  - Without `provider` and no configured source with the capability, the route answers 409.
  - `provider=demo` answers 404 and the stub call counter stays at 0 (no cross-source call).
  - `provider=stub` with the stub unconfigured answers 409.
  - Unknown provider answers 404, except `/api/logos`, which answers `{}` with 200 for unknown, lacking, unconfigured and failing sources.
  - A `NotSupported` from the method answers 404; any other exception answers 502.
  - `/api/options/chain?underlying=X` with `provider=lse` cached and then `provider=stub` reaches the stub (cache key starts with the provider name). A second identical stub call inside the TTL does not reach the stub.
  - Catalog and series without `provider` both reach the same source.
  - `/api/providers` lists `stub` with `custom: true` and the full capability list.
  - Grep gate: in `server.py` source, every `reg.get("lse")` is inside `def _lse_only`.
  - `/api/quant/fit-info` reports `lse_options` false while LSE is unconfigured; `source="lse-options"` resolves `provider="lse"` and answers the 409 detail, not the stub.
- [ ] **2.2 Run; they fail.**
- [ ] **2.3 Add the resolver and the helper** next to the route block, inside `create_app`:

```python
    # Capability -> the 409 text the UI shows when no source can answer.
    # Kept word for word from the pre-resolver routes.
    _NO_SOURCE = {
        "calendar": "LSE API key not set. Add your free key to load the economic calendar.",
        "macro": "LSE API key not set. Add your free key to load macro data.",
        "options": "LSE API key not set. Add your free key under MARKETS to load options data.",
        "flow": "LSE API key not set. Add your free key under MARKETS to load options data.",
    }

    def _source(cap: str, provider: str | None = None):
        """The provider that serves `cap` (design D2). Explicit provider:
        that one or 404/409, never another source. None: the first
        registered provider that has `cap` and is configured, else 409."""
        if provider:
            try:
                p = reg.get(provider)
            except ValueError as e:
                raise HTTPException(404, str(e))
            if cap not in p.capabilities():
                raise HTTPException(404, f"{provider} does not support {cap}")
            if not p.configured():
                raise HTTPException(409, (_NO_SOURCE.get(cap) if provider == "lse"
                                          else None) or f"{provider} is not configured")
            return p
        for p in reg.all():
            if cap in p.capabilities() and p.configured():
                return p
        raise HTTPException(409, _NO_SOURCE.get(cap, f"no configured source has {cap}"))

    def _lse_only():
        """The databank download is an LSE product feature, not a data
        surface (design D2), so it alone names the provider."""
        try:
            p = reg.get("lse")
        except ValueError:
            raise HTTPException(404, "LSE provider not available")
        if not p.configured():
            raise HTTPException(409, _NO_SOURCE["options"])
        return p

    # One bounded TTL cache for the snapshot routes until the wrapper
    # snapshot cache lands (PR 3, task 3.9). Key starts with the provider
    # name, so two sources never share an entry.
    snap_cache: dict[tuple, tuple[float, object]] = {}

    def _snap(p, key: tuple, ttl: float, fetch, what: str):
        k = (p.name, *key)
        hit = snap_cache.get(k)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            rows = fetch()
        except NotSupported as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(502, f"{what} failed: {e}")
        if len(snap_cache) >= 512:
            del snap_cache[min(snap_cache, key=lambda x: snap_cache[x][0])]
        snap_cache[k] = (time.time(), rows)
        return rows
```

  The `_NO_SOURCE` texts and the 502 prefixes (`economic calendar failed`, `macro catalog failed`, `macro series failed`, `options request failed`) must stay word for word: `app.js` and the React pages show `detail`. Check the old route bodies before you delete them. Check that `reg.all()` keeps registration order.
- [ ] **2.4 Migrate the routes.** Each route gains `provider: str | None = None` and keeps every other parameter and its TTL:
  - `/api/economic-calendar`: `_source("calendar", provider)`, `_snap(..., 60, ...)`.
  - `/api/macro/catalog`: `_source("macro", provider)`, TTL 60. `/api/macro/series`: `_source("macro", provider)`, TTL 600.
  - `/api/options/underlyings`: `_source("options", provider)`, TTL 60. `/api/options/chain`: `"options"`, TTL 15, key upper-cases and strips the underlying as today.
  - `/api/options/flow`: `"flow"`, TTL 15.
  - `/api/screener`: `provider: str | None = None` (was `"lse"`), `_source("screener", provider)`; no cache (the provider revalidates with ETag). Drop the `RuntimeError` -> 409 branch; the resolver owns 409.
  - `/api/prices`: `provider: str | None = None`; `_source("prices", provider)` first, then the empty-symbols early return; no cache.
  - `/api/logos`: `provider: str | None = None`; any `HTTPException` from `_source("logos", provider)` and any exception from the call returns `{}`.
  - Delete `econ_cache`, `macro_cat_cache`, `macro_series_cache`, `opt_cache`, `_opt_cached`, `_lse_for_options`, `_lse_for_macro`.
- [ ] **2.5 Migrate the other callers.**
  - `_ml_econ_frame`: `p = _source("calendar")` inside the existing `try`; catch `HTTPException` too and return `None`.
  - `_lse_bank`: `_lse_only()._lse()`.
  - Quant fit-info: `lse_ok` is `True` when `_source("options", "lse")` does not raise.
  - Quant fit `lse-options`: `_source("options", "lse").option_chain(...)`.
  - Assistant tools: the `get_candles` and backtest defaults name a candle source, not a capability route; leave them. The assistant economic-calendar call (no `provider`) keeps using the default source.
  - 2.11: `/api/providers` reads `p.is_custom`, `p.is_broker` directly.
- [ ] **2.6 Run the full suite and ruff.** Expected: baseline plus the new tests; `tests/test_api.py` 404/`{}` tests stay green.
- [ ] **2.7 Tick 2.1 to 2.6, 2.8, 2.11 in `tasks.md`. Commit.**

### Task 3: UI source parameter (2.7)

**Files:** `lse_terminal/ui/static/app.js`, `tasks.md`.

Design: one helper; a page captures the parameter at its listing call and reuses it for its symbol calls, so a symbol goes back to the source that listed it.

- [ ] **3.1 Add the helper** near the other small helpers:

```js
// Design D10: name the active source only when it serves `cap`; otherwise
// the engine picks the default source. Returns "" or "&provider=<name>".
function capParam(cap) {
  const p = (state.providers || []).find((x) => x.name === state.provider);
  return p && (p.capabilities || []).includes(cap)
    ? `&provider=${encodeURIComponent(p.name)}` : "";
}
```

- [ ] **3.2 Apply it.**
  - Options page: `optLoadUnderlyings` sets `optState.src = capParam("options")` and requests `/api/options/underlyings?` + `optState.src.slice(1)`. The chain fetches (`optLoadExpiry` near line 10507 and `optRefresh` near line 10700) and the flow fetch with an underlying append `optState.src`. The flow fetch without an underlying uses `capParam("flow")`.
  - A 404 on the chain or flow fetch renders "not supported by this source".
  - Trade dock: `rwPaintOpts` stores `rw.undSrc = capParam("options")` with `rw.undList`, and clears `rw.undList` when `capParam("options")` differs from `rw.undSrc`. Both chain fetches append `rw.undSrc`.
  - Calendar: `rwPaintEcon` appends `capParam("calendar")`.
  - Probes: `econ` and `opts` probes stay without `provider` (they ask "does any source answer").
  - Call sites with `provider=lse` keep it.
- [ ] **3.3 Check:** `node --check lse_terminal/ui/static/app.js`. Grep shows no remaining `/api/options/chain?underlying=` fetch without the source parameter.
- [ ] **3.4 Tick 2.7 in `tasks.md`. Commit.**

### Task 4: Review and merge

- [ ] **4.1** Opus pre-merge review over `git diff develop...feat/pr2-routes`.
- [ ] **4.2** Fold the findings. Run the suite.
- [ ] **4.3** `openspec validate data-source-contract-cache`.
- [ ] **4.4** Fast-forward `develop`, push `origin/develop`, remove the worktree.
