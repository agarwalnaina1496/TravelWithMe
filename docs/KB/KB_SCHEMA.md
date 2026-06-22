# Destination Knowledge Base — Schema and Strategy

## Overview

The Destination KB is the data layer that Meridian queries during candidate generation and scoring. It stores structured destination profiles and circuit templates optimised for TWM's elimination and scoring logic.

The KB is not a static document. It starts seeded and grows dynamically as new destinations are queried.

Both schemas are frozen at v1.0. No profiles or circuit templates should be generated until schemas are frozen. Schema changes require a version bump and migration of all existing profiles.

All enum values, ID conventions, ingest rules, and what must never be stored are defined in `kb/global_rules.yaml`. That file is the single source of truth for KB governance.

---

## Why a Dedicated KB

Meridian's Decision Engine needs structured, consistent inputs to run elimination and scoring reliably. Web search returns unstructured, variable results — parsing these into decision-engine-compatible data at query time introduces inconsistency and latency at the core of the product.

The KB solves this by providing:
- Structured profiles Meridian can filter and score against directly
- Consistent field definitions across all destinations and circuits
- Seasonality, crowd, cost, and fit data in a form the scoring logic can use
- A layer that improves over time as usage grows

Live data that changes frequently — current pricing, live travel times, transport schedules — is always fetched at runtime from APIs. The KB covers what a destination or circuit *is*. Live sources cover what it currently *costs and takes to get to*.

---

## Data Stability Classification

Every field in the KB belongs to one of three stability tiers.

**Permanent** — does not change unless the destination fundamentally changes. No scheduled review.

**Semi-stable** — changes slowly. Review every 6 months or when a significant event affects the destination (new infrastructure, viral tourism, natural event).

**Live** — never stored in KB. Always fetched at runtime. Includes: current prices, live travel times, transport schedules, weather, road conditions, origin-specific feasibility.

---

## Destination Profile Schema v1.0

Template file: `kb/destinations/_template_destination.yaml`

```yaml
schema_version: "1.0"
schema_type: destination
destination_id:                    # name_region e.g. coorg_karnataka

# ── Identification (Permanent) ──────────────────────────────
name:
aliases: []
region:
destination_type: []

# ── Access (Permanent) ──────────────────────────────────────
access:
  nearest_airport:
    name:
    iata:
    distance_km:
  nearest_railhead:
    name:
    distance_km:
  road_accessibility:              # low | medium | high
  self_drive_suitability:          # low | medium | high
  notes:

# ── Seasonality (Semi-stable) ────────────────────────────────
seasonality:
  best_months: []
  acceptable_months: []
  avoid_months: []
  peak_tourist_months: []
  off_peak_months: []
  monsoon_character:

# ── Trip Fit (Permanent) ─────────────────────────────────────
trip_goal_fit:
  relaxation:                      # low | medium | high
  adventure:
  celebration:
  workation:
  staycation:

travel_style_fit:
  scenic:                          # low | medium | high
  nature:
  relaxed:
  cultural:
  adventure:
  luxury:
  social:
  road_trip:

group_fit:
  solo:                            # low | medium | high
  couple:
  friends:
  family:

# ── Crowd Profile (Semi-stable) ──────────────────────────────
crowd_profile:
  typical_level:                   # low | moderate | high
  peak_level:
  off_peak_level:
  notes:

# ── Cost (Semi-stable) ───────────────────────────────────────
cost:
  tier:                            # budget | mid | premium
  typical_per_person_per_day:
    budget:                        # "800–1200" — range, always a string
    mid:
    premium:
  notes:

# ── Destination Profile (Permanent) ──────────────────────────
destination_profile:
  known_for: []
  primary_activities: []
  not_suitable_for: []
  trekking_required:               # true | false
  beach:                           # true | false
  mountains:                       # true | false
  wildlife:                        # low | medium | high
  nightlife:
  connectivity:

# ── Destination Complexity (Permanent) ───────────────────────
destination_complexity:
  planning_required:               # low | medium | high
  logistics_difficulty:
  first_timer_friendly:            # true | false

# ── Experience Attributes (Permanent) ────────────────────────
experience_attributes:
  walkability:                     # low | medium | high
  navigability:
  food_culture:
    quality:                       # low | medium | high
    accessibility:
    street_food:                   # true | false
    notes:
  local_authenticity:              # low | medium | high
  tourist_density:                 # low | moderate | high
  spontaneity_friendly:            # low | medium | high
  visual_character:                # free text
  pace:                            # relaxed | balanced | fast
  discovery_potential:             # low | medium | high

# ── Strengths (Permanent) ────────────────────────────────────
destination_strengths: []
uniqueness_notes:

# ── Metadata ─────────────────────────────────────────────────
meta:
  last_updated:                    # YYYY-MM-DD
  data_source:                     # curated | generated | verified
  confidence:                      # low | medium | high
```

---

## Destination Field Reference

| Field | Stability | Purpose in Meridian |
|---|---|---|
| `destination_id` | Permanent | Primary key — all joins use this |
| `destination_type` | Permanent | Step 1 semantic retrieval |
| `access.*` | Permanent | Step 6 geography validation |
| `access.distance_km` | Permanent | Determines practical accessibility of nearest airport/rail |
| `seasonality.best_months` | Semi-stable | Step 2 rain/crowd filter, Step 7 seasonality scoring |
| `seasonality.avoid_months` | Semi-stable | Step 2 hard elimination |
| `trip_goal_fit` | Permanent | Step 3 goal fit elimination, Step 7 scoring |
| `travel_style_fit` | Permanent | Step 7 travel style scoring |
| `group_fit` | Permanent | Step 7 group fit scoring |
| `crowd_profile` | Semi-stable | Step 2 crowd filter, Step 7 crowd context |
| `cost.tier` | Semi-stable | Step 1 budget-tier filtering |
| `cost.typical_per_person_per_day` | Semi-stable | Output budget expectations |
| `destination_profile.trekking_required` | Permanent | Step 2 no_trekking exclusion |
| `destination_profile.beach` | Permanent | Step 2 no_beach exclusion |
| `destination_profile.mountains` | Permanent | Step 2 no_mountains exclusion |
| `destination_profile.nightlife` | Permanent | Step 2 no_nightlife exclusion, Step 3 celebration goal |
| `destination_profile.connectivity` | Permanent | Step 3 workation goal |
| `destination_complexity` | Permanent | Step 7 scoring, output context |
| `experience_attributes.*` | Permanent | Step 7 nuanced preference modifier |
| `destination_strengths` | Permanent | Step 7 destination strengths scoring |
| `uniqueness_notes` | Permanent | Step 7 uniqueness scoring |

---

## Circuit Template Schema v1.0

Template file: `kb/circuits/_template_circuit.yaml`

```yaml
schema_version: "1.0"
schema_type: circuit
circuit_id:                        # descriptive slug e.g. rajasthan_classic

# ── Identification (Permanent) ──────────────────────────────
name:
aliases: []
circuit_type:                      # linear | loop

# ── Character (Permanent) ────────────────────────────────────
circuit_character:
primary_experience_types: []
not_suitable_for: []

# ── Stops (Permanent) ────────────────────────────────────────
stops:
  - order:
    destination_id:                # must match a valid destination_id
    name:                          # readability only — IDs are source of truth
    recommended_nights:
    min_nights:
    role:

# ── Internal Travel (Semi-stable) ────────────────────────────
internal_travel:
  - from_id:
    from_name:
    to_id:
    to_name:
    distance_km:
    typical_duration_hours:        # "5–6" — range, always a string
    recommended_modes: []
    notes:

# ── Duration (Permanent) ─────────────────────────────────────
duration:
  minimum_nights:
  optimal_nights:
  maximum_nights:

ideal_trip_length:
  nights:
  days:                            # always nights + 1

# ── Travel Metrics (Permanent) ───────────────────────────────
travel_metrics:
  total_internal_travel_hours:     # ingest pipeline verifies by summing legs
  travel_burden:                   # low (≤4h) | medium (5–12h) | high (>12h)
  pace:                            # relaxed | balanced | fast

# ── Circuit Complexity (Permanent) ───────────────────────────
circuit_complexity:
  planning_required:               # low | medium | high
  logistics_difficulty:
  first_timer_friendly:            # true | false

# ── Seasonality (Semi-stable) ────────────────────────────────
seasonality:
  best_months: []
  acceptable_months: []
  avoid_months: []
  circuit_seasonal_notes:

# ── Trip Fit (Permanent) ─────────────────────────────────────
trip_goal_fit:
  relaxation:                      # low | medium | high
  adventure:
  celebration:
  cultural:
  workation:
  staycation:

travel_style_fit:
  cultural:                        # low | medium | high
  scenic:
  luxury:
  relaxed:
  adventure:
  nature:
  social:
  road_trip:

group_fit:
  solo:                            # low | medium | high
  couple:
  friends:
  family:

# ── Experience Variety (Permanent) ───────────────────────────
experience_variety:                # low | medium | high
experience_variety_notes:          # what makes each stop distinct —
                                   # e.g. "Jaipur (forts/bazaars), Jodhpur
                                   # (desert edge), Udaipur (lakes)"

# ── Profile (Permanent) ──────────────────────────────────────
known_for: []
known_pinch_points: []

# ── Variations (Permanent) ───────────────────────────────────
common_variations:
  - name:
    circuit_id:                    # must have its own YAML file
    stops: []                      # list of destination_ids
    duration_nights:               # "3–4" — range, string
    notes:

# ── Experience Attributes (Permanent) ────────────────────────
# Circuit-level assessment — dominant character across stops
# pace removed here — lives in travel_metrics.pace
experience_attributes:
  walkability:                     # low | medium | high
  food_culture:
    quality:
    accessibility:
    street_food:                   # true | false
    notes:
  local_authenticity:              # low | medium | high
  tourist_density:                 # low | moderate | high
  spontaneity_friendly:            # low | medium | high
  visual_character:                # free text
  discovery_potential:             # low | medium | high

# ── Metadata ─────────────────────────────────────────────────
meta:
  last_updated:                    # YYYY-MM-DD
  data_source:                     # curated | generated | verified
  confidence:                      # low | medium | high
```

---

## Circuit Field Reference

| Field | Stability | Purpose in Meridian |
|---|---|---|
| `circuit_id` | Permanent | Primary key |
| `circuit_type` | Permanent | Step 1 candidate generation |
| `primary_experience_types` | Permanent | Step 1 semantic retrieval |
| `stops` | Permanent | Step 6 sequence validation, output |
| `stops[].destination_id` | Permanent | Source of truth for all stop references |
| `internal_travel` | Semi-stable | Step 5a travel-to-experience ratio, Step 6 validation, output |
| `internal_travel.distance_km` | Semi-stable | Travel metrics validation, output |
| `duration` | Permanent | Step 1 duration scoping |
| `travel_metrics.total_internal_travel_hours` | Permanent | Step 5a ratio check |
| `travel_metrics.travel_burden` | Permanent | Step 5a, Step 7 scoring, output |
| `travel_metrics.pace` | Permanent | Step 7 travel style scoring |
| `circuit_complexity` | Permanent | Step 7 scoring, output context |
| `seasonality.best_months` | Semi-stable | Step 2, Step 7 seasonality scoring |
| `seasonality.avoid_months` | Semi-stable | Step 2 hard elimination |
| `trip_goal_fit` | Permanent | Step 3 goal fit elimination, Step 7 |
| `travel_style_fit` | Permanent | Step 7 travel style scoring |
| `group_fit` | Permanent | Step 7 group fit scoring |
| `experience_variety` | Permanent | Step 7 experience variety scoring |
| `known_pinch_points` | Permanent | Output cons, refinement hooks |
| `common_variations` | Permanent | Step 5a substitution when ratio exceeded |
| `experience_attributes.*` | Permanent | Step 7 nuanced preference modifier |

---

## Storage

### Collections

Profiles stored in two separate Supabase tables:

```
kb_destinations    →  destination profiles
kb_circuits        →  circuit templates
```

Folder in KB repo maps directly to table:
```
kb/destinations/   →  kb_destinations
kb/circuits/       →  kb_circuits
```

### Retrieval during Step 1

SQL filtering on structured fields:
- `cost.tier` for budget matching
- `best_months` / `avoid_months` for seasonality
- `beach`, `mountains`, `trekking_required`, `nightlife` for exclusions
- `duration.minimum_nights` / `maximum_nights` for circuit scoping

Semantic search (Phase 2, when pgvector is enabled):
- Full profile vector embedding for intent matching on trip goal and travel style

Nuanced preference modifier (Step 7):
- Experience attribute fields queried after retrieval for scoring modifier

### Ingest Pipeline

Script reads YAML from repo, validates against `global_rules.yaml`, upserts into Supabase. Run manually for MVP. GitHub Action trigger added in Phase 2.

Key ingest validations:
- `schema_version` must match current version
- All enum fields validated against `global_rules.yaml`
- For circuits: `total_internal_travel_hours` computed from legs, flagged if YAML value differs by more than 1 hour
- `destination_id` on circuit stops validated against `kb_destinations`
- Files failing validation are rejected — not stored

---

## Population Strategy

### Phase 0: Finalise schemas before generating any data

Both schemas (destination and circuit) must be frozen before generating a single profile. Generating against an unstable schema means regenerating everything when fields change.

---

### Phase 1: Seed circuit templates first

Seed circuits before individual destinations. Every stop in every circuit automatically becomes a Tier 1 destination. Circuits tell you which destinations matter most.

**Master circuit list (target 40–60):**

```
Rajasthan         →  Classic (Jaipur–Jodhpur–Udaipur) + variations
Golden Triangle   →  Delhi–Agra–Jaipur
Himachal          →  Shimla–Manali / Manali–Spiti / Kasol variations
Kerala            →  Kochi–Munnar–Alleppey–Kovalam
Karnataka         →  Coorg–Mysore / Hampi–Gokarna / full circuit
Northeast         →  Guwahati–Kaziranga–Shillong–Meghalaya
Uttarakhand       →  Rishikesh–Mussoorie / Chopta–Auli
Odisha Coast      →  Bhubaneswar–Puri–Konark–Chilika
Maharashtra       →  Mumbai–Pune–Aurangabad (Ajanta/Ellora)
Madhya Pradesh    →  Bhopal–Khajuraho–Bandhavgarh
Andhra/Telangana  →  Hyderabad–Araku–Visakhapatnam
Tamil Nadu        →  Chennai–Pondicherry–Thanjavur–Madurai
```

Human review mandatory for circuits — LLMs consistently underestimate internal travel times and mischaracterise shoulder months.

---

### Phase 2: Seed destination profiles

**Tier 1 — circuit stops (highest priority)**
Every stop from Phase 1 circuits. Full profiles, human reviewed.

**Tier 2 — high-traffic single destinations by origin city**
```
From Bengaluru   →  Coorg, Wayanad, Ooty, Hampi, Gokarna, Pondicherry, Chikmagalur
From Mumbai      →  Lonavala, Mahabaleshwar, Alibaug, Matheran, Kashid
From Delhi       →  Rishikesh, Mussoorie, Agra, Shimla, Nainital, Corbett
From Hyderabad   →  Warangal, Araku, Coorg, Hampi
From Chennai     →  Pondicherry, Ooty, Kodaikanal, Mahabalipuram, Varkala
From Pune        →  Lonavala, Mahabaleshwar, Kashid, Alibaug
```

**Tier 3 — offbeat destinations**
After Tier 1 and 2 are solid. Ziro, Tawang, Majuli, Chopta, Dholavira, Munsiyari.

---

### Phase 3: Generation pipeline for each profile

1. LLM generation — feed schema template, instruct to leave fields blank rather than guess
2. Web verification — travel times, seasonality, cost tier, crowd profile
3. Human review — focus on seasonality accuracy, travel times, experience attributes
4. Confidence scoring — `generated` → `verified` → `curated`
5. Ingest validation — enum check, ID validation, circuit metrics verification
6. Store in Supabase

---

### Phase 4: Dynamic growth (post-launch)

```
Query not found in KB
    ↓
Generate via LLM + web research
    ↓
Store with confidence: low
    ↓
Use in current query
    ↓
Flag for human review after 3 queries
    ↓
Promote confidence after review
```

---

### Pre-launch timeline

| Task | Effort |
|---|---|
| Freeze both schemas and global rules | 1–2 days |
| Generate 50 circuit templates | 1 day |
| Human review of circuits | 2–3 days |
| Extract Tier 1 destination list from circuits | Half a day |
| Generate Tier 1 + Tier 2 destination profiles | 2 days |
| Web verification pass | 2–3 days |
| Human review of high-traffic profiles | 3–5 days |
| Set up Supabase tables and ingest pipeline | 1 day |
| Run ingest and validate | Half a day |

**Total: approximately 2–3 weeks for a solid pre-launch KB.**

---

## What the KB Does Not Cover

The KB stores what a destination or circuit *is*. The following are always fetched live:

| Data | Source |
|---|---|
| Current transport schedules and availability | Transport APIs |
| Live travel times between any two points | Maps API |
| Origin-to-destination feasibility | Maps API at query time |
| Gateway and return connection options | Maps API at query time |
| Current prices for stays and activities | Web search at query time |
| Road and weather conditions | Live data sources |
