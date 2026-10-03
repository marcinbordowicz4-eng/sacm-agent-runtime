# SACM Mission Control

Dependency-free React/Vite enterprise UI for the SACM control plane.

## Views

- **Command Center** — authorized outcome, cost, coverage, policy/security,
  executor-capacity, SLO, backup and audit signals. Missing and legacy values
  are explicit; `SUCCESS` is labeled as an accepted proxy, not human acceptance.
- **Missions** — create and optionally start a governed mission; review its
  Jira/task source, readiness, plan, agents, approval gates, execution jobs,
  captured repository diff, durable artifacts, draft-PR handoff, evidence,
  replay and cost. Approval decisions require a recorded rationale.
- **Applications** — accessible grouped application graph with impacted nodes
  and an edge list; no graph-rendering dependency.
- **Agents / Benchmarks** — persisted agent outcomes, sample sufficiency and
  explicit `NOT_RUN` benchmark states without invented scores.
- **Policies / Security** — suggested, approval-required and blocked decisions
  with recorded reasons, findings and supply-chain status.
- **Evidence & Passports** — Software Change Passport data, integrity
  verification and JSON export when an Evidence Pack exists.
- **Settings** — API URL, actor and bearer-token setup plus a resumable,
  API-backed first-mission checklist. It creates organizations and projects,
  can explicitly activate a reviewable onboarding policy, and issues a
  short-lived executor enrollment token without persisting that secret.

The global <kbd>Command</kbd>+<kbd>K</kbd> palette supports navigation,
mission filtering and safe UI actions only; it never executes shell commands.
Repository diff capture is scoped to the selected run's recorded repository;
the browser never submits an arbitrary repository path.
The layout includes semantic controls, keyboard focus, responsive breakpoints,
high-contrast states and reduced-motion support.

## Run

```bash
npm install
npm run dev
```

Set `VITE_SACM_API_URL` when the API is not proxied through `/api`. The chosen
API URL and incomplete onboarding draft are retained in browser local storage;
the bearer token and executor enrollment token are deliberately memory-only.

For a separately hosted production dashboard, configure the API with the exact
browser origins (comma separated), for example:

```bash
SACM_CORS_ORIGINS=https://console.example.com
```

Do not use a wildcard origin: Mission Control sends tenant-scoped authorization
headers. The API returns `X-Request-ID` so a connection error can be correlated
with server logs. A deployment must route the dashboard itself and its `/api`
path (or set `VITE_SACM_API_URL` to the API origin); serving only the API does
not serve Mission Control.

## Validate

```bash
npm run build
npm run lint
```
