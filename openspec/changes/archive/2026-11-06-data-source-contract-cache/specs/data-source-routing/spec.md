## ADDED Requirements

### Requirement: One resolver picks the source for a capability
The engine SHALL expose one function `_source(cap, provider)` that returns the provider for a capability. With an explicit `provider`, it SHALL return that provider when it is registered, configured and has `cap`; it SHALL answer 404 when the provider is unknown or lacks `cap`, and 409 when the provider is not configured. It SHALL NOT replace an explicit provider with another source.

#### Scenario: Explicit provider lacks the capability
- **WHEN** a route calls `_source("options", "demo")`
- **THEN** the route answers HTTP 404

#### Scenario: Explicit provider is unknown
- **WHEN** a route calls `_source("options", "nope")`
- **THEN** the route answers HTTP 404

#### Scenario: Explicit provider is not configured
- **WHEN** a route calls `_source("options", "lse")` and no LSE key is set
- **THEN** the route answers HTTP 409

### Requirement: A route without provider uses the default source
Without `provider`, every capability route SHALL use the default source for `cap`: the first registered provider that has `cap`, configured or not. The route SHALL answer 409 when the default source is not configured and 404 when no provider has `cap`. `/api/logos` SHALL NOT require the default source to be configured. Plugins SHALL register after the built-ins, sorted by entry-point name.

#### Scenario: Default source is LSE
- **WHEN** `/api/economic-calendar` is called without `provider` and a configured plugin with `calendar` is installed
- **THEN** the route uses `lse`

#### Scenario: Default source not configured
- **WHEN** `/api/economic-calendar` is called without `provider` and no LSE key is set
- **THEN** the route answers HTTP 409 with today's "connect a key" text, even when a configured plugin has `calendar`

#### Scenario: Catalog and series agree across a key change
- **WHEN** `/api/macro/catalog` is called without `provider`, then a key is saved, then `/api/macro/series?symbol=X` is called without `provider`
- **THEN** both calls resolve to `lse`

#### Scenario: Logos without a key
- **WHEN** `/api/logos` is called without `provider` and no LSE key is set
- **THEN** the route calls `LseProvider.logos`

#### Scenario: Stable plugin order
- **WHEN** plugins `zeta` and `alpha` are installed
- **THEN** `alpha` registers before `zeta` regardless of discovery order

### Requirement: Routes name the source that answered
Every successful answer of a capability route SHALL carry the header `X-Source` set to the name of the provider it resolved. An error answer SHALL NOT be used to pin a source.

#### Scenario: Header on a default-source answer
- **WHEN** `/api/options/underlyings` is called without `provider` and LSE answers
- **THEN** the response carries `X-Source: lse`

### Requirement: An explicit provider never falls back
A symbol-scoped route is one whose answer is about a symbol: `/api/prices`, `/api/options/chain`, `/api/macro/series`, and `/api/options/flow` with `underlying`. With an explicit `provider`, a symbol-scoped route SHALL call only that provider.

#### Scenario: Chain from a plugin
- **WHEN** the UI calls `/api/options/chain?provider=stub&underlying=X` and `stub` claims `options`
- **THEN** the response carries the stub's rows

#### Scenario: Tape for a source without one
- **WHEN** the UI calls `/api/options/flow?provider=moomoo&underlying=X` and `moomoo` lacks `flow`
- **THEN** the route answers HTTP 404 and does not call another source

### Requirement: Route snapshot entries are per provider
Until the wrapper snapshot cache lands, the engine SHALL keep one TTL cache for the snapshot routes. Each entry key SHALL be the resolved provider name, a route tag and the canonical arguments. The resolver, with its `configured()` check, SHALL run before the cache lookup. Each route tag SHALL keep its own size bound. The TTL per route SHALL stay as it is today.

#### Scenario: Two sources, same arguments
- **WHEN** `/api/options/chain?underlying=X` is called with `provider=lse` and then with `provider=stub`
- **THEN** the second call reaches `stub` and does not return the LSE rows

#### Scenario: Sign-out inside the TTL
- **WHEN** `/api/macro/series?symbol=X` is cached for `lse` and the key is then cleared
- **THEN** the next call answers HTTP 409 and does not return the cached rows

### Requirement: Route error mapping
A `NotSupported` from the provider SHALL map to HTTP 404. Any other provider error SHALL map to HTTP 502. `/api/logos` SHALL return an empty map instead of 400, 404 or 409.

#### Scenario: Logos never error the UI
- **WHEN** `/api/logos` is called for a provider without `logos`
- **THEN** the response is `{}` with HTTP 200

### Requirement: No provider is named in a capability route
The engine SHALL NOT call `reg.get("lse")` in any capability route, assistant tool default or ML feature builder. The databank download SHALL name the LSE provider only through a helper named `_lse_only`. The quant fit source `lse-options` SHALL resolve with an explicit `provider="lse"`.

#### Scenario: Grep gate
- **WHEN** a test greps `server.py` for `reg.get("lse")`
- **THEN** the only match is inside `_lse_only`

### Requirement: Entry-point providers are custom sources
`load_plugins` SHALL set `is_custom = True` on each provider loaded from the `lse_terminal.providers` entry-point group when the provider leaves the attribute at `False`.

#### Scenario: Plugin visible behind the directory gate
- **WHEN** the fleet directory lists only `lse` and a plugin `moomoo` is installed
- **THEN** `/api/providers` lists `moomoo` with `custom: true`

### Requirement: UI source parameter rules
For a listing call, `app.js` SHALL append `provider=<active source>` when the active source's `capabilities` contains the capability, and SHALL omit the parameter otherwise. A symbol call that comes from a listing SHALL send the listing response's `X-Source` value as `provider`. `app.js` SHALL render HTTP 404 as "not supported by this source".

#### Scenario: Active source serves options
- **WHEN** the active source is `moomoo`, `moomoo` has `options`, and the user opens an underlying from the rail
- **THEN** the options board requests `/api/options/chain?...&provider=moomoo`

#### Scenario: Active source lacks the calendar
- **WHEN** the active source is `moomoo` and `moomoo` lacks `calendar`
- **THEN** the calendar panel requests `/api/economic-calendar` with no `provider`

#### Scenario: Tape click keeps the tape's source
- **WHEN** the all-underlyings tape was answered with `X-Source: lse` and the user clicks a row
- **THEN** the chain request carries `provider=lse`, whatever the active source is

#### Scenario: Source lacks the tape
- **WHEN** the rail was answered with `X-Source: moomoo` and the user opens the flow tape for an underlying
- **THEN** the UI requests `/api/options/flow?provider=moomoo&underlying=...` and renders the 404 as "not supported by this source"

### Requirement: Symbol routes check the source configuration
The engine SHALL resolve every route that names a provider for symbol data through the resolver. These routes are `/api/instruments` (capability `search`), `/api/candles`, `/api/user-indicators/preview`, `/api/backtest`, `/api/backtest/montecarlo` and `/api/backtest/walkforward` (capability `candles`), and `/api/ws` (capability `stream`). Each HTTP route SHALL call the resolver before the `try` block that maps provider errors to 502.

#### Scenario: Unconfigured plugin on the candles route
- **WHEN** a client calls `/api/candles` with `provider=moomoo` and moomoo is not configured
- **THEN** the route answers 409 with the detail `moomoo is not configured`

#### Scenario: Unconfigured plugin on the instruments route
- **WHEN** a client calls `/api/instruments` with `provider=moomoo` and moomoo is not configured
- **THEN** the route answers 409 and does not call `search`

#### Scenario: Unknown provider on a symbol route
- **WHEN** a client calls `/api/candles` or `/api/instruments` with an unknown provider
- **THEN** the route answers 404

#### Scenario: LSE without a key on a symbol route
- **WHEN** a client calls `/api/candles` or `/api/instruments` with `provider=lse` and no LSE key is set
- **THEN** the route answers 409 with the detail `LSE provider needs an API key. Get a free one at https://londonstrategicedge.com/data#api and set it in the terminal.`

#### Scenario: Unconfigured plugin on the preview, backtest and quant routes
- **WHEN** a client calls `/api/user-indicators/preview`, `/api/backtest`, `/api/backtest/montecarlo` or `/api/backtest/walkforward` with an unconfigured provider
- **THEN** the route answers 409 with the setup hint of that provider
- **AND** the route does not call `candles`

#### Scenario: Unconfigured plugin on the stream socket
- **WHEN** a client opens `/api/ws` with an unconfigured provider
- **THEN** the handler sends `{"type": "error", "message": <setup hint>}` and closes the socket
- **AND** the handler does not call `stream`

#### Scenario: Source without a stream on the stream socket
- **WHEN** a client opens `/api/ws` with `provider=userdata`
- **THEN** the handler sends an error message that contains `does not stream` and closes the socket

### Requirement: A closed stream socket ends the stream
The `/api/ws` handler SHALL close the provider stream when the client disconnects, also when the stream sends no item. The handler SHALL cancel both of its tasks, wait until both tasks have ended, and only then call `aclose()` on the generator.

#### Scenario: Tab closes while no tick arrives
- **WHEN** the client disconnects and the provider stream has no item to send
- **THEN** the handler cancels the stream at once and calls `aclose()` on the generator
- **AND** the provider's `finally` block runs before the handler returns

#### Scenario: Symbol switch closes the old socket
- **WHEN** the client closes a socket that waits for a tick and opens a new socket for another symbol
- **THEN** the old handler closes its generator without the error `aclose(): asynchronous generator is already running`
- **AND** the new socket streams

#### Scenario: Server cancels the handler
- **WHEN** the server cancels the handler while it waits for its tasks
- **THEN** the handler cancels both tasks and calls `aclose()` after both tasks have ended
- **AND** no task runs after the handler has returned

#### Scenario: A cancel arrives again during the cleanup
- **WHEN** an anyio cancel scope delivers the cancel again while the handler waits for its tasks to end
- **THEN** the handler finishes the cleanup, including `aclose()`, before it returns
- **AND** the cleanup takes at most 5 seconds

#### Scenario: Error item stops the stream
- **WHEN** the provider stream yields an error item and the handler sends it
- **THEN** the handler calls `aclose()` on the generator that is stopped at its yield
- **AND** the provider's `finally` block runs

#### Scenario: Overlapping sockets on a symbol switch
- **WHEN** the client opens the new socket before it closes the old socket
- **THEN** closing the old socket releases only the old stream
- **AND** closing the new socket releases the new stream
