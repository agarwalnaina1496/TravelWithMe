# Trip Matcher API Contracts

This document records the API contract between the UI and Backend.

## Overview

Trip Matcher has two primary calls:

```text
Scout    -> conversation turns
Meridian -> only after the traveler taps Generate
```

Trip Matcher logic is stateless:

```text
- no conversation history
- no session state
- every call operates on complete TripState
```

For the canonical state model, field ownership, storage model, and stage transitions, see [TripState](../TRIP_STATE.md).

## Endpoints

```text
POST /scout
POST /meridian
```

## POST /scout

Handles all Scout interactions.

Scout is called for:

```text
- regular user messages
- active matching resume with message = null
- refinement messages
- destination or circuit confirmation messages
```

Scout operates on complete `TripState` for every call.

Current MVP request model:

```text
UI sends full TripState.
```

Future database-backed request model:

```text
UI may send trip_id.
TripState may be loaded before Scout runs.
```

The invariant is:

```text
Scout gets complete TripState.
Scout does not rely on hidden memory outside TripState.
```

Every Scout response returns a `state_delta`. The UI deep-merges `state_delta` back into `TripState` and stores the updated result.

The UI owns deterministic actions such as Generate Recommendations, Choose Option, and Refine with Scout. Scout signals intent through conversation; the UI writes deterministic state changes.

### Request

```json
{
  "trip_state": {},
  "message": "string | null"
}
```

Fields:

```text
trip_state -> full current TripState from localStorage
message    -> latest user message, or null
```

`message: string` means regular user interaction, including refinement.

`message: null` is only used to resume an active matcher conversation when `trip_state.stage = "matching"`.

`message: null` is not used as a post-Meridian presentation mode, refinement trigger, or option confirmation mode. Meridian returns presentable recommendation content, and the UI renders it directly.

### Request Example: Regular Turn

```json
{
  "trip_state": {
    "trip_id": "trip_7f3a9c",
    "status": "free",
    "stage": "matching",
    "trip_context": {
      "required_inputs": {
        "origin_city": "Bengaluru",
        "budget": 30000,
        "budget_unit": "total",
        "duration_nights": null,
        "num_travelers": null,
        "travel_month": "September"
      },
      "preferences": {
        "trip_goal": {
          "value": "Bachelorette",
          "confidence": "explicit"
        },
        "group_type": "friends"
      },
      "selected_option": null
    },
    "matcher_state": {
      "recommendation_intent": false,
      "conversation_context": {
        "last_scout_message": "What's your total budget for the trip?",
        "awaiting": "budget"
      },
      "recommendations": [],
      "rejected_options": []
    },
    "planner_state": null
  },
  "message": "30k total, 3 nights, we are 4 people"
}
```

### Response

Scout always returns the same top-level shape:

```json
{
  "message": "string",
  "state_delta": {
    "trip_context": {
      "required_inputs": {},
      "preferences": {}
    },
    "matcher_state": {
      "recommendation_intent": "boolean | omit if unchanged",
      "conversation_context": {
        "last_scout_message": "string",
        "awaiting": "string | null"
      }
    }
  }
}
```

Rules:

```text
- UI deep-merges state_delta into TripState.
- UI does not replace full sections unless the delta explicitly contains replacement values.
- Only changed fields should appear.
- Arrays in state_delta are append-style. The UI appends new array items and should avoid exact duplicates.
- Omit sections with no changes.
- matcher_state.conversation_context.last_scout_message should be included.
- Scout must not return stage, trip_context.selected_option, matcher_state.recommendations, or matcher_state.rejected_options.
```

### Response Example: Still Collecting

```json
{
  "message": "Got it - INR 30,000 total, 3 nights, 4 people from Bengaluru. Is there anything else you'd like me to factor in?",
  "state_delta": {
    "trip_context": {
      "required_inputs": {
        "budget": 30000,
        "budget_unit": "total",
        "duration_nights": 3,
        "num_travelers": 4
      },
      "preferences": {}
    },
    "matcher_state": {
      "recommendation_intent": false,
      "conversation_context": {
        "last_scout_message": "Got it - INR 30,000 total, 3 nights, 4 people from Bengaluru. Is there anything else you'd like me to factor in?",
        "awaiting": "additional_preferences"
      }
    }
  }
}
```

### Response Example: Recommendation Intent

```json
{
  "message": "Got it - I'll generate recommendations from what you've shared so far.",
  "state_delta": {
    "trip_context": {
      "required_inputs": {},
      "preferences": {
        "nuanced_preferences": [
          "Wants a chill celebration vibe, not a big party scene"
        ]
      }
    },
    "matcher_state": {
      "recommendation_intent": true,
      "conversation_context": {
        "last_scout_message": "Got it - I'll generate recommendations from what you've shared so far.",
        "awaiting": null
      }
    }
  }
}
```

## POST /meridian

Called only after the traveler taps Generate.

Scout never calls Meridian directly.

The traveler decides when to generate recommendations.

Scout passes that traveler intent to the UI by returning:

```text
state_delta.matcher_state.recommendation_intent = true
```

The UI uses this flag to move to `recommendation_ready` and display the Generate Recommendations button. Meridian is called only after the traveler taps that button.

`recommendation_intent` should be set because the traveler asked for recommendations, not because Scout inferred readiness or because the minimum structured inputs are present.

### Request

The UI sends `trip_state.trip_context` directly:

```json
{
  "trip_context": {
    "required_inputs": {
      "origin_city": "Bengaluru",
      "budget": 30000,
      "budget_unit": "total",
      "duration_nights": 3,
      "num_travelers": 4,
      "travel_month": "September"
    },
    "preferences": {
      "trip_goal": {
        "value": "Bachelorette",
        "confidence": "explicit"
      },
      "crowd_tolerance": {
        "value": "low",
        "confidence": "high"
      },
      "group_type": "friends",
      "travel_style": ["relaxed", "social"],
      "nuanced_preferences": [
        "Wants a chill celebration vibe, not a big party scene",
        "Prefers boutique or aesthetic stays"
      ]
    }
  }
}
```

### Success Response

```json
{
  "status": "SUCCESS",
  "generated_at": "2025-09-15T10:45:00Z",
  "version": "matcher_v1",
  "trip_type": "single",
  "budget_basis": {
    "total": 30000,
    "per_person": 7500,
    "num_travelers": 4
  },
  "options": [
    {
      "rank": 1,
      "type": "single",
      "name": "Pondicherry (Puducherry)",
      "destination_id": "pondicherry_puducherry",
      "match_sections": [
        {
          "type": "budget",
          "heading": "Realistic budget breakdown (3 nights, 4 people)",
          "stay": {
            "notes": "Boutique guesthouse near promenade",
            "estimate_per_person": 2625,
            "estimate_group": 10500,
            "assumption": "avg ~3500 INR/night for whole unit"
          },
          "food": {
            "notes": "Cafes, beachside dinners, mix of mid-range and cheap eats",
            "estimate_per_person": 1200,
            "estimate_group": 4800,
            "assumption": "~400 INR/day/person"
          },
          "travel": {
            "notes": "AC Volvo / private shared taxi from Bengaluru",
            "estimate_per_person": 1200,
            "estimate_group": 4800,
            "assumption": "1000-1500 INR per person roundtrip by bus"
          },
          "activities": {
            "notes": "Auroville visit, beach time, photography spots",
            "estimate_per_person": 500,
            "estimate_group": 2000,
            "assumption": "light paid activities"
          },
          "local_transport": {
            "notes": "Scooter rental / tuk-tuks",
            "estimate_per_person": 500,
            "estimate_group": 2000,
            "assumption": "scooter 300-500 INR/day shared"
          },
          "per_person_total": 6025,
          "group_total": 24100,
          "budget_given": 30000,
          "verdict": "comfortable"
        },
        {
          "type": "trip_goal",
          "heading": "Bachelorette fit",
          "points": [
            "Boutique cafes, pastel streets, and beachfront promenades for photos and small celebrations",
            "Private guesthouse options for group privacy and in-house gatherings",
            "A few lively bars ideal for a 4-person group - celebratory without overwhelming"
          ]
        },
        {
          "type": "crowd_preference",
          "heading": "Quieter than usual in September",
          "points": [
            "Shoulder season - town is quieter than peak",
            "Some weekend pockets busier but overall not crowded",
            "More intimate than peak-season Goa"
          ]
        },
        {
          "type": "weather",
          "heading": "Some rain expected - plan flexibly",
          "contextual": true,
          "points": [
            "Tail of monsoon - warm, humid, occasional showers",
            "Rain usually short bursts - beach time still possible",
            "Auroville stays lush and photogenic after rain"
          ]
        },
        {
          "type": "reachability",
          "heading": "Practical from Bengaluru",
          "points": [
            "~6-7 hours by road - AC bus or shared taxi",
            "Overnight bus saves daytime for the trip",
            "No flight needed for a 3-night trip"
          ]
        }
      ],
      "tradeoffs": [
        {
          "point": "Nightlife is low-key - better for intimate celebrations than large party nights",
          "affects": "trip_goal"
        },
        {
          "point": "September rain can interrupt beach plans on short notice",
          "affects": "weather"
        }
      ]
    }
  ],
  "final_recommendation": {
    "best_match": "Pondicherry (Puducherry)",
    "best_match_reason": "Best balance of bachelorette vibe, manageable travel, budget comfort, and September quiet",
    "alternative_1": "Hampi (Karnataka)",
    "alternative_1_reason": "Most budget-friendly, highly photogenic, great for a creative intimate bachelorette - leaves headroom for extras",
    "alternative_2": "Goa (North Goa - budget approach)",
    "alternative_2_reason": "Classic choice - possible within budget only on bus + budget stays. September lowers crowds but also limits nightlife"
  },
  "refinement_hooks": {
    "weakest_scoring_factor": "Nightlife intensity - options vary in party energy; Pondicherry and Hampi are mellow vs peak-season Goa",
    "constraint_with_highest_elimination": "Budget (INR 30000 total) - rules out flying and mid-range stays for Goa",
    "budget_headroom": "comfortable",
    "seasonality_note": "September is monsoon tail - expect rain and humidity. Hampi and Pondicherry stay photogenic; Goa's beach nights are unreliable",
    "nuanced_preference_gaps": null
  }
}
```

### Failure Response

Meridian failures are expected business outcomes, not infrastructure errors.
Failure responses should include the same metadata fields as success responses so debugging can identify which matcher version produced the outcome.

```json
{
  "status": "HARD_FAIL",
  "generated_at": "2025-09-15T10:45:00Z",
  "version": "matcher_v1",
  "message": "No destinations matched all the stated constraints",
  "eliminating_constraints": [
    "crowd_tolerance: low",
    "travel_month: September"
  ],
  "relaxation_suggestions": [
    "Consider October - crowds similar but rain significantly lower",
    "Moderate crowd tolerance to balanced"
  ],
  "surviving_destinations": []
}
```

Supported failure statuses:

```text
HARD_FAIL
SOFT_FAIL
BUDGET_FAIL
CONFLICT_FAIL
MISSING_INPUTS
API_ERROR
```

`API_ERROR` is used only for infrastructure failures, not normal Meridian reasoning failures.

## Full UI Flow

### Input Collection Loop

```text
User sends message
  -> POST /scout
       body: { trip_state, message: "..." }
  -> UI deep-merges state_delta into trip_state
  -> UI writes trip_state to localStorage
  -> UI renders response.message

if recommendation_intent = false:
  continue conversation

if recommendation_intent = true:
  display Generate Recommendations because the traveler asked for recommendations
```

### Generate Recommendations

```text
User taps Generate Recommendations
  -> button enters loading state
  -> POST /meridian
       body: { trip_context: trip_state.trip_context }
  -> append full Meridian response to:
       trip_state.matcher_state.recommendations
  -> render Meridian response in the UI
  -> clear recommendation_intent
  -> set stage = "recommended"
```

Meridian business output, success or failure, always appends to `recommendations`.

`generated_at` and `version` must be preserved in each recommendation history item. They identify when the recommendation was produced and which matcher contract, prompt set, KB version, and scoring logic produced it.

### Confirmation

```text
User clicks Choose destination/circuit
  -> UI writes:
       trip_state.trip_context.selected_option = { type, id }
       trip_state.stage = "matched"
  -> UI writes trip_state to localStorage
  -> Trip Matcher complete
```

If the traveler expresses confirmation in chat, the UI should still own the final `selected_option` write. Chat-based confirmation handling can be added later without changing the deterministic button-click contract.

### Refinement

```text
User clicks Refine with Scout
  -> UI sets:
       trip_state.stage = "matching"
       trip_state.matcher_state.recommendation_intent = false
  -> UI sends message = "I want to refine these recommendations." to Scout
```

Refinement always uses `message: string`, never `message: null`.

If the user sends a new message while `stage = "recommendation_ready"`, the UI should first set:

```text
stage = "matching"
matcher_state.recommendation_intent = false
```

Then it sends the message to Scout. New information makes the previous readiness stale.

If the user refines after `stage = "matched"`, the UI should clear `trip_context.selected_option` and move back to `matching`.

### Page Refresh

```text
User refreshes
  -> UI reads trip_state from localStorage

stage = "new":
  UI shows the starting Scout experience

stage = "matching":
  UI may resume from conversation_context by calling Scout with message = null

stage = "recommendation_ready":
  display Generate Recommendations
  no Scout call needed

stage = "recommended":
  show existing recommendations
  no Scout call needed unless user refines

stage = "matched":
  show confirmed destination
  no Scout call needed unless user re-enters Matcher
```

## Error Handling

### Scout Failure

```text
POST /scout fails with 5xx or network error
  -> UI does not update localStorage
  -> UI shows retry message
  -> user retries with same trip_state
```

### Meridian Infrastructure Failure

```text
POST /meridian fails with 5xx or network error
  -> do not append to recommendations
  -> UI shows retry state
  -> user retries with same trip_state
```

### Meridian Business Failure

```text
Meridian returns HARD_FAIL / SOFT_FAIL / BUDGET_FAIL / CONFLICT_FAIL
  -> append failure response to recommendations
  -> UI renders Meridian response
```
