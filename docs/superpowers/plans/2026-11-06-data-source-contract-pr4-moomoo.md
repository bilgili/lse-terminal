# moomoo plugin (PR 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A separate package `lse-terminal-moomoo` adds moomoo (through the OpenD gateway) as a data source that the terminal discovers as a plugin and the cache wraps.

**Architecture:** One class `MoomooProvider(Provider)` in its own repository. Every SDK call goes through one method `_sdk(name, *args, **kw)`. That method owns the shared `OpenQuoteContext`, the lock, the rebuild after a dead socket, and the mapping of `ret != RET_OK` to an exception. Time conversion lives in one pure module `_time.py`. The terminal core does not change: `load_plugins` finds the entry point, marks the source custom, and the registry wraps it in the cache (it keeps `cacheable = True`).

**Tech stack:** Python 3.12 (moomoo-api and its protobuf dependency may not support 3.14 yet; the implementer checks), `moomoo-api`, pandas, `zoneinfo`, pytest. Parent spec: `lse-terminal/openspec/changes/data-source-contract-cache/` design D11, tasks 4.1 to 4.9, the `data-provider-contract` spec, `lse_terminal.testing.check_provider`.

---

## Facts verified before this plan (task 4.1)

These were read from moomoo's own kline service on 2026-11-06, through the moomoo MCP endpoint (`https://mcp.moomoo.com/mcp`). OpenD uses the same backend; Task 6's live smoke test confirms them against OpenD.

| Series | `time_key` values (exchange local time) | Meaning |
|---|---|---|
| US.AAPL 60m, 2026-11-04 | 10:30, 11:30, …, 15:30, 16:00 | **bar close**; the last bar is 30 min |
| HK.00700 30m, 2026-11-04 | 10:00, 10:30, …, 13:30 (after lunch), …, 16:00 | **bar close** |
| US.AAPL 1d | 2026-11-04 00:00 ET, 2026-11-05 00:00 ET | trading date, local midnight |

**Decision.** Intraday: `ts = epoch(time_key in the market zone) − step`. Daily and weekly: `ts = date at 00:00 UTC`. The intraday rule is deterministic per bar and strictly ascending, which the cache needs. A short bar (the 15:30–16:00 US bar on 60m, the HK pre-lunch bar) gets a stamp that is earlier than its true open. Discarded: `max(close − step, previous close)`. It gives the true open, but its answer depends on where a page starts, so two calls could disagree, and the cache rule I3 needs one answer per bar. The cache already handles off-grid bars (`cx_irr`).

The SDK (`request_history_kline`) returns `time_key` as a naive string `"YYYY-MM-DD HH:MM:SS"` in the exchange's zone. The MCP endpoint returned epoch milliseconds with a `time_zone` field; the plugin parses the SDK string.

---

## Repository and file map

New repository `~/projects/lse-terminal-moomoo` (sibling of `lse-terminal`).

| File | Responsibility |
|---|---|
| `pyproject.toml` | package `lse-terminal-moomoo`, entry point `moomoo = "lse_terminal_moomoo:MoomooProvider"` in `lse_terminal.providers`, deps `lse-terminal`, `moomoo-api`, `pandas` |
| `src/lse_terminal_moomoo/__init__.py` | exports `MoomooProvider` |
| `src/lse_terminal_moomoo/_time.py` | market → zone, `time_key` → epoch, epoch → SDK bound strings |
| `src/lse_terminal_moomoo/provider.py` | `MoomooProvider` |
| `tests/fake_opend.py` | `FakeQuoteContext`: canned SDK answers, call log, failure injection |
| `tests/test_time.py`, `tests/test_provider.py`, `tests/test_live.py` | unit tests; live smoke gated on `MOOMOO_OPEND` |
| `openspec/` | `openspec init`; one change `moomoo-provider` that points at parent D11 |
| `README.md` | OpenD setup, config, quota, surfaces |

---

### Task 0: Repository, environment, OpenSpec

- [ ] **0.1** `mkdir ~/projects/lse-terminal-moomoo`, `git init -b main`, `.gitignore` (`.venv/`, `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `.serena/`).
- [ ] **0.2** `uv venv -p 3.12 .venv`, then `uv pip install -e ../lse-terminal moomoo-api pytest ruff`. If `moomoo-api` fails to install on 3.12, try 3.11 and record the reason in the README. Record the installed `moomoo-api` version in the README.
- [ ] **0.3** Before any code, read the installed SDK's real signatures and return shapes, and write them into `openspec/changes/moomoo-provider/design.md` as the source of truth for the fake: `OpenQuoteContext.__init__`, `request_history_kline` (returns `(ret, df, page_req_key)`; `start`/`end` strings; `max_count`), `get_stock_basicinfo`, `get_user_security_group`, `get_user_security`, `get_market_snapshot` (column names), `get_option_expiration_date`, `get_option_chain` (date span limit per call), `subscribe`, `set_handler`, `TickerHandlerBase.on_recv_rsp`, `get_history_kl_quota`, `KLType`, `AuType`, `SubType`, `Market`, `SecurityType`, `RET_OK`. Use `inspect.signature` and the package docstrings; do not guess.
- [ ] **0.4** `openspec init` (non-interactive flags if the CLI asks), then change `moomoo-provider`: `proposal.md` (why, link to parent D11), `design.md` (this plan's decisions plus 0.3), `tasks.md` (Tasks 1 to 6), one spec `specs/moomoo-provider/spec.md` with the requirements below. STE for all of it. `openspec validate moomoo-provider`.
- [ ] **0.5** Design review gate (user rule): one Opus reviewer over the change before Task 1. It judges the lock and rebuild protocol and the probe thread; it may record "no TLA+ model — one lock, no ordering protocol" or model the rebuild if it finds a race. Fold findings. Commit on `main` (first commit), then work on branch `feat/moomoo-provider` in a worktree `../lse-terminal-moomoo-feat`.

Spec requirements (for 0.4): the plugin registers as `moomoo`, custom; `configured()` does no I/O; every SDK failure raises; `candles` honours the parent candle contract (half-open, ascending, no duplicates, oldest N with start, newest N without, complete, raise on failure); intraday `ts = close − step`, daily `ts = date 00:00 UTC`; `AuType.NONE`; `check_provider` passes against the fake; unsupported surfaces raise `NotSupported`.

### Task 1: Time module

**Files:** `src/lse_terminal_moomoo/_time.py`, `tests/test_time.py`.

- [ ] **1.1 Tests first:**

```python
from lse_terminal_moomoo._time import bar_open, market_zone, sdk_bound

def test_intraday_key_is_bar_close():
    # US.AAPL 60m, 2026-11-04 10:30 ET close (observed live) -> open 09:30 ET
    assert bar_open("US.AAPL", "1h", "2026-11-04 10:30:00") == 1793806200 - 3600

def test_hk_after_lunch_bar():
    # HK.00700 30m, 13:30 HKT close (observed live: 1793770200) -> open 13:00 HKT
    assert bar_open("HK.00700", "30m", "2026-11-04 13:30:00") == 1793770200 - 1800

def test_daily_is_date_midnight_utc():
    assert bar_open("US.AAPL", "1d", "2026-11-04 00:00:00") == 1793750400  # 2026-11-04T00:00Z

def test_zone_per_market():
    assert market_zone("US.X").key == "America/New_York"
    assert market_zone("HK.X").key == "Asia/Hong_Kong"
    assert market_zone("SH.X").key == market_zone("SZ.X").key == "Asia/Shanghai"
    assert market_zone("SG.X").key == "Asia/Singapore"
    assert market_zone("JP.X").key == "Asia/Tokyo"

def test_dst_boundary_us():
    # 2026-11-02 is after the US DST end (2026-11-01): EST, UTC-5
    assert bar_open("US.AAPL", "1h", "2026-11-02 10:30:00") == 1793633400 - 3600

def test_sdk_bound_round_trip():
    # the SDK takes inclusive local strings; a bound for bar opens [s, e) asks closes [s+step, e+step-1]
    assert sdk_bound("US.AAPL", "1h", 1793806200 - 3600, kind="start") == "2026-11-04 10:30:00"
```

  The implementer recomputes every expected epoch with `zoneinfo` in the test file (keep the observed live values as comments) before trusting the numbers above.
- [ ] **1.2 Implement** `market_zone(code)` (prefix before the first `.`: US, HK, SH, SZ, SG, JP, AU, CA; unknown prefix raises `ValueError`), `bar_open(code, tf, time_key)`, `sdk_bound(code, tf, epoch, kind)` (start → local string of `epoch + step` for intraday, `date` for 1d/1w; end → local string of `epoch + step − 1` for intraday, previous date for 1d/1w), using `TIMEFRAME_SECONDS` from `lse_terminal.contracts`.
- [ ] **1.3** Run, commit.

### Task 2: Provider core: config, probe, `_sdk`

**Files:** `provider.py`, `tests/fake_opend.py`, `tests/test_provider.py`.

- [ ] **2.1 Fake first.** `FakeQuoteContext(host, port)` implements the methods recorded in 0.3 with canned DataFrames shaped exactly like the SDK's, a `calls` log, `fail_next = (ret_code, msg)` for one SDK error, `dead = True` to raise the SDK's disconnect error on the next call, and `close()`. `MoomooProvider(ctx_factory=FakeQuoteContext, probe=lambda: True)` is the test seam.
- [ ] **2.2 Tests:** `configured()` returns the cached probe and never calls the context; the probe thread refreshes every 30 s (inject a short interval); an SDK `ret != RET_OK` raises `MoomooError` with the SDK message, never an empty frame; a dead socket closes the context, builds a new one and retries the call once; a second consecutive failure raises; concurrent calls from 8 threads never overlap inside the fake (the fake asserts no re-entry).
- [ ] **2.3 Implement:**

```python
class MoomooError(RuntimeError):
    """An OpenD call failed. The cache treats any exception as "no answer"."""


class MoomooProvider(Provider):
    name = "moomoo"
    title = "moomoo (OpenD)"
    timeframes = ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]
    settle_seconds = 60

    def __init__(self, ctx_factory=None, probe=None, probe_every=30.0):
        host, _, port = os.environ.get("MOOMOO_OPEND", "127.0.0.1:11111").partition(":")
        self._addr = (host, int(port or 11111))
        self._markets = [m.strip().upper() for m in
                         os.environ.get("MOOMOO_MARKETS", "US,HK").split(",") if m.strip()]
        self._ctx_factory = ctx_factory          # None: moomoo.OpenQuoteContext, imported lazily
        self._ctx = None
        self._lock = threading.Lock()
        self._probe = probe or self._tcp_probe
        self._up = bool(self._probe())          # one probe at construction, then the thread
        threading.Thread(target=self._probe_loop, args=(probe_every,), daemon=True,
                         name="moomoo-probe").start()

    def configured(self) -> bool:                # contract: no I/O
        return self._up

    def _sdk(self, method: str, *args, **kw):
        """The one door to OpenD: lock, lazy context, one rebuild on a dead
        socket, ret != RET_OK -> MoomooError. Returns the SDK's data part."""
```

  `_sdk` returns `data` for `(ret, data)` and `(data, page_req_key)` for `request_history_kline`. The implementer decides from 0.3 which SDK exception or message means "dead socket" and pins it in the fake. Log the history-kline quota once per process at the first `candles` call (`get_history_kl_quota`); quota text stays in the log and the README (no new contract surface).
- [ ] **2.4** Run, commit.

### Task 3: `search` and `candles`

- [ ] **3.1 Tests:**
  - `search("")` returns watchlist groups (category `"Watchlist: <group>"`) first, then market catalogs (category = market); `search("app")` ranks prefix matches on code or name before substring matches; the catalog is fetched once per hour (inject a clock).
  - `candles` with `start`: half-open `[start, end)`, ascending, no duplicates, oldest `limit`, pages on `page_req_key` until `limit` rows or the range ends; `AuType.NONE` and the right `KLType` on every call.
  - `candles` without `start`: newest `limit` bars; the plugin widens a look-back window (start at `limit × step × 2`, multiply by 4) until it holds `limit` bars or the window starts before 1990-01-01; a short answer is then complete.
  - An unknown timeframe raises `ValueError`; an SDK error mid-paging raises (no partial frame).
  - `lse_terminal.testing.check_provider(provider)` reports no violations against the fake for every timeframe.
- [ ] **3.2 Implement.** `candles` asks the SDK for closes `[sdk_bound(start), sdk_bound(end)]` (inclusive), converts every row with `bar_open`, then filters to `[start, end)`, sorts and drops duplicate `ts` (keep last). Columns exactly `CANDLE_COLUMNS`. `end=None` means now.
- [ ] **3.3** Run, commit.

### Task 4: `quote`, `prices`, `stream`

- [ ] **4.1 Tests:** `prices` chunks at 400 codes per `get_market_snapshot` call and returns `PriceRow`s (`symbol`, `price`, `bid`, `ask`, `ts`); `quote` returns the contract `Quote` for one code; `stream` subscribes `SubType.TICKER`, the handler pushes onto the asyncio queue through `loop.call_soon_threadsafe`, a full queue drops instead of blocking, ticks pass through `QuoteSynthesizer` like `lse.py`, and closing the generator unsubscribes.
- [ ] **4.2 Implement** following `lse_terminal/providers/lse.py` `stream` (lines ~666–720) for the bridge shape.
- [ ] **4.3** Run, commit.

### Task 5: Options

- [ ] **5.1 Tests:** `options_underlyings` returns `UnderlyingRow`s for the US and HK stocks in the user's watchlist groups (documented scope: OpenD has no "optionable" flag in the catalog; the implementer confirms from 0.3, and uses a real flag if one exists). `option_chain(und, type, expiry, limit)` gets the expirations, asks `get_option_chain` in windows that respect the SDK's per-call date span, enriches through `get_market_snapshot` in 400-code chunks, and returns `ChainRow`s (`contract_type` "call"/"put", `strike`, `expiry` `YYYY-MM-DD`, `last_price`, plus `underlying_price`, `iv`, `delta`, `open_interest`, `volume_today` where the snapshot has them), at most `limit` rows, nearest expiry first. `options_flow`, `screener`, `logos`, `economic_calendar`, `macro_*` raise `NotSupported` (base defaults; the test asserts the capability set is exactly `{search, candles, quote, stream, prices, options}`).
- [ ] **5.2 Implement.** `# ponytail:` note on the SDK rate limits (chain ~10 per 30 s, snapshot ~60 per 30 s): no throttle in v1; a chain with many expiries can hit the limit, which surfaces as a `MoomooError`.
- [ ] **5.3** Run, commit.

### Task 6: Live smoke test, README, close

- [ ] **6.1** `tests/test_live.py`, skipped unless `MOOMOO_OPEND` is set: `configured()` is true; `candles("US.AAPL", "1h", limit=20)` is ascending with `ts` on the expected open times (first bar of a session at 09:30 ET), which pins the 4.1 answer against OpenD; `prices(["US.AAPL"])` has a price; `check_provider` passes.
- [ ] **6.2** README (STE): OpenD install and login, `MOOMOO_OPEND`, `MOOMOO_MARKETS`, `pip install lse-terminal-moomoo`, the kline quota (distinct symbols per 30 days) and where the plugin logs it, the bar-time rule and the short-bar stamp, which surfaces moomoo serves, which fall back to LSE (calendar, macro, screener, flow without an underlying, logos: the default source) and which show "not supported by this source" (the flow tape for a moomoo underlying).
- [ ] **6.3** Integration check from the terminal side: in the `lse-terminal` venv, `pip install -e ../lse-terminal-moomoo`, start the engine, `GET /api/providers` lists `moomoo` with `custom: true` and the six capabilities; without OpenD it shows `configured: false` and the routes answer 409 `moomoo is not configured`.
- [ ] **6.4** Opus pre-merge review of the plugin branch; fold findings; merge into the plugin's `main`. Tick 4.1 to 4.9 in the parent `tasks.md` on `lse-terminal` `develop`. Ask the user where the plugin repository's remote lives (GitHub or the homelab GitLab) before any push.

---

## Parent repository edits (on `lse-terminal` develop)

- D11: add the verified `time_key` rule (bar close for intraday, date for daily), the `ts = close − step` decision with the discarded alternative, and `title = "moomoo (OpenD)"`. Note that the 4.1 check used moomoo's hosted kline service and the plugin's live test confirms it on OpenD.
- tasks.md 4.1: tick only after 6.1 passes against a live OpenD.
