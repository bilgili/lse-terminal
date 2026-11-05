# Data-source contract PR 2: routes and UI — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The engine picks the source of each capability route through one resolver, and no capability route names LSE.

**Architecture:** `server.py` gets one resolver `_source(cap, provider)` (design D2: static default source, `X-Source` header) and one TTL helper `_snap(...)` keyed by resolved provider, route tag and arguments. Each capability route resolves its source, calls the contract method, and maps errors. `app.js` gets one helper `capParam(cap)` (design D10). Contract fixes from the PR 1 review land in the contract modules.

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

Design (D2 as amended after the routing model): one owner. `_source` is the only place that turns `(cap, provider)` into a provider. `_snap` is the only route-level cache. Routes contain no source logic. The default source is the first registered provider that has `cap`, configured or not; it is LSE for every lifted capability. Every capability route sets `X-Source`. Model: `openspec/changes/data-source-contract-cache/design_routing.tla`, config `design_routing_fixed.cfg`.

- [ ] **2.1 Write `tests/test_routing.py` first.** A `FullStubProvider(Provider)` with `name = "stub"`, `configured()` controlled by a flag, and every lifted method overridden, returning one fixture-shaped row (from `tests/fixtures/lse_rows.json`) and counting calls. Register it through a monkeypatched `load_plugins` in a `client` fixture like `tests/test_api.py` (sandbox config dir, no `LSE_API_KEY`). Any synthetic epoch value starts at or after 1e9. Tests, per migrated route where it applies:
  - `provider=stub` returns the stub rows and `X-Source: stub`.
  - Without `provider`, LSE unconfigured, stub configured: the route answers 409 with today's LSE text and the stub counter stays 0 (default source is LSE).
  - Without `provider`, LSE configured (monkeypatch `LseProvider.configured` and the method): the route answers the LSE rows with `X-Source: lse`, even though the stub is configured.
  - `provider=demo` answers 404 and the stub counter stays 0.
  - `provider=stub` with the stub unconfigured answers 409 `stub is not configured`.
  - Unknown provider answers 404.
  - `/api/logos`: `{}` with 200 for unknown, lacking and failing sources; without `provider` and without a key it calls `LseProvider.logos` (no `configured()` check).
  - A `NotSupported` from the method answers 404; any other exception answers 502 and the detail contains the route prefix and `str(e)`.
  - Cache: `/api/options/chain?underlying=X` for `lse`, then for `stub`, reaches the stub. A second identical stub call inside the TTL does not reach the stub. LSE macro series cached, then LSE becomes unconfigured: the next call answers 409 (resolver before lookup).
  - Catalog without `provider`, key saved, series without `provider`: both `X-Source: lse`.
  - `/api/providers` lists `stub` with `custom: true` and the full capability list.
  - Grep gate: in `server.py` source, every `reg.get("lse")` is inside `def _lse_only`.
  - `/api/quant/fit-info` reports `lse_options` false while LSE is unconfigured and the stub is configured.
- [ ] **2.2 Run; they fail.**
- [ ] **2.3 Add the resolver and the helper** inside `create_app`, before the first capability route:

```python
    # Capability -> the 409 text the UI shows for an unconfigured LSE.
    # Word for word from the pre-resolver routes; app.js shows `detail`.
    _LSE_KEY_HINT = {
        "calendar": "LSE API key not set. Add your free key to load the economic calendar.",
        "macro": "LSE API key not set. Add your free key to load macro data.",
        "options": "LSE API key not set. Add your free key under MARKETS to load options data.",
        "flow": "LSE API key not set. Add your free key under MARKETS to load options data.",
    }

    def _source(cap: str, provider: str | None = None, *, need_key: bool = True):
        """The provider that serves `cap` (design D2). Explicit provider:
        that one or 404/409, never another source. None: the first
        registered provider that has `cap`, configured or not, so the
        default never moves when a key is saved or cleared (LSE today)."""
        if provider:
            try:
                p = reg.get(provider)
            except ValueError as e:
                raise HTTPException(404, str(e))
            if cap not in p.capabilities():
                raise HTTPException(404, f"{provider} does not support {cap}")
        else:
            p = next((q for q in reg.all() if cap in q.capabilities()), None)
            if p is None:
                raise HTTPException(404, f"no source supports {cap}")
        if need_key and not p.configured():
            raise HTTPException(409, (_LSE_KEY_HINT.get(cap) if p.name == "lse" else None)
                                or f"{p.name} is not configured")
        return p

    def _lse_only():
        """The databank download is an LSE product feature, not a data
        surface (design D2), so it alone names the provider."""
        try:
            p = reg.get("lse")
        except ValueError:
            raise HTTPException(404, "LSE provider not available")
        if not p.configured():
            raise HTTPException(409, _LSE_KEY_HINT["options"])
        return p

    # One TTL cache for the snapshot routes until the wrapper snapshot
    # cache lands (PR 3, task 3.9). Key = resolved provider, route tag,
    # canonical args; the caller resolves (and checks the key) first, so
    # a signed-out source never serves a cached row. Bounds per tag, as
    # the four caches this replaces had.
    _SNAP_BOUND = {"econ": 64, "macro_cat": 1, "macro_series": 256, "opt": 64}
    snap_cache: dict[str, dict[tuple, tuple[float, object]]] = {k: {} for k in _SNAP_BOUND}

    def _snap(p, tag: str, args: tuple, ttl: float, fetch, what: str):
        c = snap_cache[tag]
        k = (p.name, *args)
        hit = c.get(k)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            rows = fetch()
        except NotSupported as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(502, f"{what} failed: {e}")
        if len(c) >= _SNAP_BOUND[tag]:
            del c[min(c, key=lambda x: c[x][0])]
        c[k] = (time.time(), rows)
        return rows
```

  Check each `what` prefix against the old route body before you delete it: `economic calendar`, `macro catalog`, `macro series`, `options request`. The old options flow and chain both used `options request failed`.
- [ ] **2.4 Migrate the routes.** Each route gains `provider: str | None = None` and `response: Response` (FastAPI), sets `response.headers["X-Source"] = p.name`, and keeps every other parameter, its TTL and its argument canonicalisation:
  - `/api/economic-calendar`: `_source("calendar", provider)`, tag `econ`, TTL 60.
  - `/api/macro/catalog`: `"macro"`, tag `macro_cat`, TTL 60. `/api/macro/series`: `"macro"`, tag `macro_series`, TTL 600.
  - `/api/options/underlyings`: `"options"`, tag `opt`, TTL 60. `/api/options/chain`: `"options"`, tag `opt`, TTL 15, key upper-cases and strips the underlying as today. `/api/options/flow`: `"flow"`, tag `opt`, TTL 15. Keep the `"und"`, `"chain"`, `"flow"` first element of each args tuple.
  - `/api/screener`: `provider: str | None = None` (was `"lse"`), `_source("screener", provider)`; no cache. Delete the `RuntimeError` -> 409 branch.
  - `/api/prices`: `_source("prices", provider)` first, then the empty-symbols early return; no cache.
  - `/api/logos`: `_source("logos", provider, need_key=False)`; any `HTTPException` or call error returns `{}`.
  - Delete `econ_cache`, `macro_cat_cache`, `macro_series_cache`, `opt_cache`, `_opt_cached`, `_lse_for_options`, `_lse_for_macro`.
- [ ] **2.5 Migrate the other callers.**
  - `_ml_econ_frame`: `p = _source("calendar", "lse")` inside the existing `try`; catch `HTTPException` as well and return `None`.
  - `_lse_bank`: `_lse_only()._lse()`.
  - Quant fit-info: `lse_ok` is `True` when `_source("options", "lse")` does not raise.
  - Quant fit `lse-options`: `_source("options", "lse").option_chain(...)`.
  - Assistant `get_candles` and backtest defaults name a candle source: leave them.
  - 2.11: `/api/providers` reads `p.is_custom`, `p.is_broker` directly.
- [ ] **2.6 Run the full suite and ruff.** Expected: baseline plus the new tests. `tests/test_api.py` 404 and `{}` tests stay green.
- [ ] **2.7 Tick 2.1 to 2.6, 2.8, 2.11 in `tasks.md`. Commit.**

### Task 3: UI source parameter (2.7)

**Files:** `lse_terminal/ui/static/app.js`, `tasks.md`.

Design (D10): `capParam` names the source of a listing call only. A symbol call that comes from a listing sends the listing response's `X-Source` header as `provider`. So a symbol always goes back to the source that listed it, also across an active-source switch and a tape click. The engine sets `X-Source` on every capability route (Task 2).

- [ ] **3.1 Add the helpers** near the other small helpers:

```js
// Design D10: a listing call names the active source only when it serves
// `cap`; otherwise the engine uses its default source. "" or "&provider=x".
function capParam(cap) {
  const p = (state.providers || []).find((x) => x.name === state.provider);
  return p && (p.capabilities || []).includes(cap)
    ? `&provider=${encodeURIComponent(p.name)}` : "";
}
// The source that answered a listing, for the symbol calls that follow it.
function srcParam(src) {
  return src ? `&provider=${encodeURIComponent(src)}` : "";
}
```

- [ ] **3.2 Options page.**
  - `optLoadUnderlyings`: request `/api/options/underlyings?` + `capParam("options").slice(1)`; store `optState.src = r.headers.get("X-Source") || ""` with `optState.unds`.
  - Re-run `optLoadUnderlyings` when the active source changes (find where `state.provider` is set and the options page is open; or check `capParam("options")` against the value stored at load at the top of `optRefresh` and reload when it differs).
  - Chain fetches (near lines 10507 and 10700) and the flow fetch with an underlying append `srcParam(optState.src)`.
  - The all-underlyings tape (flow without an underlying) uses `capParam("flow")` and stores `optState.tapeSrc` from `X-Source`. A tape-row click that opens the chain sets the chain's source to `optState.tapeSrc` (find the click handler near line 10541).
  - Persisted underlying (near line 9554): save `{und, src}`; on restore, drop `und` when `src` differs from the rail's source.
  - A 404 on a chain or flow fetch renders "not supported by this source".
- [ ] **3.3 Trade dock.** `rwPaintOpts`: request the underlyings with `capParam("options")`, store `rw.undSrc` from `X-Source` and `rw.undKey = capParam("options")`; reload `rw.undList` when `capParam("options") !== rw.undKey`. Both chain fetches append `srcParam(rw.undSrc)`.
- [ ] **3.4 Calendar.** `rwPaintEcon` appends `capParam("calendar")`. The `econ` and `opts` probes stay without `provider`. Call sites with `provider=lse` keep it.
- [ ] **3.5 Check.** `node --check lse_terminal/ui/static/app.js`. Grep: no `/api/options/chain?underlying=` or `/api/options/flow?underlying=` fetch without a source parameter.
- [ ] **3.6 Tick 2.7 in `tasks.md`. Commit.**

### Task 4: Review and merge

- [ ] **4.1** Opus pre-merge review over `git diff develop...feat/pr2-routes`.
- [ ] **4.2** Fold the findings. Run the suite.
- [ ] **4.3** `openspec validate data-source-contract-cache`.
- [ ] **4.4** Fast-forward `develop`, push `origin/develop`, remove the worktree.
