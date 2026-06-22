# Trip Matcher

Trip Matcher is Phase 1 of TravelWithMe.

Its job is to help a traveler move from an open-ended trip idea to a confident destination shortlist.

The success metric is simple:

```text
The traveler reaches a confident destination or circuit decision, grounded in their trip goal, preferences, and constraints.
```

## Scope

Trip Matcher focuses on:

```text
- collecting trip context from the traveler
- understanding required inputs and preferences
- generating destination recommendations
- supporting refinement until the traveler chooses a direction
```

## Agents

Trip Matcher consists of two agents:

```text
Scout
  -> traveler-facing conversation agent
  -> collects inputs and presents recommendations conversationally
```

[Scout](SCOUT.md)

```
Meridian
  -> recommendation engine
  -> evaluates trip context and returns destination or circuit matches
```
[Meridian](MERIDIAN.md)

## Flow

```text
Traveler talks to Scout
  -> Scout updates TripState
  -> Scout sets generate_ready when the traveler wants recommendations
  -> traveler clicks Generate Recommendations
  -> Meridian returns recommendations
  -> UI presents them
  -> traveler confirms a destination or circuit
```

Scout is the conversation layer. Meridian is the decision layer. TripState is the shared source of truth.

## API Contracts

See [API contracts](API_CONTRACTS.md).

## Architecture And Operations

- [Architecture](../ARCHITECTURE.md)
- [Current EC2 deployment](../EC2_SETUP.md)
- [n8n setup and workflow operations](../SELF_HOSTED_N8N.md)
