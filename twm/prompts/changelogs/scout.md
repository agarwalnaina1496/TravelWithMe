# Scout prompt changelog

## Scout 1.9.0 — 2026-08-14

- Renames the `duration_days` fixed shared key to `trip_duration`, matching
  the same rename applied to Meridian's and Guide's awaiting slugs in this
  release.
- Rewrites two behavioral instructions from negative ("do not X") to
  positive framing (topic-redirect reply length, when to ask/route on a
  null-intent turn). Kept the Ownership Boundary's negative framing as-is:
  Scout only knows abstract routing intents (`advise`/`matcher`/`planner`),
  not the concrete downstream agent identities, so it cannot correctly
  state "Meridian/Guide own X" without assuming knowledge it doesn't have.
  Safety/injection guardrails unchanged.

## Scout 1.8.0 — 2026-08-14

- Restored fixed shared keys for five facts (`origin_city`, `num_travelers`,
  `duration_days`, `travel_dates`, `budget`) instead of an invented
  semantic key, since Meridian and Guide now also read/write them under
  these exact names. Values stay free-form and verbatim as before; only
  the key name is fixed for these five. Everything else Scout extracts
  keeps a freely chosen semantic key.

## Scout 1.7.0 — 2026-07-18

- Treats brief conversational turns as valid conversation rather than adversarial or clearly off topic.
- Keeps conversational glue out of traveler context unless it carries a material travel input or decision.

## Scout 1.6.0 — 2026-07-18

- Added instruction hierarchy, prompt secrecy, untrusted-data handling, and a concise travel-only response for clearly off-topic turns.
- Kept mixed travel content useful while preventing injection, role, tool, prompt, and unrelated text from entering traveler context.

## Scout 1.5.0 — 2026-07-17

- Made complete advice end naturally after the useful guidance and limited follow-up questions to missing details that materially change Scout-owned advice.

## Scout 1.4.0 — 2026-07-16

- Preserved extracted traveler values verbatim under semantic keys while keeping qualifiers, relationships, budget boundaries, route distinctions, seasonal relevance, and trip shape intact.
- Kept Scout limited to extraction, initial routing, and complete general advice while Meridian owns destination and circuit recommendation work.
- Required answer-first advice to address every material ask with a practical verdict, useful guidance beyond query repetition, relevant pacing, trade-offs, and qualified time-sensitive guidance.

## Scout 1.2.0 — 2026-07-16

- Clarified that Scout owns entry routing and advice only until UI performs a specialist handoff.
- Limited null-message resume behavior to Scout-owned context; active specialist continuations bypass Scout.

## Scout 1.1.0 — 2026-07-13

- Restored Given & Extract so reusable traveler signals are stored directly under `trip_context`.
- Clarified that verbatim preservation applies to useful extracted signals, not wholesale copies of the user's query.
- Removed model-generated advisor-memory duplication; the application owns deterministic persistence of visible replies.

## Scout 1.0.0 — 2026-07-12

- Established the first file-based Scout prompt release.
- Captured the existing extraction, routing, response, CTA, and resume behavior without intentionally changing it.
