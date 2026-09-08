<!-- TWM delivery PR. Keep the title `TWM#<issue> - <concise title>` and lead
the description with a `## Tracking` section (Linear link first). -->

## Tracking

- Linear:
- Companion PR(s):

## Summary



## Definition of done

The things CI can't catch. Tick each, or say why it doesn't apply.

- [ ] **One home per concern** — this change does not derive a fact that is already derived somewhere else. Rendered facts go through the `TripView` composer; stored facts go to their lifecycle home (`trip_context` / the relevant `*_state` branch); agents never write UI-owned deterministic state.
- [ ] **New invariant → new fitness function** — anything this change must keep true is enforced by a test or a lint/import-linter rule, not just a code comment. (See `AGENTS.md` → *Architecture rules (enforced)*.)
- [ ] **Read-path change → a count assertion** — if this touches how a response is fetched or composed, a test pins the number of DB round-trips / agent calls / composer passes.
- [ ] **No god function, no oversized module** — nothing crossed a `ruff` cap without an inline `# noqa: <code>` + reason; the `# noqa: C901` list did not grow past 5; no `twm/` module crossed 600 lines without a documented `EXEMPT` entry.
- [ ] **Dispatch stays a registry** — any command / action dispatcher added or touched is a `{name: Handler}` map, not `if x == … elif`.
- [ ] **Deleted, not deprecated** — dead code, unused endpoints, unused columns, and superseded state shapes are removed, not left with a comment.
- [ ] **`AGENTS.md` still accurate** — the *Architecture rules (enforced)* section and any `ignore_imports` / `EXEMPT` list still describe reality after this change.
- [ ] **Observability** — the request / decision / external-call / failure boundaries this change touches are logged per the *Application logging* rules, or no logging change is needed and the PR says why.

## Verification

<!-- Backend checks, affected-doc verification, known limitations, rollback -->

## Rollback


