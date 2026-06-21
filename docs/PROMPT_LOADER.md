# Prompt Loader

Prompt loading is a shared backend capability, not specific to Trip Matcher.

Today it is used for Scout and Meridian prompts. Later, the same pattern can be used for Trip Planner, Booking, Concierge, or any other agent prompt.

## Purpose

Prompts should live outside workflow/code internals as editable files.

Runtime layers should load prompts from a common prompt endpoint instead of hardcoding long prompt blocks inside workflow definitions or agent code.

## Current Prompt Files

- [Scout system prompt](../twm/prompts/SCOUT_SYSTEM_PROMPT.md)
- [Meridian system prompt](../twm/prompts/MERIDIAN_SYSTEM_PROMPT.md)

Future prompt files can follow the same pattern:

```text
twm/prompts/<AGENT_NAME>_SYSTEM_PROMPT.md
```

## Current Endpoints

Plain text:

```text
GET /prompts/scout
GET /prompts/meridian
```

JSON:

```text
GET /prompts/scout/json
GET /prompts/meridian/json
```

JSON response:

```json
{
  "prompt": "..."
}
```

## Future Shape

The endpoint can remain generic as more agents are added:

```text
GET /prompts/{agent_name}
GET /prompts/{agent_name}/json
```

Examples:

```text
GET /prompts/planner
GET /prompts/booking
GET /prompts/concierge
```

## Ownership

Prompt loader docs live at the shared docs level because the capability is common across phases.

Trip-specific API contracts should only document the product APIs they expose to the UI.
