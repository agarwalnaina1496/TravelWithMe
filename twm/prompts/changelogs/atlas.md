# Atlas prompt changelog

## Atlas 1.7.0 — 2026-08-25

- Adds `departure_date`/`departure_month` structured travel-date fields
  for `TRAVEL` timeline items, separate from free-text trip-level timing.
  Atlas may set `departure_date` (`YYYY-MM-DD`) only from a confirmed
  exact working-plan day date, and `departure_month` (`YYYY-MM`) only
  when a confirmed year and month are both known — never by guessing a
  year from a bare month name like "October". The two fields are
  mutually exclusive, and both must stay absent when precision isn't
  confidently known, in which case Atlas records a `dates` assumption
  instead. Every non-`TRAVEL` item must leave both absent. This unblocks
  Backend/UI sending exact or month-precision dates to flight search
  without fabricating date data (TWM-200).

## Atlas 1.6.0 — 2026-08-24

- Adds `from_city`/`to_city` canonical movement-endpoint guidance for
  `TRAVEL` timeline items, separate from narrative `location`/`detail`
  copy. Atlas must set both fields to canonical city/town names when
  confident, leave both absent when it isn't, and never place a road,
  landmark, or "via" description in them. Every non-`TRAVEL` item must
  leave the new endpoint fields absent. This unblocks Backend/UI building
  booking legs and Trusted Actions feasibility requests from structured
  endpoints instead of parsed display text (TWM-200).

## Atlas 1.5.0 — 2026-08-21

- Removes every line describing what Backend does — the prompt states
  Atlas's own job and contract only. Reworded: "supplied by the Backend"
  → "you receive", "The Backend calculates totals from the returned
  lines" → "Totals are calculated from the returned lines", "Use the
  Backend-supplied JSON Schema" → "Use the supplied JSON Schema". Removed
  entirely: "that status is set by the Backend once a traveler actually
  confirms it" and "the Backend attaches trusted prompt provenance" (Atlas
  doesn't need to know why it must not generate `agent_meta` or mark
  bookings confirmed — only that it must not).

## Atlas 1.4.0 — 2026-08-13

- Removes the `travel_options`/`stay_options` suggested-option lists entirely.
  Atlas never had live inventory to back these with, so they were always
  `GENERAL_GUIDANCE` placeholders presented as if they were real, bookable
  options — misleading to travelers. Transport/stay guidance is now woven
  narratively into the day timeline and practical notes only (general,
  non-bookable). Live, bookable transport/stay search is a separate future
  capability, not Atlas's job. The budget breakdown still includes an
  indicative transport/stay estimate line, unaffected by this change.

## Atlas 1.3.0 — 2026-08-12

- Adds handling for `confirmed_anchors`: fixed, application-owned logistics
  facts (transport/stay/activity) that Atlas must reflect exactly on their
  named day and never contradict, adjusting surrounding suggestions around
  them. Still cannot mark anything as booking-confirmed — that stays
  application-owned.

## Atlas 1.2.0 — 2026-08-12

- Replaces free-text `assumptions` with structured entries (`category` + `detail`)
  covering dates, arrival/departure window, stay area, budget, and traveler count.
- Adds `booking_readiness` (`suggested` | `needs_advance_booking` | `unresolved`) to
  every travel option and stay option, and to day timeline items that require
  advance booking. Deliberately excludes a confirmed/booked state, which remains
  application-owned and is never fabricated by Atlas.

## Atlas 1.1.0 — 2026-08-12

- Adds an explicit `assumptions` output requirement for planning assumptions made
  because the working plan lacked a confirmed value (most commonly a missing
  start date), so duration-only itineraries state their assumptions instead of
  silently inventing dates.

## Atlas 1.0.0 — 2026-08-03

- Establishes one-shot detailed itinerary compilation from a finalized working plan.
- Preserves traveler authority while adding practical day-wise enrichment.
- Establishes explicit verified/general treatment while live research remains a separate integration task.
- Keeps unsupported specifics honest and leaves trusted provenance to the Backend.
