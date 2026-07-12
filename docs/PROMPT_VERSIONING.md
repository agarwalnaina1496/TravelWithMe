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

## Runtime Boundary

This mechanism defines prompt releases only. FastAPI response provenance is a separate capability. Runtime version metadata must be attached by backend code, not trusted from LLM or n8n output.
