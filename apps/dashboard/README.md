# SACM Mission Control

Dependency-free React/Vite enterprise UI for the SACM control plane.

## Views

- **Command Center** — authorized outcome, cost, coverage, policy/security,
  executor-capacity, SLO, backup and audit signals. Missing and legacy values
  are explicit; `SUCCESS` is labeled as an accepted proxy, not human acceptance.
- **Missions** — Jira/task source, Definition of Ready and clarifications,
  risk-based autonomy, plans, agents/models/frameworks, approvals, execution
  jobs, Change Journey events, verification, snapshots/replay and cost.
- **Applications** — accessible grouped application graph with impacted nodes
  and an edge list; no graph-rendering dependency.
- **Agents / Benchmarks** — persisted agent outcomes, sample sufficiency and
  explicit `NOT_RUN` benchmark states without invented scores.
- **Policies / Security** — suggested, approval-required and blocked decisions
  with recorded reasons, findings and supply-chain status.
- **Evidence & Passports** — Software Change Passport data, integrity
  verification and JSON export when an Evidence Pack exists.
- **Settings** — the only surface for API URL, actor and bearer-token setup.

The global <kbd>Command</kbd>+<kbd>K</kbd> palette supports navigation,
mission filtering and safe UI actions only; it never executes shell commands.
The layout includes semantic controls, keyboard focus, responsive breakpoints,
high-contrast states and reduced-motion support.

## Run

```bash
npm install
npm run dev
```

Set `VITE_SACM_API_URL` when the API is not proxied through `/api`.

## Validate

```bash
npm run build
npm run lint
```
