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
- `design.plan.cfg`, `design.cx_l1_limit.cfg` and the `*_impl.cfg` runs: the PR 3 plan review (section 5).

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
| `ok_nolock_noclear` | read after unlock (D7.6 as written), no clear | PASS (I1, I2, CovBehindSource, I3) | 13,488,044 | 60 | 1 min 21 s |
| `ok_step2` | step 2, settle 1, off-grid bar, cap 1, 2 readers | PASS (I1, I2, CovBehindSource, I3) | 1,726,280 | 43 | 6 s |
| `ok_step2_s2` | step 2, settle 2, 9 ticks, 2 readers | PASS (I1, I2, CovBehindSource, I3) | 1,001,034 | 40 | 3 s |
| `ok_lag_eq_settle` | lag 1 = settle 1, 2 readers | PASS (I1, I2, CovBehindSource, I3) | 5,281,515 | 57 | 17 s |
| `live_asis` | L1, all rules as written, 1 key, 1 reader, windowed only | PASS (L1) | 94,476 | 56 | 9 s |
| `live_fixed` | L1, all amendments, 1 key, 1 reader, windowed only | PASS (L1) | 248 | 17 | <1 s |
| `live2_fixed` | L1, all amendments, 1 key, 2 readers, windowed only | PASS (L1) | 844 | 18 | <1 s |
| `live2_asis` | L1, all rules as written, 1 key, 2 readers, windowed only | not finished: stopped at 3.16M distinct states, queue still growing | – | – | >17 min |
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

- A read outside the lock is safe while the store only grows and each fill is one bars-then-coverage transaction. `ok_nolock_noclear` runs the amended rules with the read after unlock and no clear: PASS for I1, I2, CovBehindSource and I3 over 13,488,044 distinct states. A reader can see bars without coverage (bars upserted by the tail). That is harmless, because no rule infers coverage from bars (D7.5) and FixCount counts only bars inside the run. A reader can never see coverage without bars, because one transaction writes both.
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
- **L1 holds** for the rules as written and for the amended rules: with one key and one reader, `live_asis` passes over 94,476 distinct states and `live_fixed` over 248. With two readers, `live2_fixed` passes over 844. The liveness configs use `Keys = {"A"}`. L1 is per key, and the safety runs show that keys do not interact. With two keys, the as-written run reached 17.5M states in 13 h, and the reviewer stopped it. The defects above are safety defects. No rule stops coverage from growing.

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

## 5. PR 3 plan review

Scope: Task 3 of `docs/superpowers/plans/2026-11-05-data-source-contract-pr3.md` (the code of `CachedProvider` and `SqliteStore`), checked against `design.tla` and against probes on the plan's own code.
Method: the reviewer added six flags to `design.tla` that select the plan's rules, and ran TLC on them. This section records the findings (R1 to R9) and the TLC runs on the merged model.
Verdict: **implement the plan with amendments R1 to R9.** The plan's limit-only hit serves the wrong end of the tail (I3 violated). With R1, every invariant passes.

### 5.1 New flags in `design.tla`

Every flag is `FALSE` in the `cx_*`, `ok_*` and `live_*` configs of section 1, so those runs keep their meaning. `design.cfg` sets the implemented rules: every `Fix*` flag, `PlanWin`, `PlanMissAsIs`, `ExtOnePage`, `FixHit` and `TailFromRun` are `TRUE`, and `PlanLimTail` is `FALSE`.

| Flag | Rule |
|---|---|
| PlanWin | A windowed call stops the paging when the stored bars in `[ws, g)` reach `limit`. It serves the covered run from `ws`, clipped at `wc`. It adds the tail only when that run reaches `wc`, with the remaining limit, oldest first from `max(edge, ws)`. |
| PlanLimTail | A limit-only hit gets its tail from `candles(start=edge, limit=limit)`. That call returns the oldest `limit` bars after `edge`. This is the plan as first written. |
| PlanMissAsIs | A limit-only miss serves the source answer as it is. |
| ExtOnePage | The extension fill of a limit-only call asks at most one page. |
| FixHit | A short newest-N answer covers from the history start (tick 0 in the model). |
| TailFromRun | D7 rule 4 as rewritten (R1). There is no extension fill. One call `candles(start=c, limit=L)` from the end `c` of the newest covered run gives the tail. Then the call is a hit or a miss. |

### 5.2 Findings

- **R1 (high). Limit-only path.** The plan's hit test `len(run) >= limit` almost never holds after a miss. Its hit tail `candles(start=edge, limit=limit)` returns the oldest bars after `edge`, so the frame misses the newest bars (`design.plan.cfg`, I3 violated, section 5.4). Fix: D7 rule 4 as rewritten. The tail comes from the end of the newest run. A hit is run bars plus tail. A short newest-N answer covers from `HISTORY`. One source call per poll on a covered key.
- **R2 (high). Fail open.** A store that cannot open, or a `sqlite3.Error` in the cached path, stopped every call. Fix: `create_app` uses `store = None` and `MemorySnapshots`. The wrapper passes a call through to the source on `sqlite3.Error`, for candles and snapshots.
- **R3 (medium). Lock timeout.** A hung source call held the key lock without a bound. Fix: `lock.acquire(timeout=30)`; on timeout the call passes through to the source.
- **R4 (medium). The CLI cannot find the engine.** The desktop app starts the engine on the first free port from 7799, and the CLI default is 7787. Fix: the engine writes `config_dir()/engine.json` with `{"port", "pid"}`. `lset cache` reads it and falls back to `--port`.
- **R5 (medium to low). Logos leave the snapshot cache.** `LseProvider.logos()` returns `{}` on a failed fetch. The wrapper would keep that `{}` for one hour. PR 2 never cached logos. Six snapshot methods remain.
- **R6 (medium, accepted). Windowed empty-range churn.** A windowed poll off-hours records an empty gap `[prev_edge, edge)` with a unique `empty_until`, so rows do not merge. The prune on write bounds the growth. Accepted for v1. The chart path is limit-only and has no churn after R1.
- **R7 (low).** `__getattr__` raises `AttributeError` for `_inner`. The store gets `count(key, a, b)` for the paging stop. `write_page` builds its rows before `BEGIN IMMEDIATE`. The `coverage` table has no primary key and deletes rows by `rowid`, because a real range and an empty range can share a `start`.
- **R8 (low). Tests.** Test 7 holds only with R1. Test 21 uses an unbounded `cap` and `PAGE = 7`.
- **R9 (low). Model and spec.** This section. L1 is restated: it holds when `limit` is at least the number of bars in the window (`cx_l1_limit` shows the bound).

### 5.3 TLC results

TLC 2.19, `-workers auto` (18 cores), Apple Silicon. Other processes loaded the machine (load average 45 to 100) during these runs, so the times are wall-clock times under load. They are not comparable with section 1.3. TLC stops at the first violation, so a failing run's state count is the part of the graph it searched.

| Config | Flags | Result | Distinct states | Depth | Time |
|---|---|---|---|---|---|
| `design.cfg` | implemented (R1), step 1, limits {2, 4}, clear on | PASS (I1, I2, CovBehindSource, I3) | 11,214,231 | 48 | 58 min 29 s |
| `design.ok_step2_impl.cfg` | implemented, step 2, settle 1, off-grid bar, cap 1 | PASS (I1, I2, CovBehindSource, I3) | 2,895,176 | 43 | 15 min 25 s |
| `design.ok_step2_s2_impl.cfg` | implemented, step 2, settle 2, 9 ticks | PASS (I1, I2, CovBehindSource, I3) | 859,157 | 34 | 11 s |
| `design.live_impl.cfg` | implemented, L1, 1 reader, limit 8 | PASS (L1) | 248 | 17 | <1 s |
| `design.live2_impl.cfg` | implemented, L1, 2 readers, limit 8 | PASS (L1) | 844 | 18 | 4 min 24 s |
| `design.plan.cfg` | plan as written: PlanLimTail on, FixHit and TailFromRun off | **I3 violated** | 74,311 | 14 | 1 s |
| `design.cx_l1_limit.cfg` | implemented, L1, 1 reader, limit 4 | **L1 violated** | 256 | – | <1 s |
| `design.ok_step2.cfg` | section 1 rules, new flags off | PASS | 1,726,280 | 43 | 11 s |
| `design.ok_step2_s2.cfg` | section 1 rules, new flags off | PASS | 1,001,034 | 40 | 7 s |
| `design.ok_lag_eq_settle.cfg` | section 1 rules, new flags off | PASS | 5,281,515 | 57 | 16 min 9 s |
| `design.ok_nolock_noclear.cfg` | section 1 rules, new flags off | PASS | 13,488,044 | 60 | 17 min 10 s (second run; the first run printed no progress for 22 min at load 100, and this review stopped it) |
| `design.live_fixed.cfg` | section 1 rules, new flags off | PASS (L1) | 248 | 17 | 3 s |
| `design.live2_fixed.cfg` | section 1 rules, new flags off | PASS (L1) | 844 | 18 | <1 s |
| `design.live_asis.cfg` | section 1 rules, new flags off | PASS (L1) | 94,476 | 56 | 20 min 44 s |
| `design.cx_tail.cfg` | new flags off | **I3 violated** | 3,788 | 10 | <1 s |
| `design.cx_edge.cfg` | new flags off | **I2 violated** | 1,183 | 8 | <1 s |
| `design.cx_edge_i3.cfg` | new flags off | **I3 violated** | 4,813 | 10 | <1 s |
| `design.cx_trunc.cfg` | new flags off | **I3 violated** | 13,781 | 12 | <1 s |
| `design.cx_count.cfg` | new flags off | **I3 violated** | 1,414,234 | 24 | 8 s |
| `design.cx_lock.cfg` | new flags off | **I3 violated** | 9,018 | 10 | <1 s |
| `design.cx_irr.cfg` | new flags off | **I2 violated** | 5,963 | 9 | <1 s |
| `design.cx_restate.cfg` | new flags off | **I2 violated** | 4,965 | 8 | <1 s |
| `design.cx_lag.cfg` | new flags off | **I1 violated** | 815 | 8 | <1 s |
| `design.cx_transient.cfg` | new flags off | **I2 violated** | 1,000 | 8 | <1 s |
| `design.cx_skip.cfg` | new flags off | **I2 violated** | 3,079 | 9 | <1 s |

Every `cx_*` run violates the same property as in section 1.3. The state counts differ a little from section 1.3, because the order of the parallel search changes which counter-example TLC finds first. `design.live2_asis.cfg` did not run again; section 1.3 records that it does not finish.

The reviewer's scratch runs also passed the implemented rules with limits {1, 2, 4}: 26,947,596 distinct states for step 1, and 2,079,199 for step 2 with settle 2.

### 5.4 Counter-example: the plan's limit-only hit tail

`design.plan.cfg`. Step 1, settle 1. Key `A` has bars at 0, 1, 2, 4, 5, 6, 7 (holiday at 3).

```
now=5  r2 limit-only 4 on A, t0=5, edge=4: no coverage, miss
       r2 Fill   newest 4 = {1,2,4,5}, served as is; stores {1,2}; coverage [1,4)
now=5  r1 limit-only 2 on A, Lock t0=5, edge=4: the run [1,4) reaches the edge
now=6  r1 hit test: the run holds 2 stored bars >= 2, so "hit"
       r1 Read   tail = candles(start=4, limit=2) = oldest 2 from 4 = {4,5}
       served newest 2 of {1,2} + {4,5} = {4,5}   unwrapped {5,6}   I3 violated
```

The tail call takes the oldest bars after the edge, so a hit drops every bar after the first `limit` tail bars. R1 takes the whole tail from the end of the run and serves the newest `limit` bars of run plus tail.

### 5.5 What the model does not cover

- **R2 fail open and R3 lock timeout.** Both send the call to the source without the store. That answer is the unwrapped answer by definition, so I3 holds with no model. The model has no store errors and no timeouts.
- **R4 CLI, R5 logos, R7 and R8.** These are process, code and test points with no ordering protocol.
- **R6 churn.** The model holds coverage as a set of ticks. It has no rows and no `empty_until`, so it cannot count rows.
- **`HISTORY`.** The model uses tick 0 as the history start. The guard `c <= edge` of R1 always holds in the model, because coverage never passes the edge.
- **Limits.** `design.cfg` checks limits {2, 4}. Limit 1 passed only in the reviewer's scratch run.

## 6. Follow-ups after PR 4 review

Scope: the D2 paragraph "Symbol routes `/api/instruments` and `/api/candles`, amended after PR 4", D12, the two new requirements at the end of `specs/data-source-routing/spec.md`, and tasks 6.1 and 6.2. The code is commit `0098b68` on `fix/symbol-routes-ws`. The moomoo plugin is `lse-terminal-moomoo` at `a24c6aa`.
Method: code reading, asyncio experiments, a Starlette test-client run, and a TLA+ model of D12 (`design_ws.tla`).
Verdict: **implement the D2 amendment with the changes F2, F6 and F7. Do not implement D12 as written.** In D12, `aclose()` raises on almost every tab close. One change in the cleanup order fixes it: cancel both tasks, await both, then call `aclose()`. With that order, every property of the model holds.

New files beside the proposal:

- `design_ws.tla`: the model of the `/api/ws` handler, with the modes `old`, `as_written` and `fixed`.
- `design_ws.cfg`: the amended order (F1). It must pass.
- `design_ws_as_written.cfg`, `design_ws_as_written_shutdown.cfg`: D12 as written. Each run shows one counter-example.
- `design_ws_old.cfg`: today's handler. It shows the defect that D12 removes.

### 6.1 Claims checked

**Claim 1. No built-in source serves `search` or `candles` without configuration.** The claim holds.

- `userdata`: `configured()` always returns True (`lse_terminal/providers/userdata.py:572-573`).
- `demo`: the class has no override. The contract default returns True (`lse_terminal/contracts/provider.py:163-168`).
- `lse`: `configured()` returns `bool(self._key)` (`lse_terminal/providers/lse.py:116-117`). `search` calls `_instruments`, then `_refresh_catalog`, then `_lse()` (`lse.py:144-169`). `candles` calls `_lse()` (`lse.py:278`). `_lse()` raises when the key is not set (`lse.py:122-124`). The tick path builds `LSE(api_key=self._key)` with no key (`lse.py:365`).
- Hosted mode: `LseProvider()` reads `LSE_API_KEY` from the environment (`lse_terminal/engine/config.py:35-36`). `set_key` refuses in hosted mode (`server.py:6945`). Hosted LSE is configured only when the operator sets that variable. The amendment does not change this.
- `broker:<name>`: `configured()` returns `broker in hub.conns` (`lse_terminal/engine/broker_hub.py:1229-1232`). `search` and `candles` go through `hub._conn`, which raises `LookupError` for a broker that is not connected (`broker_hub.py:545-549`). The engine removes the source when the broker disconnects (`server.py:7351-7356`).
- moomoo plugin: `configured()` returns the probe flag (`lse_terminal_moomoo/provider.py:158-159`). `_sdk` raises on the same flag (`provider.py:423-425`). The 409 replaces a call that fails in all cases.
- `capabilities()` always contains `search` and `candles` (`contracts/provider.py:190`). The resolver branch "the provider lacks `cap`" never runs on these two routes.

**Claim 2. No caller depends on the 502 of these two routes.** The claim holds.

- `loadChart` shows `detail` for every status that is not OK (`lse_terminal/ui/static/app.js:259-262`).
- `loadInstruments` retries on every status that is not OK. It then shows the fixed text "instrument load failed" (`app.js:2224-2239`).
- `hopeless` reads `state.lseConfigured`, not the status (`app.js:2220`). Its behaviour does not change.
- The two key-save probes call `/api/instruments?provider=lse` after a successful save (`app.js:927`, `app.js:11678`). LSE is configured at that time, so the resolver passes.
- The React sources and the chart bundle check only `res.ok` (`frontend/src/hooks/useMarketData.ts:42-43`, `frontend/src/lib/localEngine.ts:158-160`, `lse_terminal/ui/static/chart/chart.js` lines 40 and 646).
- No UI code matches the error text ("needs an API key", "candles failed", "search failed", "OpenD").
- No test expects 502 on these routes. The only 502 assertion is `tests/test_routing.py:201-205`, for the eight lifted routes.
- These tests call the two routes and pass with no change: `tests/test_api.py:30-56`, `tests/test_api.py:182`, `tests/test_api.py:198`, `tests/test_userdata_and_quant.py:36-77`, `tests/test_userdata_and_quant.py:297`, `tests/test_userdata_and_quant.py:358`, `tests/test_user_indicators.py:50`. They use `demo`, `userdata` or an unknown provider.

**Facts for D12.**

- The client sends no message on `/api/ws`. `connectStream` opens the socket, reads messages and closes the socket (`app.js:354-372`). The React sources have no `/api/ws` client. The `ws.send` calls in `app.js` go to the assistant sockets.
- `connectStream` closes the old socket on every symbol switch (`app.js:355`). A client disconnect is therefore a frequent event.
- A `receive()` in one task and a `send()` in another task on the same socket is valid. Starlette keeps a separate state for each direction (`client_state`, `application_state`). The ASGI (Asynchronous Server Gateway Interface) receive and send channels are independent. The amended handler ran correctly with this split in the test client.
- Starlette raises on a `receive()` after the disconnect message: `WebSocketDisconnected` in 1.10.0 (`starlette/websockets.py:63`), `RuntimeError` in 1.6.0. The watch loop must stop at the first `websocket.disconnect`.
- uvicorn 0.54.0 closes the transport when the handler returns (`uvicorn/protocols/websockets/websockets_impl.py:275`). The handler needs no explicit `close()` after the pump ends. This is today's behaviour.

### 6.2 Findings

**F1 (high). The D12 cleanup order raises "already running" and can leave the pump running.**
D12 says that the handler cancels the other task, and that its `finally` calls `agen.aclose()`. `task.cancel()` only requests a cancel. The `CancelledError` reaches the pump at its next resume. When the handler calls `aclose()` before that resume, the pump is still inside `agen.__anext__()`. CPython 3.12.13, 3.14.5 and 3.15.0a8 then raise `RuntimeError: aclose(): asynchronous generator is already running`. The scratch experiment shows this with a `finally` that does not await, and with a `finally` that awaits. The moomoo `finally` awaits `asyncio.to_thread(ctx.close)` (`lse_terminal_moomoo/provider.py:379-381`).
A silent stream waits in `__anext__()` almost all the time. The error therefore occurs on almost every symbol switch, and uvicorn logs it. The provider's `finally` still runs, but in the pump task after the handler has returned. The spec scenario "calls `aclose()` ... AND the provider's `finally` block runs" then does not hold.
There is a second defect. `asyncio.wait` does not cancel its tasks when the server cancels the waiting handler. The scratch experiment shows the pump still running after the handler has ended. The D12 `finally` contains only `aclose()`, so the pump keeps the stream and its OpenD subscription.
TLC: `design_ws_as_written.cfg` violates `NoAlreadyRunning` (section 6.3). `design_ws_as_written_shutdown.cfg` violates `NoOrphanTask`.
Design change. Write this order in D12:

```python
tasks = [asyncio.create_task(pump()), asyncio.create_task(watch())]
try:
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
finally:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await agen.aclose()
```

The `finally` cancels both tasks, also when the server cancels the handler. `gather` waits until the pump has left the generator. `aclose()` then finds an ended generator, or a generator at a yield. Use `asyncio.wait`, not `asyncio.TaskGroup`: `pyproject.toml:11` allows Python 3.10, and `TaskGroup` needs Python 3.11. `design_ws.cfg` passes with this order. Replace the last sentence of the D12 claim, "`aclose()` on an ended generator does nothing", with "after `gather`, `aclose()` finds an ended generator or a generator at a yield".

**F2 (medium). The resolver call must come before the route's `try`.**
Each route puts the provider call in a `try` with `except Exception` → 502 (`server.py:699-707`, `server.py:822-832`). FastAPI's `HTTPException` is an `Exception`. A `_source` call inside the `try` changes the 409 into 502 `candles failed: 409: moomoo is not configured`.
Design change. Add to the D2 amendment: "The route calls `_source` before its `try`. The `try` keeps `ValueError` → 404 and other errors → 502 for the provider call only." The 409 test of F7 detects a wrong position.

**F3 (medium). Three other routes keep the old lookup.**
The indicator preview (`server.py:903`), the backtest (`server.py:1075`) and the quant modes (`server.py:1095`) call `reg.get(body.provider)` and then `candles()`. The preview sends the active source (`app.js:7680-7686`), so an unconfigured moomoo still answers 502 there. The ML trainer (`server.py:4478`) accepts only `userdata` (`server.py:4472-4476`), which is always configured.
Judgement: the resolver is the correct single owner. It is the only layer that knows HTTP status, capabilities and the configuration rule together. The boundary is in the correct place, but three callers do not go through it.
Design change. These three routes call `_source("candles", body.provider)` before their `try`. Add an AST (abstract syntax tree) gate beside `test_grep_gate_reg_get_lse_only_in_lse_only` (`tests/test_routing.py:266-283`): in `server.py`, a `reg.get` call with an argument that is not a constant occurs only inside `_source`.
Discarded: the check in `Registry.get`. The registry has no HTTP status, and `set_key`, `clear_key` and the broker sync need plain lookups. Discarded: a typed error in each provider. Each plugin then repeats the rule.

**F4 (medium). `/api/ws` also skips the configuration check.**
The handler calls `reg.get(provider)` (`server.py:7075`). For an unconfigured moomoo, `stream()` opens a new OpenD context on each connect (`lse_terminal_moomoo/provider.py:354`). The connect waits up to `CONNECT_TIMEOUT = 3` seconds (`provider.py:59`). The plugin records that a context closed during the connect leaks a timer thread (`provider.py:54-57`). The UI connects again on every symbol switch.
Design change, in task 6.2: after `accept()`, the handler resolves with `_source("stream", provider)`. It catches `HTTPException`, sends `detail` as `{"type": "error"}`, and closes, as today for `NotSupported`. Add `"stream": "does not stream"` to `UNSUPPORTED` (`contracts/provider.py:29-37`). The default `stream()` then uses that entry. Without the entry, the resolver answers `does not support stream` (`server.py:737`). The UI filter matches only `does not stream` (`app.js:367`), so every MY DATA chart would show a stream error.
`X-Source` does not apply. The socket has no response headers after the handshake, and the page names the provider.

**F5 (medium). The test of task 6.2 must assert inside the test-client block.**
When the `with` block ends, the Starlette test client sends a disconnect and then cancels the handler's cancel scope (`starlette/testclient.py:112-113`, `testclient.py:119`). That cancel also reaches today's handler. With a `finally` that does not await, a check after the `with` block passes on today's handler. The scratch run shows this.
Design change, for the test. The stub stream waits for ever, and its `finally` awaits once and then sets a `threading.Event`. Inside the `with` block, the test calls `ws.send_text("not json")`, then `ws.close()`, then waits on the event with a timeout. Scratch run: today's handler, event not set; amended handler, event set.

**F6 (low). Keep the LSE key instruction on these routes.**
D2 says that `LseProvider` keeps today's per-route texts word for word. Today a keyless LSE chart shows `candles failed: LSE provider needs an API key. Get a free one at ...`. The amendment changes this to `lse is not configured`, which loses the instruction. `loadChart` shows the detail in the status line (`app.js:259-262`).
Design change. Add `"search"` and `"candles"` with the value `_NEEDS_KEY` to `_SETUP_HINT` (`providers/lse.py:28-34`). Add `"stream"` too with F4. Correct the sentence about the default hint in the D2 amendment.

**F7 (low). Task 6.1 names tests that do not exist.**
No test expects 502 for a keyless LSE on these routes (claim 2). Change 6.1 from "Update the tests" to "Add tests":

- A stub with `configured()` False answers 409 with `stub is not configured` on both routes. The stub records no `search` or `candles` call. The stub methods at `tests/test_routing.py:55-59` need `_hit` for this.
- An unknown provider on `/api/instruments` answers 404. Today only `/api/candles` has this test (`tests/test_api.py:52-53`).
- A keyless LSE answers 409 with the F6 text.

**F8 (low). Name the primitive of the watch task.**
The watch task calls `websocket.receive()` and compares `message["type"]`. It does not use `receive_text()` or `receive_json()`. Both raise on disconnect, and `receive_json()` parses the message. `gather(..., return_exceptions=True)` absorbs an unexpected error in the watch task. The pump sends its own errors in-band, as today. Write this in D12.

**F9 (low). Stale comments and `X-Source`.**
Two comments still say that the keyless catalog answers 502 (`app.js:2218-2219`, `app.js:11686-11687`). Update them in 6.1. D2 says that every successful capability answer carries `X-Source`. Pass `response=response` to `_source` on both routes, so the rule has no exception. The page names the provider, so this changes no behaviour.

### 6.3 TLA+ model of D12

D12 says "No TLA+ model: ... no shared state except the generator, which one task owns." This is not correct. The pump task calls `__anext__()` and the handler calls `aclose()` on the same generator. The pump task and the watch task share the socket. F1 is an ordering defect between these tasks. The user rule therefore requires a model.

`design_ws.tla` models one handler, one pump task, one watch task, one generator and one tab. The step size is one asyncio await point. A cancel request is a flag. The task receives `CancelledError` at its next resume, and a pending cancel wins over a ready result. The provider's `finally` awaits, so it is a separate step. These environment actions are not fair: a tick, an error item, the end of the stream, the tab close, and the server cancel of the handler. A tick can therefore never arrive. All task and handler actions are weakly fair.

Modes: `old` is today's handler. `as_written` is the D12 text. `fixed` is the F1 order. The constant `SHUTDOWN` lets the server cancel the handler while it waits.

Properties:

- `NoAlreadyRunning`: no `aclose()` or `__anext__()` call meets a generator that runs.
- `ReleasedOnReturn`: when the handler returns, the provider's `finally` has run.
- `NoOrphanTask`: no task runs after the handler has returned.
- `DisconnectReleases`: a closed tab leads to a closed generator and a returned handler, also when no tick arrives.
- `PumpEndReleases`: a pump that ends leads to the same end state.

TLC 2.19, `-workers 1`:

| Config | Mode | Result | Distinct states | Time |
|---|---|---|---|---|
| `design_ws.cfg` | fixed, shutdown on | PASS (all five) | 83 | <1 s |
| `design_ws_as_written.cfg` | as written, shutdown off | **NoAlreadyRunning violated** | 38 | <1 s |
| `design_ws_as_written_shutdown.cfg` | as written, shutdown on | **NoOrphanTask violated** | 17 | <1 s |
| `design_ws_old.cfg` | old | invariants hold, **DisconnectReleases violated** | 34 | <1 s |

TLC stops at the first violation. A failing run's state count is the part of the graph that TLC searched.

Counter-example `design_ws_as_written.cfg`, 6 states:

```
1  pump in __anext__, generator running, watch in receive
2  ClientClose     the tab closes
3  WatchRecv       receive() returns websocket.disconnect
4  Wake            asyncio.wait returns
5  Cancel          pump.cancel(): request only, the pump is still inside __anext__
6  Aclose          aclose() meets the running generator: RuntimeError
```

Counter-example `design_ws_as_written_shutdown.cfg`, 3 states: the server cancels the handler. The `finally` calls `aclose()` while the pump still waits in `__anext__()`, and `aclose()` raises. The handler returns, and the pump keeps running with no cancel request.

Counter-example `design_ws_old.cfg`: the tab closes while the pump waits in `__anext__()`, and no tick arrives. No fair action is enabled, so the generator stays open for ever. This is the defect that D12 removes.

What the model does not cover:

- A second server cancel during `gather`. The `finally` then stops before `aclose()`. A generator at a yield then stays open until the asyncio finalizer hook closes it at garbage collection.
- A provider generator that catches `CancelledError` and continues. `gather` then waits for ever. The `stream()` docstring can state that a stream must not catch `CancelledError`.
- A cancel in the middle of a frame send. This occurs only after the tab has gone.

Scratch evidence, outside the repository:

- The asyncio experiment ran on CPython 3.12.13, 3.14.5 and 3.15.0a8.
- The test-client run used Starlette 1.6.0 and FastAPI 0.141.1, because the moomoo environment has no `httpx2`.
- The code reading used Starlette 1.10.0, FastAPI 0.162.0 and uvicorn 0.54.0 from the moomoo environment.

### 6.4 Edits for the author, by severity

1. **D12 (F1, F8).** Write the cleanup order of F1. Name `websocket.receive()` as the watch primitive. Replace "No TLA+ model" with a reference to `design_ws.tla` and section 6.3.
2. **Spec (F1).** Add a scenario: when the server cancels the handler, the handler cancels both tasks and calls `aclose()` after both tasks have ended.
3. **D2 amendment (F2, F3, F6).** State the position of `_source`. Extend the change to the preview, backtest and quant routes, with the AST gate. Use `_NEEDS_KEY` for the LSE hint.
4. **Task 6.2 (F4, F5).** Resolve the stream source through `_source("stream", provider)` and add the `UNSUPPORTED` entry. Write the test as F5 describes.
5. **Task 6.1 (F7, F9).** Change "Update the tests" to "Add tests" with the F7 list. Update the two stale comments. Pass `response` to `_source`.
