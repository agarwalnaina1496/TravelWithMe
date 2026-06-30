# Stage Transitions

## Forward Transitions

| From Stage | To Stage | Trigger |
|---|---|---|
| `new` | `matching` | First Scout matcher message |
| `matching` | `recommendation_ready` | User confirms there is nothing else to add, then Scout sets `recommendation_intent = true` |
| `recommendation_ready` | `recommended` | User clicks Generate and Meridian response is returned to UI |
| `recommended` | `matched` | User selects or clearly confirms one destination |
| `matched` | `planning` | First Scout planner message |

## Backward Transitions

| From Stage | To Stage | Trigger |
|---|---|---|
| `recommendation_ready` | `matching` | User adds or changes preferences before clicking Generate |
| `recommended` | `matching` | User rejects recommendations or asks to refine preferences |
| `matched` | `recommended` | User rejects selected destination but wants to choose from existing recommendations |
| `matched` | `matching` | User rejects selected destination and wants fresh recommendations |
| `planning` | `matched` | User changes itinerary details but keeps the same destination |
| `planning` | `recommended` | User changes destination from existing recommendations |
| `planning` | `matching` | User changes destination, budget, dates, duration, or trip goal significantly |