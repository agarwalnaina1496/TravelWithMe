# Trip Matcher

Trip Matcher is Phase 1 of TravelWithMe.

Its job is to help a traveler move from an open-ended trip idea to a confident destination shortlist.

## Scope

Trip Matcher currently focuses on:

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

Meridian
  -> recommendation engine
  -> evaluates trip context and returns destination matches
```

## API Contracts

See [API contracts](API_CONTRACTS.md).

## Architecture And Operations

- [Architecture](ARCHITECTURE.md)
- [Current EC2 deployment](EC2_SETUP.md)
- [Runbook](RUNBOOK.md)
- [n8n-specific notes](SELF_HOSTED_N8N.md)
