## ADDED Requirements

### Requirement: One resolver picks the source for a capability
The engine SHALL expose one function `_source(cap, provider, scoped)` that returns the provider for a capability. With an explicit `provider`, it SHALL return that provider when it is registered, configured and has `cap`; it SHALL answer 404 when the provider is unknown or lacks `cap`, and 409 when the provider is not configured.

#### Scenario: Explicit provider lacks the capability
- **WHEN** a route calls `_source("options", "demo", scoped=True)`
- **THEN** the route answers HTTP 404

#### Scenario: Explicit provider is unknown
- **WHEN** a route calls `_source("options", "nope", scoped=True)`
- **THEN** the route answers HTTP 404

#### Scenario: Explicit provider is not configured
- **WHEN** a route calls `_source("options", "lse", scoped=True)` and no LSE key is set
- **THEN** the route answers HTTP 409

### Requirement: Global routes fall back by registration order
A global route is one whose answer carries no symbol: `/api/screener`, `/api/logos`, `/api/options/underlyings`, `/api/economic-calendar`, `/api/macro/catalog`, and `/api/options/flow` without `underlying`. Without `provider`, a global route SHALL use the first registered provider that has `cap` and is configured, and SHALL answer 409 when none exists. Plugins SHALL register after the built-ins, sorted by entry-point name.

#### Scenario: Fallback finds a source
- **WHEN** `/api/economic-calendar` is called without `provider` and a configured provider with `calendar` exists
- **THEN** the route uses the first such provider in registration order

#### Scenario: Fallback finds nothing
- **WHEN** `/api/economic-calendar` is called without `provider` and no configured provider has `calendar`
- **THEN** the route answers HTTP 409

#### Scenario: Stable plugin order
- **WHEN** plugins `zeta` and `alpha` are installed
- **THEN** `alpha` registers before `zeta` regardless of discovery order

### Requirement: Symbol-scoped routes require a provider
A symbol-scoped route is one whose answer is about a symbol: `/api/prices`, `/api/options/chain`, `/api/macro/series`, and `/api/options/flow` with `underlying`. A symbol-scoped route SHALL require `provider` and SHALL answer 400 without it. It SHALL NOT fall back to another source.

#### Scenario: Chain without provider
- **WHEN** the UI calls `/api/options/chain?underlying=X` with no `provider`
- **THEN** the route answers HTTP 400

#### Scenario: Chain from a plugin
- **WHEN** the UI calls `/api/options/chain?provider=stub&underlying=X` and `stub` claims `options`
- **THEN** the response carries the stub's rows

#### Scenario: Tape for a source without one
- **WHEN** the UI calls `/api/options/flow?provider=moomoo&underlying=X` and `moomoo` lacks `flow`
- **THEN** the route answers HTTP 404 and does not call another source

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
For a global route, `app.js` SHALL append `provider=<active source>` when the active source's `capabilities` contains the capability, and SHALL omit the parameter otherwise. For a symbol-scoped route, `app.js` SHALL always send `provider=<active source>` and SHALL render HTTP 404 as "not supported by this source".

#### Scenario: Active source serves options
- **WHEN** the active source is `moomoo` and `moomoo` has `options`
- **THEN** the options board requests `/api/options/chain?...&provider=moomoo`

#### Scenario: Active source lacks the calendar
- **WHEN** the active source is `moomoo` and `moomoo` lacks `calendar`
- **THEN** the calendar panel requests `/api/economic-calendar` with no `provider`

#### Scenario: Active source lacks the tape
- **WHEN** the active source is `moomoo` and the user opens the flow tape for an underlying
- **THEN** the UI requests `/api/options/flow?provider=moomoo&underlying=...` and renders the 404 as "not supported by this source"
