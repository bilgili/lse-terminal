# Design review: data-source-contract-cache

Scope: `proposal.md`, `design.md` (D1 to D11, I1 to I3, L1), the three spec deltas, and the current code they change.
Method: a TLA+ (Temporal Logic of Actions) model of the candle cache (D5 to D7), checked with the TLC model checker, plus an architecture review of D1 to D4 and D8 to D11.
Verdict: **do not implement D7 as written.** The model finds seven ways the cache serves or keeps wrong bars. Each has a small rule fix. With the fixes, every invariant and L1 pass. The architecture (one contract, one resolver, one cache owner at the registry) is sound, with four ownership corrections.

Files beside the proposal:

- `design.tla`: the model. Each rule has an "as written" and an "amended" form, selected by a `Fix*` flag. Source faults have their own flags.
- `design.cfg`: the main run. All amendments on, no source faults.
- `design.cx_*.cfg`: one counter-example run per finding. Each run flips one flag.
- `design.ok_*.cfg`: extra runs that must pass.
- `design.live_*.cfg`: the L1 liveness runs.

Run any of them with `tlc design.tla -config design.<name>.cfg`.

## 1. Model summary and TLC results

### 1.1 What the model contains

- **Clock.** `now` is an integer tick from 0 to `MaxTime`. The clock can advance between any two steps of any reader.
- **Source.** Each key has a fixed set of bar open times (`SrcBars`). A gap in the set is a holiday. A bar exists from the moment it opens at the source. Its value is its progress, `min(srcNow - ts, Step)`. The value `Step` means final. `srcNow = now - Lag` models clock skew or late publication.
- **Unwrapped answers.** `candles(start, end, limit)` returns the oldest `limit` visible bars in `[start, end)` (LSE `head`). `candles(limit)` returns the newest `limit` visible bars (LSE `tail`).
- **Store.** `st[k][ts]` holds a stored value or `NONE`. `cov[k]` is the set of covered ticks. A set of ticks is the same as a union of merged half-open ranges, so the D6 merge rule holds by construction.
- **Readers.** Two readers. Each call goes `idle -> wait -> fill -> read -> idle`. `Lock` takes the per-key lock, samples `t0 = now` and computes the gaps under the lock. Each `Fill` step is one source call plus one atomic bars-then-coverage write (D7.6). `Read` makes the live-tail call, builds the response and compares it with the unwrapped answer at the same instant (I3).
- **Rules.** D7.1 closed bar, D7.2 live tail, D7.3 windowed gap fill with a cap, D7.4 limit-only fill and store hit, D7.5 holiday coverage, D7.6 lock and transaction, D7.8 clear.
- **Size.** 2 keys, 2 readers, step 1 or 2, 8 or 9 ticks, cap 3 (cap 1 for the off-grid run), limits {2, 4}, windows `[0, 8)` and `[3, 6)`.

Not modelled, with reason:

- The snapshot TTL (time to live) cache of D8. It is a key-to-body map with an expiry time and no ordering protocol. No TLA+ model is necessary.
- Calls with `end` and no `start`. The design has no rule for them (see finding 2.6).
- Clear from a second process. `threading.Lock` cannot cover it. Section 2.5 covers it in prose.

### 1.2 Properties

| Name | Meaning in the model |
|---|---|
| I1 | No stored bar is partial at the source (`st >= Step`). I1 as written ("`ts + step <= horizon` at write time") is true by construction, because every write filters on that test. The useful property is that the stored value is final. |
| I2 | For every covered tick of a key: a closed source bar is stored with the source's value, and a tick with no source bar has no stored bar. |
| CovBehindSource | Coverage never contains a bar that the source has not closed. |
| I3 | Every served frame equals the unwrapped provider's answer to the same call at the same instant. |
| L1 | With windowed calls only and fairness, every closed tick of every key becomes covered. |

### 1.3 TLC results

TLC 2.19, 18 workers, Apple Silicon.

TLC stops at the first violation, so a failing run's state count is the part of the graph it searched before the counter-example.

| Run | What changes from the main run | Result | Distinct states | Search depth | Time |
|---|---|---|---|---|---|
| `design.cfg` (main) | none: all amendments on, step 1, settle 1, clear on | PASS (I1, I2, CovBehindSource, I3) | 4,936,858 | 57 | 39 s |
| `ok_nolock_noclear` | read after unlock (D7.6 as written), no clear | RUNS_PENDING | | | |
| `ok_step2` | step 2, settle 1, off-grid bar, cap 1 | RUNS_PENDING | | | |
| `ok_step2_s2` | step 2, settle 2, 9 ticks | RUNS_PENDING | | | |
| `ok_lag_eq_settle` | lag 1 = settle 1 | RUNS_PENDING | | | |
| `live_asis` | L1, all rules as written, 1 reader, windowed only | RUNS_PENDING | | | |
| `live_fixed` | L1, all amendments, 1 reader, windowed only | RUNS_PENDING | | | |
| `cx_tail` | D7.2 as written | **I3 violated** | 2,432 | 9 | <1 s |
| `cx_edge` | D7.1 as written, step 2, settle 1 | **I2 violated** | 1,323 | 9 | <1 s |
| `cx_edge_i3` | same, check I3 only | **I3 violated** | 5,055 | 11 | <1 s |
| `cx_trunc` | D7.3 truncation as written | **I3 violated** | 16,150 | 12 | <1 s |
| `cx_count` | D7.4 store hit as written | **I3 violated** | 1,641,180 | 24 | 9 s |
| `cx_lock` | D7.6 and D7.8 as written, clear on | **I3 violated** | 8,883 | 10 | <1 s |
| `cx_irr` | D7.3 coverage formula as written, off-grid bar | **I2 violated** | 3,036 | 9 | <1 s |
| `cx_restate` | source restates one closed bar | **I2 violated** | 4,169 | 8 | <1 s |
| `cx_lag` | lag 2 > settle 1 | **I1 violated** | 1,021 | 8 | <1 s |
| `cx_transient` | a failed call answers empty | **I2 violated** | 1,074 | 9 | <1 s |
| `cx_skip` | a limit-only answer omits one interior bar | **I2 violated** | 4,295 | 9 | <1 s |

### 1.4 Counter-example traces

TLC traces, shortened to the steps that change state. Tick values are model ticks. Key `A` has bars at 0, 1, 2, 4, 5, 6, 7 (holiday at 3) for step 1. Key `B` has a bar at every tick.

**cx_tail (D7.2 drops the just-closed bar).** Step 1, settle 1.

```
now=1  r1 Start  windowed [0,8) limit 2 on A
       r1 Lock   t0=1, horizon=0, no gaps
       r1 Read   tail = newest 2 = {0,1}; none is "closed" (ts+1 <= 0 fails)
                 "append the open bar" = {1}
       served {1}   unwrapped {0,1}                      I3 violated
```

When settle equals step (every timeframe up to 1m, because `settle = min(step, 60)`), the two newest bars are both inside the settle window. Bar 0 is complete but not "closed". It is not stored and not appended. Every chart at 1s, 30s or 1m misses its second-newest bar.

**cx_edge (D7.1 coverage reaches `horizon`, not the closed edge).** Step 2, settle 1. Key `A` has bars at 0, 4, 6.

```
now=2  r1 Lock   windowed [0,8) on A, t0=2, horizon=1, gap [0,1)
       r1 Fill   answer {0}; 0+2 <= 1 fails, so bar 0 is not stored
                 coverage [0, min(1, 0+2, 1)) = {0}
       bar 0 closed at the source (0+2 <= 2), covered, not stored   I2 violated
cx_edge_i3: the same call serves {} where the unwrapped answer is {0}.
```

Coverage up to `horizon` includes every bar that opened in `(horizon - step, horizon)`. That bar is still forming, so D7.1 does not store it. The gap fill never asks for a covered tick again. On a 1d chart, today's bar opens at 00:00 UTC. Every fill after 00:01 covers it and does not store it. On step 1 in the model the defect is invisible, because every tick is on the grid.

**cx_trunc (D7.3 one call per gap, then `store.read(ws, we).head(limit)`).** Cap 3, limit 4, key `B`.

```
now=5  r1 Lock   windowed [0,8) limit 4 on B, t0=5, gap [0,4)
       r1 Fill   answer {0,1,2} (cap 3, truncated); coverage [0,3); bar 3 stays a gap
       r1 Read   store {0,1,2} + tail from the edge {4,5}
       served {0,1,2,4}   unwrapped {0,1,2,3}            I3 violated
```

The frame has a hole in the middle, not only a short tail. `_backtest_candles` (server.py:985-1011) asks `limit=100_000` with a window. For a backtest, truncation at 5000 is the normal case.

**cx_count (D7.4 hit test counts every stored bar of the key).**

```
now=6  r1 windowed [0,8) on A, t0=6: covers [0,5), stores {0,1,2,4}
now=8  r2 limit-only 2, t0=8: miss; answer {6,7}; stores {6}; coverage [6,7)
       coverage {0..4, 6}: tick 5 uncovered
       r1 limit-only 4, t0=8: tick 6 covered, 5 stored bars >= 4, so "hit"
       served newest 4 of {0,1,2,4,6} + tail {7} = {2,4,6,7}
       unwrapped {4,5,6,7}                               I3 violated
```

**cx_lock (clear runs without the key lock).**

```
now=2  r1 Lock   windowed [0,8) limit 2 on A, t0=2
       r1 Fill   stores {0}, coverage {0}
          Clear(A)  (does not wait for r1's lock)
       r1 EndFill, Read: store {} + tail {1,2}
       served {1,2}   unwrapped {0,1}                    I3 violated
```

**cx_irr (D7.3 `last_ts + step` over-reaches on an off-grid bar).** Step 2, settle 1, cap 1. Key `A` has a session-open bar at 1, then 2, 4, 6.

```
now=5  r1 Lock   windowed [0,8) on A, t0=5, edge=3, gap [0,3)
       r1 Fill   answer {1} (cap 1); coverage [0, min(3, 1+2, 3)) = {0,1,2}
       bar 2 exists, is closed, is covered, was never fetched   I2 violated
```

**cx_restate (the design's stated risk).** The fill stores bars 0 and 1 with coverage `[0,2)`. The source then corrects bar 0. I2 is violated: the store keeps the old value until `clear`. This confirms the accepted risk in design.md.

**cx_lag (skew larger than settle).** Lag 2, settle 1.

```
now=2  r1 Lock   windowed [0,8) on A, t0=2, horizon=1, gap [0,1)
       r1 Fill   source clock is 0: bar 0 has progress 0 (just opened)
                 0+1 <= 1, so the wrapper calls it closed and stores value 0
       stored bar is partial                              I1 violated
```

`ok_lag_eq_settle` passes, so `settle` absorbs a lag up to `settle`, as the design claims. It does not absorb more.

**cx_transient (a failure that answers empty).**

```
now=2  r1 Lock   windowed [0,8) on A, gap [0,1)
       r1 Fill   the source fails and answers {}; D7.5 records coverage {0}
       bar 0 exists and is closed, covered, not stored    I2 violated
```

**cx_skip (a limit-only answer with an interior hole).**

```
now=3  r1 limit-only 4 on A, t0=3: miss; the true newest bars are {0,1,2}
       the source answers {0,2} (one page lost)
       stores {0}; coverage [0, edge=2) = {0,1}; bar 1 covered, not stored   I2 violated
```

### 1.5 What the amendments are

The main run passes with these six rule changes. Each `cx_*` run shows what breaks without one of them.

| Flag | Amended rule |
|---|---|
| FixEdge | `edge = horizon - step + 1` (integer seconds). Coverage and the gap clip stop at `edge`. A covered `ts` is always `<= horizon - step`, so it is always closed. |
| FixTail | The tail is `inner.candles(start=edge)` (a windowed call from the closed edge), not `limit=2`. Every bar it returns goes into the response. No tail bar is stored, because a tail bar is never closed by definition. |
| FixTrunc | A truncated answer (exactly `limit` rows) covers `[gap_start, last_ts + 1)`. The wrapper requests the rest of the gap from `last_ts + 1` in the same call, until it has `limit` bars or the gap is covered. A short answer covers the whole gap. This needs the contract clause "a short answer is complete" (section 4, item 5). |
| FixCount | The store hit counts only the stored bars of the covered run that ends at `edge`. The response uses only that run plus the tail. |
| FixExtend | A limit-only call first gap-fills `[newest covered tick + 1, edge)` as a windowed fill, then runs the hit test. |
| FixLock | The response read runs under the key lock. `clear(key)` takes the key lock. |

## 2. Findings on the five suspected weak spots

### 2.1 Limit-only coverage `[first_ts, horizon)` (D7.4)

**Partly confirmed.**

- A newest-N answer proves that no other bar exists between its first bar and the source's present. A silent cap (the source returns the newest 1000 when asked 5000) keeps that proof, because the answer is still contiguous. The amended rule `[first_ts, edge)` is sound for it. The main run covers it.
- The rule is not sound in three cases. Each leaves a covered tick without its bar:
  - The answer has an interior hole, for example a lost page (`cx_skip`).
  - The source lags more than `settle` (`cx_lag`). The source has not yet published the bars between its clock and `horizon`.
  - Any step above 1 s, because the bound is `horizon` and not the closed edge (`cx_edge`).
- A later windowed call inside that range gets the hole from the store. The gap fill never asks for a covered tick again. Only a later limit-only miss repairs it, because it upserts the newest N. As written, almost every chart open is a miss (next point), so charts heal. Windowed backtests do not.
- **New finding: the D7.4 hit test fails on every call after the first second.** The test is "coverage reaches `horizon`". `horizon` moves with `now`. The live tail does not extend coverage. For a call at `t2 >= t1 + 1 s`, `horizon(t2) - 1` is not covered. The call is a miss and refetches `limit` bars. The spec scenarios "Repeat poll" ("exactly one source call, with `limit=2`") and "First and second call" cannot pass. `app.js:256` opens every chart with `limit=5000` and no `start`, so the main UI path gets no benefit from the cache. FixExtend fixes it. It fills only the bars between the newest covered tick and the edge, then tests the run.
- **New finding: the hit test counts the wrong bars.** "The store holds at least `limit` closed bars" counts the whole key. Disjoint windowed coverage then satisfies it, and `tail(limit)` splices across the hole (`cx_count`).

### 2.2 Live tail `limit=2` and clock skew (D7.2)

**Confirmed, and worse than the design states.**

- With `settle = step` (1s, 30s, 1m), the two newest bars are both inside the settle window. "Upsert the closed bar of the two" stores nothing. "Append the open bar" drops the second-newest bar (`cx_tail`, a 6-state trace).
- The clock moves during a long fill. A 5000-bar fill takes seconds, and on 1s bars `now` moves several bars in that time. The newest two bars at the end of the call no longer touch the store's closed edge, and the bars between them are lost. The model lets the clock tick between `Lock` and `Read`. The amended tail (a windowed call from the closed edge) passes that case in the main run.
- Skew up to `settle` is safe (`ok_lag_eq_settle` passes). Skew above `settle` stores a partial bar and covers bars the source has not published (`cx_lag`).
- The repair claim in "Risks: Clock skew" is false in general. The tail re-reads only the newest two bars at the source. It repairs a partial bar only when a poll comes while that bar is still one of the newest two, so in practice only when polls are less than one step apart. It never repairs a coverage hole, because the gap fill skips covered ticks.
- `settle` is a property of the source's publication delay. The provider knows it and the cache does not. Make it a provider attribute (`settle_seconds`, default 60) and keep `min(step, settle_seconds)` in the cache.

### 2.3 `min(gap_end, last_ts + step, horizon)` with bars off the step grid (D7.3)

**Confirmed: it over-reaches.**

- `cx_irr`: an off-grid bar at the session open and a truncated answer cover a bar the source never returned. The hole is permanent.
- Real cases: the 09:30 half-hour bar of a US equity on `1h`; local-session bars that shift by one hour in UTC at a daylight-saving change; a vendor that stamps intraday bars at bar close (D11 asks this question about moomoo `time_key`).
- It also under-reaches on a complete answer. Coverage stops at the last bar plus one step, so a trailing stretch with no bars is asked again on the next call. That costs one extra empty call and is harmless.
- `last_ts + 1` is the bound the LSE provider already uses for its own forward paging (`s = max_ts + 1`, lse.py `candles`). It is correct on any grid.
- Truncation also corrupts the response, not only coverage (`cx_trunc`, section 1.4).

### 2.4 Holiday versus transient failure (D7.5)

**Confirmed: the design does not tell them apart.**

- An empty answer from a failed call records coverage. The bars in that range are never fetched again (`cx_transient`, I2 violated in a 6-state trace). For a window in the past, no tail and no limit-only miss reaches it, so the hole is permanent until `clear`.
- The cache turns a transient source fault into a permanent one. Today the next call simply asks again.
- The LSE SDK raises `LSEError` on a non-2xx answer (server.py:5194-5196 relies on this), so LSE is safe today. The risk is in plugins. The moomoo SDK returns `(ret, data)` tuples. A plugin that maps `ret != RET_OK` to an empty frame poisons the cache.
- Required: a contract clause "`candles` raises on failure; an empty frame means the source holds no bars in `[start, end)`". Optional defence in the cache: mark coverage that comes from an empty answer, and treat it as a gap again after a TTL. An empty answer costs almost nothing to repeat.

### 2.5 Lock scope (D7.6, D7.8)

**Refuted for the read alone. Confirmed for clear.**

- A read outside the lock is safe while the store only grows and each fill is one bars-then-coverage transaction. `ok_nolock_noclear` runs the amended rules with the read after unlock and no clear: RUNS_PENDING_NOLOCK. A reader can see bars without coverage (bars upserted by the tail). That is harmless, because no rule infers coverage from bars (D7.5) and FixCount counts only bars inside the run. A reader can never see coverage without bars, because one transaction writes both.
- `clear` breaks the "only grows" condition. Without the key lock, a clear between a reader's fill and its read serves a frame with the cleared bars missing (`cx_lock`). A fill that fetched before a clear and writes after it also puts old data back. That defeats the purpose of `clear`, which is the only fix for a restated bar.
- `lset cache clear` runs in a second process. `threading.Lock` does not reach it. Either the CLI calls `DELETE /api/cache` on the running engine, or a per-key generation number lives in SQLite and each fill writes only when the generation has not changed.
- Three SQLite points the design must state:
  - A `sqlite3` connection is not thread-safe by default (`check_same_thread`). FastAPI runs sync routes in a thread pool. Use one connection per thread or one store lock.
  - Set `busy_timeout`. With WAL (write-ahead log) mode, a second writer gets `SQLITE_BUSY` otherwise.
  - Never hold a write transaction open across a source call. Otherwise one key's network call blocks every other key, and the scenario "Two threads, two keys" fails.

### 2.6 Other model findings

- **I1 as written is a tautology.** Change it to "no stored bar is partial at the source".
- **When `now` is sampled matters.** The model samples `t0` at lock time, before any source call. Sampling after a source call lets the wrapper call a bar closed that was partial when the source answered. D7 must say "sample `now` once, before the first source call".
- **Calls with `end` and no `start` have no rule.** `_backtest_candles` sends that shape when a backtest has a "to" date and no "from" date (server.py:1003-1011). LSE answers "the newest N before `end`". D7.4 as written either drops `end` (wrong frame) or records coverage up to `horizon` past `end`. Add a third rule (newest N in `(-inf, min(end, edge))`, coverage `[first_ts, min(end, edge))`), or bypass the cache for this shape.
- **"The window reaches the present" needs a definition.** Use `end is None or end > edge`. The gap computation must clip at `edge`. Otherwise `[horizon, end)` is a new gap on every call, which costs one extra source call per poll.
- **L1 holds** for the rules as written and for the amended rules: RUNS_PENDING_LIVE. The defects above are safety defects. No rule stops coverage from growing.

## 3. Architecture findings, D1 to D11

| Decision | Verdict | Rationale | Alternative or change |
|---|---|---|---|
| D1 lift surfaces into the base class | **Keep the mechanism. Change the key lists.** | The override test already exists and gives one capability truth. But the required-key lists do not match the LSE rows or the UI. Calendar rows carry `datetime`, `date`, `time` and `region_code` (app.js:16230-16244, server.py:4447-4455). There is no `ts`, no `region` and no `importance` (server.py:4427 says the feed has no impact rating). `macro_series` returns `date`, not `ts` (lse.py docstring). The chain UI reads `last_price`, `underlying_price`, `delta` and `iv`, not `last` or `oi`. The LSE provider would fail its own harness. | Derive each list from a recorded LSE fixture and from the keys `app.js` reads. Put each list once in `contracts/types.py` as a `TypedDict` (`total=False` for optional keys). The harness reads `__required_keys__` and the docstring names the type. Typed values can come later without churn, because the type and its location already exist. |
| D2 resolver with fallback by registration order | **Change.** | Fallback is sound only for surfaces with no symbol argument: calendar, macro catalog, underlyings, logos, screener, and flow without an underlying. For `prices`, `option_chain`, `macro_series` and flow with an underlying, fallback sends source A's symbol to source B, and cross-source mapping is a non-goal. The scenario "Flow falls back" asks LSE for the tape of a moomoo underlying. `BrokerProvider` has `prices` (broker_hub.py), so a price fallback can also reach a broker. Plugin order is entry-point discovery order, which changes between environments. `configured()` runs on every request for every provider, and the moomoo plan makes it a TCP probe (tasks 4.3). | Split the capabilities into "global" (fallback allowed) and "symbol-scoped" (explicit `provider`, 404 when it lacks the capability, no fallback). Sort plugins by name at load. Add the contract clause "`configured()` does no I/O". The quant fit source is named `lse-options`. Keep it on an explicit `provider="lse"` rather than fallback, so a fit does not change vendor when a key is removed. |
| D3 shared timeframe table | **Keep, with one fix.** | `demo.py:21-27` has `"tick": 1`, and `demo.timeframes = list(_TF_SECONDS)` (demo.py:59). The shared table has no `tick`, so demo loses its tick timeframe and PR 1 changes behaviour. | Demo uses `{**TIMEFRAME_SECONDS, "tick": 1}`. Also put one `to_epoch(bound)` parser next to the table. The wrapper must turn ISO with `+00:00`, date-only and epoch strings into seconds. Today only `LseProvider._gate_ts` does that. |
| D4 plugins are custom sources | **Keep.** | One attribute default, no new type. `/api/providers` already exempts custom sources (server.py:630). | none |
| D5 wrapper at the registry | **Keep the seam. Change the mechanics.** | The registry is the right owner, because every path registers through it: built-ins, plugins, `set_key` (server.py:6992), `clear_key` (server.py:7027) and brokers. Four problems follow. See the next four rows. | See the next four rows. |
| D5a wrapper type | **Change.** | If `CachedProvider` subclasses `Provider`, `__getattr__` never runs for `name`, `title`, `timeframes`, `configured`, `capabilities`, `quote`, `stream` or the nine new methods, because `Provider` defines them all. The wrapper then reports `name=""` and default timeframes. `capabilities()` runs the override test on the wrapper's type. It claims `options`, `flow`, `calendar`, `macro` and `logos` for every provider (the wrapper overrides those methods), and it loses `quote` and `stream`. The resolver then routes the calendar to a plugin without one. | Make `CachedProvider` a plain class with `__getattr__`. Call `Provider.register(CachedProvider)` if `isinstance` is needed. Today no code checks `isinstance(p, Provider)`. State this in the spec, because the "Wrapper is transparent" scenario depends on it. |
| D5b lock and store owner | **Change.** | `set_key` builds a fresh `LseProvider` and registers it again. If each wrapper owns its locks, an old request in flight and a new request hold two different locks for one key. | One registry-owned `CandleCache` object holds the store and the per-key locks. Each wrapper holds a reference to it. Pass it to `Registry(cache=...)` at construction, with the hosted flag. Today the registry knows neither (the hosted flag is computed at server.py:550). |
| D5c sign-out | **Change.** | `clear_key` (server.py:7014-7028) states that nothing LSE answers after sign-out. A windowed call that is fully covered and in the past makes no source call, so the wrapper serves LSE bars and snapshots after sign-out. | When `inner.configured()` is `False`, the wrapper passes the call straight to `inner`. The unwrapped error then surfaces, which is I3. |
| D5d entitlement | **Accept and document.** | The cache key is the provider name, not the API key or plan. After a key change to a lower plan, the cache still serves bars fetched under the higher plan. | Accept for single-user local mode. Say it in the design. |
| D6 store schema | **Keep, with additions.** | One file and five methods is the right size. | Mark empty-answer coverage (section 2.4). Prune expired `snapshots` rows: their keys include `start` and `end`, and the in-memory dicts they replace are bounded at 64 and 256 entries (server.py:1111, 1172, 1203). Encode snapshot bodies with FastAPI's `jsonable_encoder`, so rows with `datetime` or `NaN` round-trip. Add the threading points of section 2.5. |
| D7 fill rules | **Change.** | Sections 1 and 2. | The six amended rules of section 1.5, plus `now` sampling, the end-only rule and the edge clip of section 2.6. |
| D8 snapshot TTLs in the wrapper | **Keep the owner, allow a provider override.** | D5 says "six snapshot methods" but the D8 table and the spec list seven. Use seven everywhere. The wrapper is the right single owner of the TTL mechanism. The TTL value is a property of the data, which the provider knows. LSE already caches logos for the process lifetime (`_logo_map`, lse.py `logos`), so the 3600 s logo TTL only adds persistence across restarts. | The wrapper reads `getattr(inner, "snapshot_ttl", {}).get(method, DEFAULT_TTL[method])`. Keys must be canonical: the routes upper-case and strip the underlying today (server.py:1223, 1233). Do that in the wrapper or before it. Apply the D5c sign-out rule to snapshots too. |
| D9 routes | **Keep.** | One shape per route. The 409 for a missing key moves into the resolver's `configured()` check. That replaces the `RuntimeError` to 409 mapping in `/api/screener` (server.py:738-741). | none |
| D10 UI `capParam` | **Change, with D2.** | For a symbol-scoped capability, omitting `provider` sends the active source's symbol to another source. | For symbol-scoped capabilities, always send the active source and show "not supported by this source" on 404. Use `capParam` only for global capabilities. |
| D11 moomoo plugin | **Keep, with contract items.** | A separate repository keeps the vendor SDK out of the core. | The plugin must page `request_history_kline` to `limit`, so a short answer means "complete". It must map `ret != RET_OK` to an exception, never to an empty frame. It must convert moomoo's inclusive `end` to the half-open contract. It must cache its TCP probe so `configured()` does no I/O. `AuType.NONE` is correct for I2. |

### 3.1 What the spec deltas leave out

The design relies on these. No spec states them.

1. **Bound semantics.** `start` is inclusive and `end` is exclusive. Demo filters `ts <= end` (demo.py:126), which is inclusive. LSE is exclusive. The cache assumes half-open ranges.
2. **Short answer means complete.** A `candles` answer with fewer than `limit` rows holds every bar of the source in the asked range.
3. **Contiguous answer.** No bar is left out between the first and the last bar of an answer.
4. **Raise on failure.** An empty frame means "no bars", never "the call failed".
5. **`configured()` does no I/O.**
6. **The wrapper is not a `Provider` subclass** and delegates `capabilities()`.
7. **Sign-out pass-through** for candles and snapshots.
8. **Clear takes the key lock**, and the cross-process rule for the CLI.
9. **The calls with `end` and no `start`**, and the definition of "reaches the present".
10. **Snapshot pruning.**
11. **The regression cases.** Tasks 3.5 cites "the ten cases from the design review", but no review existed before this one. Add the seven counter-examples of section 1.4 as tests, next to the ten listed cases.

Two spec scenarios contradict D7.4 as written: "Repeat poll" and "First and second call" (section 2.1). Both pass with FixExtend.

## 4. Edits for the author, by severity

**Blocking: the cache serves or keeps wrong bars.**

1. D7.2 and the "Live tail" requirement. Replace `limit=2` with a windowed call from the closed edge. Append every returned bar to the response. Store none of them. Replace the "Repeat poll" scenario with "one tail call, plus one gap call when the newest covered tick is behind the edge".
2. D7.1 and the "Only closed bars are stored" requirement. Define `edge = horizon - step + 1` in integer seconds. Coverage and the gap clip never pass `edge`. Add a scenario: step 300, `now` 30 s past a grid line, and the bar that opened 4 min ago is neither covered nor stored.
3. D7.3 and the "Windowed gap fill" requirement. On a truncated answer, cover `[gap_start, last_ts + 1)` and request again from `last_ts + 1` in the same call, until the call has `limit` bars or the gap is covered. A short answer covers the whole gap. Delete `last_ts + step`. Serve only the covered run that starts at `ws`. Add the `cx_trunc` and `cx_irr` cases as scenarios.
4. D7.4 and the "Limit-only fill" requirement. First gap-fill `[newest covered tick + 1, edge)`, with at most `limit` bars. Then count only the stored bars of the covered run that ends at `edge`, and serve only that run plus the tail. Add the `cx_count` case as a scenario.
5. data-provider-contract spec. Add the four candle clauses of section 3.1, items 1 to 4. The cache's correctness depends on each of them. Extend `check_provider`: compare `candles(start, end, limit=k)` with a larger limit for a contiguous prefix, check `ts < end` for half-open bounds, and check that the answer is ascending.
6. D7.6, D7.8 and the "Invalidation" requirement. `clear(key)` takes the key lock. A global clear takes every lock or bumps a generation number. The CLI clears through the running engine or through the SQLite generation number. State whether the response read runs under the lock (cheap and recommended) or rely on the "store only grows" argument in writing.
7. D7, a new rule: calls with `end` and no `start` (section 2.6), or a bypass for them.
8. data-provider-contract spec, "Required row keys". Rebuild every key list from an LSE fixture and the UI's reads. Put the lists in `TypedDict`s in `contracts/types.py`.

**High: wrong ownership or routing.**

9. D5. `CachedProvider` does not subclass `Provider`. One registry-owned `CandleCache` holds the store and the locks. When `inner.configured()` is `False`, the wrapper passes every call through. Add the scenarios "capabilities of a wrapped search-and-candles plugin equal the plugin's" and "after sign-out, a covered window is not served".
10. D2 and D10. Split global and symbol-scoped capabilities. Remove the fallback for symbol-scoped ones. Sort plugins by name. Add the contract clause "`configured()` does no I/O". Keep the quant fit on an explicit `provider`. Rewrite the "Flow falls back" scenario.
11. Risks, "Clock skew". Delete the repair claim. Add `settle_seconds` as a provider attribute. Add the empty-answer coverage mark with a TTL (section 2.4), or record the decision to accept the risk.

**Medium: correctness at the edges, operations.**

12. D3. Demo keeps `tick`. Add `to_epoch()` to `contracts/types.py`.
13. D7. Sample `now` once, before the first source call. Rewrite I1 as "no stored bar is partial at the source". Define "reaches the present" as `end is None or end > edge`.
14. D6 and D8. Prune expired snapshots. Use `jsonable_encoder` for snapshot bodies. Make snapshot keys canonical. Add the provider TTL override.
15. D6. One SQLite connection per thread (or one store lock), `busy_timeout`, and no write transaction across a source call.
16. tasks.md 3.5. Add the seven counter-examples as regression tests. Keep `design.tla` and the configs, and run `tlc design.tla -config design.cfg` in the review gate when D7 changes.
