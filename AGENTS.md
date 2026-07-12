# Travel With Me backend

This repository owns backend APIs, agent prompts and workflows, request/response schemas, and server-side business logic. Follow the workspace-level `AGENTS.md` in addition to these repository-specific rules.

## Scope and issue naming

- Prefix every Linear implementation-story title for this repository with `[BE]`.
- Use the prefix only in Linear. Do not add `[BE]` to branch names, commit messages, or pull-request titles unless the user explicitly requests it.
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

- Require documentation when a story changes product behavior, a shared/public contract, prompt behavior, state ownership, architecture, or a material operational workflow.
- Do not require standalone documentation for every small internal or technical change. Tests, concise PR context, and code comments are sufficient when no product or operational contract changes.
- Include only genuinely affected documentation in story scope, acceptance criteria, and verification.
- Keep product behavior and shared-contract docs in `TWM_Docs/`, including Scout/Meridian behavior, the playbook, product architecture, TripState/stages/CTA mappings, and shared API/user flows.
- Keep backend technical and operational docs in this repository, including prompt versioning/changelogs, FastAPI internals, n8n, EC2, deployment/runtime configuration, and backend troubleshooting.
- Do not create a duplicate `TWM_Docs/` change for backend-only technical or operational documentation.
- A backend story is not complete while affected product, contract, or operational documentation is stale.

## Verification

- Run focused prompt/contract regression tests plus relevant backend tests for every changed path.
- Validate both Scout and Meridian handoff behavior when routing or shared context changes.
- For API changes, verify normalized FastAPI responses rather than only raw n8n/LLM output.
- Report backend checks, documentation verification, compatibility risks, and rollback instructions separately from UI results.

## Git delivery

- Use a backend-specific branch and pull request.
- Stage only intended files in a dirty worktree.
- Do not commit, push, open, or merge a pull request without the explicit gate required by the workspace instructions.
- An explicitly authorized `AGENTS.md` process-configuration update does not need a Linear story, but all requested Git delivery gates still apply.
- A request to address PR review comments authorizes brief replies and resolution of verified addressed threads unless the user says otherwise.
