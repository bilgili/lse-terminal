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
Without `provider`, every capability route SHALL use the default source for `cap`: the first registered provider that has `cap` and is configured. The route SHALL answer 409 when no default source exists. Plugins SHALL register after the built-ins, sorted by entry-point name.

#### Scenario: Default source found
- **WHEN** `/api/economic-calendar` is called without `provider` and a configured provider with `calendar` exists
- **THEN** the route uses the first such provider in registration order

#### Scenario: No default source
- **WHEN** `/api/economic-calendar` is called without `provider` and no configured provider has `calendar`
- **THEN** the route answers HTTP 409

#### Scenario: Catalog and series agree
- **WHEN** `/api/macro/catalog` and then `/api/macro/series?symbol=X` are called without `provider`
- **THEN** both calls use the same provider

#### Scenario: Stable plugin order
- **WHEN** plugins `zeta` and `alpha` are installed
- **THEN** `alpha` registers before `zeta` regardless of discovery order

### Requirement: An explicit provider never falls back
A symbol-scoped route is one whose answer is about a symbol: `/api/prices`, `/api/options/chain`, `/api/macro/series`, and `/api/options/flow` with `underlying`. With an explicit `provider`, a symbol-scoped route SHALL call only that provider.

#### Scenario: Chain from a plugin
- **WHEN** the UI calls `/api/options/chain?provider=stub&underlying=X` and `stub` claims `options`
- **THEN** the response carries the stub's rows

#### Scenario: Tape for a source without one
- **WHEN** the UI calls `/api/options/flow?provider=moomoo&underlying=X` and `moomoo` lacks `flow`
- **THEN** the route answers HTTP 404 and does not call another source

### Requirement: Route snapshot entries are per provider
Until the wrapper snapshot cache lands, the engine SHALL keep one bounded TTL cache for the snapshot routes. Each entry key SHALL start with the provider name. The TTL per route SHALL stay as it is today.

#### Scenario: Two sources, same arguments
- **WHEN** `/api/options/chain?underlying=X` is called with `provider=lse` and then with `provider=stub`
- **THEN** the second call reaches `stub` and does not return the LSE rows

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
`app.js` SHALL append `provider=<active source>` when the active source's `capabilities` contains the capability, and SHALL omit the parameter otherwise. A page SHALL send the same parameter for its listing call and its symbol calls. The options page and the trade dock SHALL use the `options` capability for the underlyings, the chain and the tape with an underlying. `app.js` SHALL render HTTP 404 as "not supported by this source".

#### Scenario: Active source serves options
- **WHEN** the active source is `moomoo` and `moomoo` has `options`
- **THEN** the options board requests `/api/options/chain?...&provider=moomoo`

#### Scenario: Active source lacks the calendar
- **WHEN** the active source is `moomoo` and `moomoo` lacks `calendar`
- **THEN** the calendar panel requests `/api/economic-calendar` with no `provider`

#### Scenario: Active source lacks the tape
- **WHEN** the active source is `moomoo` and the user opens the flow tape for an underlying
- **THEN** the UI requests `/api/options/flow?provider=moomoo&underlying=...` and renders the 404 as "not supported by this source"
