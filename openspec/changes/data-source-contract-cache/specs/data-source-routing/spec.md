## ADDED Requirements

### Requirement: One resolver picks the source for a capability
The engine SHALL expose one function `_source(cap, provider=None)` that returns the provider for a capability. With an explicit `provider`, it SHALL return that provider when it is registered, configured and has `cap`. Without `provider`, it SHALL return the first registered provider that has `cap` and is configured.

#### Scenario: Explicit provider lacks the capability
- **WHEN** a route calls `_source("options", "demo")`
- **THEN** the route answers HTTP 404

#### Scenario: Explicit provider is unknown
- **WHEN** a route calls `_source("options", "nope")`
- **THEN** the route answers HTTP 404

#### Scenario: Explicit provider is not configured
- **WHEN** a route calls `_source("options", "lse")` and no LSE key is set
- **THEN** the route answers HTTP 409

#### Scenario: Fallback finds a source
- **WHEN** a route calls `_source("calendar")` and a configured provider with `calendar` exists
- **THEN** the route uses the first such provider in registration order

#### Scenario: Fallback finds nothing
- **WHEN** a route calls `_source("calendar")` and no configured provider has `calendar`
- **THEN** the route answers HTTP 409

### Requirement: Capability routes accept an optional provider
`/api/prices`, `/api/screener`, `/api/logos`, `/api/options/underlyings`, `/api/options/chain`, `/api/options/flow`, `/api/economic-calendar`, `/api/macro/catalog` and `/api/macro/series` SHALL accept an optional `provider` query parameter and SHALL resolve their source through `_source`. A `NotSupported` from the provider SHALL map to HTTP 404. Any other provider error SHALL map to HTTP 502. `/api/logos` SHALL return an empty map instead of 404 or 409.

#### Scenario: Options from a plugin
- **WHEN** the UI calls `/api/options/chain?provider=stub&underlying=X` and `stub` claims `options`
- **THEN** the response carries the stub's rows

#### Scenario: Flow falls back when the active source has only a chain
- **WHEN** the UI calls `/api/options/flow?provider=moomoo` and `moomoo` has `options` but not `flow`
- **THEN** the response is HTTP 404, and a call without `provider` resolves through `_source("flow")`

#### Scenario: Calendar falls back
- **WHEN** the UI calls `/api/economic-calendar` without `provider` and the active source lacks `calendar`
- **THEN** the response carries the rows of the first configured provider with `calendar`

### Requirement: No provider is named in a capability route
The engine SHALL NOT call `reg.get("lse")` in any capability route, assistant tool default, ML feature builder or quant fit path. The databank download SHALL be the only path that names the LSE provider, through a helper named `_lse_only`.

#### Scenario: Grep gate
- **WHEN** a test greps `server.py` for `reg.get("lse")`
- **THEN** the only match is inside `_lse_only`

### Requirement: Entry-point providers are custom sources
`load_plugins` SHALL set `is_custom = True` on each provider loaded from the `lse_terminal.providers` entry-point group when the provider leaves the attribute at `False`.

#### Scenario: Plugin visible behind the directory gate
- **WHEN** the fleet directory lists only `lse` and a plugin `moomoo` is installed
- **THEN** `/api/providers` lists `moomoo` with `custom: true`

### Requirement: UI passes the active source when it has the capability
`app.js` SHALL append `provider=<active source>` to a capability route when the active source's `capabilities` contains that capability, and SHALL omit the parameter otherwise.

#### Scenario: Active source serves options
- **WHEN** the active source is `moomoo` and `moomoo` has `options`
- **THEN** the options board requests `/api/options/chain?...&provider=moomoo`

#### Scenario: Active source lacks the calendar
- **WHEN** the active source is `moomoo` and `moomoo` lacks `calendar`
- **THEN** the calendar panel requests `/api/economic-calendar` with no `provider`
