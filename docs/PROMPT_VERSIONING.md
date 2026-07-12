# Prompt Versioning

Scout and Meridian are runtime prompts that evolve independently. Each deployed prompt has a human-readable semantic version so behavior can be released and debugged without using a Git commit as the product-facing identifier.

## Files

```text
twm/prompts/
  scout.md
  meridian.md
  versions.json
  CHANGELOG.md
```

`versions.json` contains exactly one version per supported prompt:

```json
{
  "scout": "1.0.0",
  "meridian": "1.0.0"
}
```

The prompt changelog contains a heading for each current release. The initial `1.0.0` versions establish tracked releases. Meridian's legacy/model-returned `matcher_v2` value describes an output contract and is not prompt provenance.

## Version Policy

```text
PATCH  wording or constraint clarification intended to preserve the contract
MINOR  backward-compatible behavioral capability or instruction change
MAJOR  breaking routing, ownership, state, or response-contract behavior
```

Every behavioral prompt change must update the corresponding version and changelog entry in the same backend change. Scout and Meridian versions move independently.

## Validation

Backend loading rejects missing, malformed, incomplete, unknown, or non-semantic versions. Every current version must also have a matching changelog heading.

The repository check compares prompts with a supplied Git base ref and requires a version bump plus changelog heading for each changed prompt:

```powershell
python scripts/check_prompt_version_changes.py origin/main
```

The first versioning change is treated as a bootstrap because its base ref has no version registry.

## Runtime provenance

FastAPI attaches backend-owned provenance to every normalized Scout and Meridian response:

```json
{
  "agent_meta": {
    "agent": "scout",
    "prompt_version": "1.0.0"
  }
}
```

The backend loads prompt content and its version as one release before agent execution, then attaches `agent_meta` during response normalization. Any `agent_meta` claimed by LLM or n8n output is ignored. Missing or invalid version configuration stops execution rather than returning misleading provenance.

Meridian's legacy top-level `version` remains part of its existing output contract for compatibility, but it is not prompt provenance. Consumers must use `agent_meta.prompt_version` when identifying the deployed prompt release. Error responses produced after a valid prompt release is loaded also include `agent_meta`; configuration failures that prevent loading a release do not produce a normalized agent response.

## Pull request enforcement

The GitHub Actions workflow at `.github/workflows/ci-runner.yaml` installs backend dependencies, runs the prompt version policy check, and executes backend tests for pull requests targeting `main`. The policy check compares the pull request with its base branch and fails when a changed Scout or Meridian prompt does not include the corresponding version bump and changelog heading.

The workflow delegates the policy logic to `scripts/check_prompt_version_changes.py`; the script remains the single implementation that developers can also run locally. To prevent merging after a failure, repository branch protection or a ruleset must require the generic `CI Runner` status check. Additional backend CI validations can be added to the same runner over time.
