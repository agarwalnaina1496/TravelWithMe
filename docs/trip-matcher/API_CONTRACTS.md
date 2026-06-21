# Trip Matcher API Contracts

This document records the API contract between the UI and Backend

## Overview

Trip Matcher has two primary calls:

```text
Scout    -> every user interaction
Meridian -> only when the user taps Generate
```

Trip Matcher is stateless:

```text
- no TripState storage
- no conversation history
- no session tracking
- every request is self-contained
```

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
- post-Meridian recommendation presentation
- refinement
- destination confirmation
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

Every Scout response returns only a `state_delta`. The UI deep-merges `state_delta` back into `TripState` and stores the updated result.

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

`message: string` means regular user interaction.

`message: null` means Scout should infer the situation from `trip_state`, usually one of:

```text
- post-Meridian presentation if matcher_state.last_recommendations exists
- page refresh / re-engagement if last_recommendations is null
```

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
      "generate_ready": false,
      "conversation_context": {
        "last_scout_message": "What's your total budget for the trip?",
        "awaiting": "budget"
      },
      "last_recommendations": null
    },
    "planner_state": null
  },
  "message": "30k total, 3 nights, we are 4 people"
}
```

### Request Example: Post-Meridian Presentation

The UI first writes Meridian output to `matcher_state.last_recommendations`, then calls Scout with `message: null`.

```json
{
  "trip_state": {
    "trip_id": "trip_7f3a9c",
    "status": "free",
    "stage": "ready",
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
        "travel_style": ["relaxed", "social"]
      },
      "selected_option": null
    },
    "matcher_state": {
      "generate_ready": false,
      "conversation_context": {
        "last_scout_message": "Perfect - ready when you are.",
        "awaiting": null
      },
      "last_recommendations": {
        "options": [
          "pondicherry_puducherry",
          "hampi_karnataka",
          "goa_north"
        ],
        "best_match": "pondicherry_puducherry",
        "generated_at": "2025-09-15T10:45:00Z",
        "meridian_output": {}
      }
    },
    "planner_state": null
  },
  "message": null
}
```

### Response

Scout always returns the same top-level shape:

```json
{
  "message": "string",
  "state_delta": {
    "stage": "string | omit if unchanged",
    "trip_context": {
      "required_inputs": {},
      "preferences": {},
      "selected_option": {}
    },
    "matcher_state": {
      "generate_ready": "boolean | omit if unchanged",
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
- Omit sections with no changes.
- matcher_state.conversation_context.last_scout_message should be included.
```

### Response Example: Still Collecting

```json
{
  "message": "Got it - INR 30,000 total, 3 nights, 4 people from Bengaluru. Is there anything else you'd like me to factor in?",
  "state_delta": {
    "stage": "matching",
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
      "generate_ready": false,
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
    "stage": "ready",
    "trip_context": {
      "required_inputs": {},
      "preferences": {
        "nuanced_preferences": [
          "Wants a chill celebration vibe, not a big party scene"
        ]
      }
    },
    "matcher_state": {
      "generate_ready": true,
      "conversation_context": {
        "last_scout_message": "Got it - I'll generate recommendations from what you've shared so far.",
        "awaiting": null
      }
    }
  }
}
```

### Response Example: Presentation

```json
{
  "message": "Found three options. Pondicherry comes up top - fits your INR 30,000 budget cleanly at about INR 6,000 per person. Hampi is a strong alternative if you want something more photogenic and offbeat. Goa is possible but tight. Want me to walk through any of these in detail?",
  "state_delta": {
    "stage": "ready",
    "matcher_state": {
      "generate_ready": false,
      "conversation_context": {
        "last_scout_message": "Found three options. Pondicherry comes up top...",
        "awaiting": "user_response_to_recommendations"
      }
    }
  }
}
```

### Response Example: Destination Confirmed

```json
{
  "message": "Pondicherry it is. Great choice for a September bachelorette trip.",
  "state_delta": {
    "stage": "matched",
    "trip_context": {
      "required_inputs": {},
      "preferences": {},
      "selected_option": {
        "type": "destination",
        "id": "pondicherry_puducherry"
      }
    },
    "matcher_state": {
      "generate_ready": false,
      "conversation_context": {
        "last_scout_message": "Pondicherry it is. Great choice for a September bachelorette trip.",
        "awaiting": null
      }
    }
  }
}
```

### Response Example: Failure Presentation

```json
{
  "message": "Nothing came through that matched everything - the main conflict is that September is tricky for crowd-free options near Bengaluru. Would you be open to October instead?",
  "state_delta": {
    "stage": "matching",
    "matcher_state": {
      "generate_ready": false,
      "conversation_context": {
        "last_scout_message": "Nothing came through that matched everything...",
        "awaiting": "constraint_relaxation"
      }
    }
  }
}
```

## POST /meridian

Called only when the user taps Generate.

Scout never calls Meridian directly.

The traveler decides when to generate recommendations.

Scout reflects that traveler intent by returning:

```text
state_delta.matcher_state.generate_ready = true
```

The UI uses this flag only to show the Generate Recommendations button. Meridian is called only after the traveler clicks that button.

`generate_ready` should be set because the traveler asked for recommendations, not merely because the minimum structured inputs are present.

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

```json
{
  "status": "HARD_FAIL",
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

if generate_ready = false:
  continue conversation

if generate_ready = true:
  show Generate Recommendations button because the traveler has indicated recommendation intent
```

### Generate Recommendations

```text
User taps Generate Recommendations
  -> button enters loading state
  -> POST /meridian
       body: { trip_context: trip_state.trip_context }
  -> write full Meridian response to:
       trip_state.matcher_state.last_recommendations
  -> POST /scout
       body: { trip_state with last_recommendations, message: null }
  -> deep-merge Scout state_delta into localStorage
  -> render Scout presentation
  -> hide Generate button
```

Meridian output, success or failure, always goes to `last_recommendations` before calling Scout.

The UI does not need to distinguish Meridian success from business failure before calling Scout.

### Refinement

```text
User sends adjustment
  -> POST /scout
       body: { trip_state, message: "What if we go in October instead?" }
  -> Scout extracts adjustment
  -> UI deep-merges state_delta into TripState

if generate_ready = true:
  show Generate button again because the traveler has indicated recommendation intent
  user taps
  -> POST /meridian
  -> POST /scout with message: null
  -> updated recommendations shown
```

### Confirmation

```text
User confirms destination
  -> POST /scout
       body: { trip_state, message: "Let's go with Pondicherry" }
  -> Scout returns:
       state_delta.trip_context.selected_option = { type, id }
       state_delta.stage = "matched"
  -> UI deep-merges into localStorage
  -> Trip Matcher complete
```

### Page Refresh

```text
User refreshes
  -> UI reads trip_state from localStorage

stage = "new" or "matching":
  POST /scout with { trip_state, message: null }
  Scout resumes from conversation_context

stage = "ready":
  show Generate button
  POST /scout with { trip_state, message: null }
  Scout re-engages from last_scout_message

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
  -> write { status: "API_ERROR" } to last_recommendations
  -> POST /scout with message: null
  -> Scout translates into a retry-friendly message
```

### Meridian Business Failure

```text
Meridian returns HARD_FAIL / SOFT_FAIL / BUDGET_FAIL / CONFLICT_FAIL
  -> write failure response to last_recommendations
  -> POST /scout with message: null
  -> Scout translates into a natural follow-up question
  -> user adjusts
  -> refinement loop continues
```
