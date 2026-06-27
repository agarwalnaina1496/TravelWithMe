# Destination Knowledge Base — Schema and Strategy

## Overview

The Destination KB is the data layer that Meridian queries during candidate filtering and scoring. It stores structured travel region profiles optimised for Meridian's filtering and scoring logic.

The KB is not a static document. It starts seeded and grows dynamically as new regions are queried.

The schema is frozen at v1.0. No profiles should be generated until the schema is frozen. Schema changes require a version bump and migration of all existing profiles.

All enum values, ID conventions, ingest rules, and what must never be stored are defined in `kb/global_rules.yaml`. That file is the single source of truth for KB governance.

---

## Why a Dedicated KB

Meridian needs structured, consistent inputs to run filtering and scoring reliably. Web search returns unstructured, variable results — parsing these at query time introduces inconsistency and latency.

The KB solves this by providing:
- Structured profiles Meridian can filter and score against directly
- Consistent field definitions across all travel regions
- Seasonality, environment, character, and constraint data in a form Meridian can use
- A layer that improves over time as usage grows

Live data that changes frequently — current pricing, live travel times, transport schedules — is always fetched at runtime. The KB covers what a region *is*. Live sources cover what it currently *costs and takes to get to*.

---

## Core Principle

**The KB stores traveler-agnostic facts only.**

Every field must pass this test before being added:

> *"Can this field be answered without knowing who the traveler is?"*

```
Yes → KB
No  → Matcher or Planner
```

The KB tells Meridian what a region is.
Meridian decides whether it fits this traveler.
Planner decides how to execute the trip.

---

## Data Stability Classification

Every field belongs to one of three stability tiers.

**Permanent** — does not change unless the destination fundamentally changes. No scheduled review.

**Semi-stable** — changes slowly. Review every 6 months or when a significant event affects the region (new infrastructure, viral tourism, natural event).

**Live** — never stored in KB. Always fetched at runtime. Includes: current prices, live travel times, transport schedules, weather, road conditions, origin-specific feasibility.

---

## Travel Region Schema v1.0

Template file: `kb/regions/_template_region.yaml`

```yaml
schema_version: "1.0"
schema_type: travel_region
region_id:                         # snake_case slug e.g. spiti_valley

# ── Identification (Permanent) ──────────────────────────────
name:
aliases: []

# ── Environment (Permanent) ─────────────────────────────────
environment:
  altitude:                        # low | medium | high | very_high
  climate:                         # tropical_humid | tropical_dry | cold_desert | temperate | alpine | coastal
  terrain:                         # flat | hilly | mountainous | coastal | plateau
  landscape:                       # free text e.g. stark_arid | lush_green | coastal_plains
  water_bodies:                    # none | limited | moderate | abundant
  forest_cover:                    # none | low | medium | high
  urbanization:                    # very_low | low | medium | high | very_high

# ── Destination Character (Permanent) ────────────────────────
destination_character:
  remoteness:                      # very_low | low | medium | high | very_high
  physical_exertion:               # low | medium | high
  scenic_value:                    # low | medium | high | very_high
  spiritual_culture:               # none | low | medium | high | very_high
  social_scene:                    # none | low | medium | high
  nightlife:                       # none | low | medium | high
  pace:                            # slow | balanced | fast
  novelty:                         # low | medium | high | very_high

# ── Seasonality (Semi-stable) ────────────────────────────────
seasonality:
  best_months: []
  acceptable_months: []
  avoid_months: []
  monsoon_character:               # free text — how monsoon affects this region specifically

# ── Cost (Semi-stable) ───────────────────────────────────────
cost:
  in_destination_tier:             # budget | mid | premium
  notes:                           # free text — what drives cost at this destination

# ── Constraints (Permanent) ──────────────────────────────────
# Factual limitations — matcher interprets implications, not KB
constraints:
  altitude_above_4000m:            # true | false
  medical_facilities_limited:      # true | false
  road_conditions_challenging:     # true | false
  luxury_inventory_available:      # true | false
  trekking_available:              # true | false
  trekking_required:               # true | false
  permit_required:                 # true | false
  beach:                           # true | false
  wildlife:                        # true | false
  connectivity:                    # low | medium | high

# ── Experience Attributes (Permanent) ────────────────────────
experience_attributes:
  walkability:                     # low | medium | high
  local_authenticity:              # low | medium | high | very_high
  food_accessibility:              # low | medium | high
  spontaneity_friendly:            # low | medium | high

# ── Crowd Profile (Semi-stable) ──────────────────────────────
crowd_profile:
  typical_level:                   # low | moderate | high
  peak_level:                      # low | moderate | high
  off_peak_level:                  # low | moderate | high
  peak_months: []                  # which months drive peak crowd

# ── Uniqueness (Permanent) ───────────────────────────────────
uniqueness_factor: []              # list of factual differentiators

# ── Known For (Permanent) ────────────────────────────────────
known_for: []                      # used in Meridian output reasoning

# ── Metadata ─────────────────────────────────────────────────
meta:
  last_updated:                    # YYYY-MM-DD
  data_source:                     # curated | generated | verified
  confidence:                      # low | medium | high
```

---

## Field Reference

| Field | Stability | How Meridian Uses It |
|---|---|---|
| `region_id` | Permanent | Primary key |
| `environment.*` | Permanent | Step 1 environment mismatch filter, Step 4 scoring |
| `destination_character.*` | Permanent | Step 3 goal fit interpretation, Step 4 scoring |
| `seasonality.best_months` | Semi-stable | Step 1 season filter, Step 4 seasonality scoring |
| `seasonality.avoid_months` | Semi-stable | Step 1 hard elimination |
| `cost.in_destination_tier` | Semi-stable | Step 1 budget filter, Step 2 total cost calculation |
| `constraints.trekking_required` | Permanent | Step 1 no_trekking exclusion |
| `constraints.beach` | Permanent | Step 1 no_beach exclusion |
| `constraints.connectivity` | Permanent | Step 1 workation filter, Step 3 goal fit |
| `constraints.luxury_inventory_available` | Permanent | Step 1 luxury exclusion |
| `constraints.altitude_above_4000m` | Permanent | Step 3 goal fit — health/family implications |
| `constraints.medical_facilities_limited` | Permanent | Step 3 goal fit — safety implications |
| `constraints.road_conditions_challenging` | Permanent | Step 3 goal fit — logistics burden |
| `experience_attributes.walkability` | Permanent | Step 4 nuanced preference scoring |
| `experience_attributes.local_authenticity` | Permanent | Step 4 nuanced preference scoring |
| `experience_attributes.food_accessibility` | Permanent | Step 4 nuanced preference scoring |
| `experience_attributes.spontaneity_friendly` | Permanent | Step 4 nuanced preference scoring |
| `crowd_profile.*` | Semi-stable | Step 1 crowd filter, Step 4 crowd scoring |
| `uniqueness_factor` | Permanent | Step 4 uniqueness scoring |
| `known_for` | Permanent | Meridian output reasoning |

---

## What the KB Does Not Store

| Data | Reason | Handled By |
|---|---|---|
| Trip duration | Depends on traveler | Planner |
| Recommended nights | Depends on traveler | Planner |
| Origin proximity | Depends on traveler origin | Meridian Step 2 (live) |
| Transport cost | Changes, origin-dependent | Meridian Step 2 (live) |
| Route and itinerary | Depends on traveler | Planner |
| Stop combinations | Depends on duration and traveler | Planner |
| Current prices | Live data | Web search at query time |
| Live travel times | Live data | Maps API at query time |
| Road and weather conditions | Live data | Live sources at query time |
| trip_goal_fit scores | Traveler-dependent interpretation | Meridian Step 3 |
| travel_style_fit scores | Traveler-dependent interpretation | Meridian Step 3 |
| group_fit scores | Traveler-dependent interpretation | Meridian Step 3 |
| not_suitable_for | Derived recommendation | Meridian Step 3 |

---

## Storage

### Collections

All profiles stored in a single Supabase table:

```
kb_regions  →  travel region profiles
```

Folder in KB repo:
```
kb/regions/  →  kb_regions
```

### Retrieval

Step 1 — SQL filtering on structured fields:
```
cost.in_destination_tier      → budget matching
seasonality.avoid_months      → season elimination
seasonality.best_months       → season scoring
constraints.*                 → hard exclusion flags
environment.*                 → environment mismatch elimination
crowd_profile.typical_level   → crowd filter
```

Step 4 — Fact retrieval for scoring:
```
destination_character.*       → character and goal fit scoring
experience_attributes.*       → nuanced preference scoring
uniqueness_factor             → uniqueness scoring
crowd_profile.*               → crowd scoring
```

Future — Semantic retrieval (Phase 2, when pgvector is enabled):
```
Full profile vector embedding for intent matching
```

### Ingest Pipeline

Script reads YAML from repo, validates against `global_rules.yaml`, upserts into Supabase. Run manually for MVP. GitHub Action trigger added in Phase 2.

Key ingest validations:
- `schema_version` must match current version
- All enum fields validated against `global_rules.yaml`
- `region_id` must be unique
- Files failing validation are rejected — not stored

---

## Population Strategy

### Phase 0 — Freeze schema before generating any data

Schema must be frozen before generating a single profile. Generating against an unstable schema means regenerating everything when fields change.

---

### Phase 1 — Seed high-traffic regions first

**Tier 1 — Major travel regions (highest priority)**

```
Rajasthan         →  Jaipur–Jodhpur–Udaipur belt
Golden Triangle   →  Delhi–Agra–Jaipur
Himachal          →  Shimla–Manali corridor
Spiti Valley      →  Kaza–Kalpa–Chandratal belt
Kashmir Valley    →  Srinagar–Gulmarg–Pahalgam
Ladakh            →  Leh–Nubra–Pangong
Kerala            →  Kochi–Munnar–Alleppey–Kovalam belt
Karnataka Coast   →  Gokarna–Murudeshwar–Karwar
Coorg             →  Madikeri–Nagarhole belt
Meghalaya         →  Shillong–Cherrapunji–Dawki
Northeast         →  Guwahati–Kaziranga–Majuli
Andaman Islands   →  Port Blair–Havelock–Neil
Uttarakhand       →  Rishikesh–Mussoorie–Chopta belt
Rajasthan Desert  →  Jaisalmer–Bikaner belt
Hampi             →  Hampi–Badami–Pattadakal
Tamil Nadu        →  Pondicherry–Thanjavur–Madurai belt
```

**Tier 2 — High-traffic single-city-adjacent regions**

```
From Bengaluru   →  Coorg, Wayanad, Chikmagalur, Hampi, Gokarna, Pondicherry
From Mumbai      →  Lonavala, Mahabaleshwar, Alibaug, Kashid
From Delhi       →  Rishikesh, Mussoorie, Agra, Shimla, Nainital, Corbett
From Hyderabad   →  Warangal, Araku, Coorg, Hampi
From Chennai     →  Pondicherry, Ooty, Kodaikanal, Mahabalipuram, Varkala
From Pune        →  Lonavala, Mahabaleshwar, Kashid, Alibaug
```

**Tier 3 — Offbeat regions**

After Tier 1 and 2 are solid:
```
Ziro, Tawang, Majuli, Chopta, Dholavira, Munsiyari, Dzukou Valley
```

Human review mandatory — LLMs consistently underestimate remoteness, mischaracterise shoulder months, and overstate food accessibility.

---

### Phase 2 — Generation pipeline for each profile

```
1. LLM generation
   Feed schema template
   Instruct to leave fields blank rather than guess

2. Web verification
   Seasonality accuracy
   Crowd levels
   Cost tier
   Constraint facts

3. Human review
   Focus on environment, constraints, experience attributes
   These are the fields Meridian uses most heavily

4. Confidence scoring
   generated → verified → curated

5. Ingest validation
   Enum check, ID uniqueness, required fields

6. Store in Supabase
```

---

### Phase 3 — Dynamic growth (post-launch)

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
| Freeze schema and global rules | 1–2 days |
| Generate Tier 1 region profiles | 1 day |
| Human review of Tier 1 profiles | 2–3 days |
| Generate Tier 2 profiles | 1 day |
| Web verification pass | 2–3 days |
| Human review of high-traffic profiles | 3–5 days |
| Set up Supabase table and ingest pipeline | 1 day |
| Run ingest and validate | Half a day |

**Total: approximately 2–3 weeks for a solid pre-launch KB.**
