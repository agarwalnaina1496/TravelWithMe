# Travel With Me backend

This repository owns backend APIs, agent prompts and workflows, request/response schemas, and server-side business logic. Follow the workspace-level `AGENTS.md` in addition to these repository-specific rules.

## Scope and issue naming

- Prefix every implementation story for this repository with `[BE]`.
- Keep backend work in this repository. Coordinate separate `[UI]` work when a contract or user flow also requires frontend changes.
- Do not include unrelated prompt, workflow, documentation, or refactoring changes in the same branch or pull request.

## Product intent and discovery

- The user's explicit decisions in the active discovery define intended product behavior.
- Treat prompts, code, tests, and documentation as evidence of current behavior. Surface conflicts and ask the user to decide rather than silently selecting an artifact as product authority.
- Inspect the relevant UI integration before finalizing changes to an API or end-to-end user flow.

## Agent prompts and workflows

- Treat `twm/prompts/scout.md` and `twm/prompts/meridian.md` as independently evolving runtime prompts.
- Preserve the separation between traveler-provided context, agent-owned operational state, and UI-owned deterministic lifecycle state.
- Prompt behavior changes must include representative regression cases covering extraction, routing, response shape, and state ownership as applicable.
- Once file-based prompt versioning is present, every behavioral prompt change must bump the corresponding prompt version and update the prompt changelog in the same change.
- Prompt or workflow version metadata must be attached deterministically by backend code; do not trust an LLM-generated version value as provenance.

## API and state contracts

- Backend request and response schemas are the implementation source of truth for API contracts after the intended behavior is approved.
- Keep FastAPI schemas, response normalization, agent-engine forwarding, and workflow structured-output schemas aligned.
- Agents must not write UI-owned deterministic fields such as lifecycle stage, selected option, or stored recommendation history unless an approved contract explicitly changes ownership.
- Prefer backward-compatible response additions. Document compatibility and rollout sequencing for any breaking change.

## Documentation

- Documentation updates are mandatory for every implementation story.
- Include affected API contracts, prompt behavior, state ownership, versioning rules, and operational flow in the story scope, acceptance criteria, and verification plan.
- When canonical documentation lives in `TWM_Docs/`, plan and verify the coordinated documentation change without folding that repository into this repository's Git history or pull request.
- A backend story is not complete while its documented behavior or contract is stale.

## Verification

- Require unit tests for backend code changes. Prompt-only Markdown changes do not require unit tests that assert prompt text.
- Validate prompt-only changes through representative behavioral cases, prompt release/version checks, structured-output checks, and relevant manual or workflow verification.
- When a change includes both prompt and backend code, run the relevant backend unit tests in addition to prompt-behavior verification.
- Validate both Scout and Meridian handoff behavior when routing or shared context changes.
- For API changes, verify normalized FastAPI responses rather than only raw n8n/LLM output.
- Report backend checks, documentation verification, compatibility risks, and rollback instructions separately from UI results.

## Git delivery

- Use a backend-specific branch and pull request.
- Stage only intended files in a dirty worktree.
- Do not commit, push, open, or merge a pull request without the explicit gate required by the workspace instructions.
