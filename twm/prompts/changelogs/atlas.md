# Atlas prompt changelog

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
