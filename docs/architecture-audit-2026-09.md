# Architecture audit — 2026-09 (first run)

Run: `python scripts/architecture_audit.py` on `main` after TWM-223 / TWM-224.
This is the first instance of the quarterly audit (TWM-225); it also
formalises the manual pass done on 2026-09-04.

6 findings, none blocking. Triage below.

## Modules approaching the size cap (600)

| module | lines | note |
|---|---|---|
| `twm/persistence/postgres.py` | 581 | already `EXEMPT` (one cohesive asyncpg repository). Watch — a further +20 forces a split. |
| `twm/schemas/trusted_action.py` | 574 | already `EXEMPT` (one capability's full contract). |
| `twm/schemas/flight_search.py` | 538 | already `EXEMPT` (one capability's full contract). |

**Decision:** no action. All three are known and exempted; the audit just
confirms none has drifted further. Revisit if any crosses 600.

## Suppression growth

- `twm/schemas/trusted_action.py:212` — a stray `# type: ignore`.

**Decision:** low priority. One line; fold into the next `trusted_action`
change. Not worth its own story. `# noqa: C901` list is at 3/5 — healthy.

## import-linter ratcheted debt

- `twm.persistence.postgres → twm.services.trip_commands.state`
  (`canonical_state` / `touched_branches`, lazy-imported in `create_trip` /
  `replace_trip`).

**Decision:** the one real architectural debt — persistence reached up into
the service layer. **Fixed in this PR (TWM-225):** `TOUCHABLE_BRANCHES` +
the canonical-empty branch shapes + a `populated_touchable_branches(state)`
helper moved to `twm/shared/trip_state_branches.py`; both layers import
down from there. `create_trip` / `replace_trip` now call the shared helper;
the `ignore_imports` line is gone.

## `if x == "literal"` dispatch chains

- `twm/services/trusted_action/resolvers.py:build_query_params` — 4
  literal-equality branches.

**Decision:** looked. It builds partner-specific query params by branching
on `partner` — 4 partners, each a few lines. A `{partner: builder}` dict
would be marginally cleaner but this is a small, stable function, not a
growing `_apply`-style chain. No action; note for the next `resolvers.py`
touch.

## Routes with no test coverage

Clean.

---

## Outcome

No cleanup story opened automatically. One item
(`postgres → trip_commands.state`) is worth a `[BE]` follow-up story at the
team's discretion. Everything else is watch-list, not work.
