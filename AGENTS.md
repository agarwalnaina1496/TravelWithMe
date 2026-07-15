# Travel With Me backend

This repository owns backend APIs, agent prompts and workflows, request/response schemas, and server-side business logic. Follow the workspace-level `AGENTS.md` in addition to these repository-specific rules.

## Scope and issue naming

- Prefix every Linear implementation-story title for this repository with `[BE]`.
- Use the prefix only in Linear. Do not add `[BE]` to branch names, commit messages, or pull-request titles unless the user explicitly requests it.
- Keep backend work in this repository. Coordinate separate `[UI]` work when a contract or user flow also requires frontend changes.
- Do not include unrelated prompt, workflow, documentation, or refactoring changes in the same branch or pull request.

## Product intent and discovery

- This product is currently pre-MVP. Do not add backward compatibility, migrations, rollout layers, or support for legacy request/state shapes unless the user explicitly requests them; implement the current approved canonical contract directly.
- Inspect the relevant UI integration before finalizing changes to an API or end-to-end user flow.

## Engineering design defaults

- Treat clean architecture, object-oriented design, and SOLID principles as default implementation quality, not as separate product scope that must be repeated in every Linear issue.
- Keep FastAPI routers thin: validate HTTP input, invoke an application service, and return a validated response. Move workflow normalization, integration transport, and business rules out of router modules.
- Organize schemas and services by cohesive capability or agent as they grow. Split catch-all modules when they acquire multiple responsibilities, while preserving stable package-level imports where useful.
- Keep API contracts, application orchestration, external integrations, and configuration loading in distinct modules with explicit dependency direction.
- Prefer composition, focused protocols, and dependency injection at external boundaries. Use inheritance only when there is a genuine substitutable relationship.
- Introduce abstractions only when they clarify ownership, isolate a changing boundary, or remove meaningful duplication; avoid speculative layers and pattern-driven over-engineering.
- Preserve approved API behavior during structural refactors unless the active Linear scope explicitly changes the contract.
- Include maintainability refactoring needed to implement the approved change within that implementation story. Do not create separate Linear work for routine code organization, and do not use this default to broaden a change into unrelated cleanup.

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
- Do not preserve superseded API or state shapes by default during pre-MVP development.

## Documentation

- Keep product behavior and shared-contract docs in `TWM_Docs/`, including Scout/Meridian behavior, the playbook, product architecture, TripState/stages/CTA mappings, and shared API/user flows.
- Keep backend technical and operational docs in this repository, including prompt versioning/changelogs, FastAPI internals, n8n, EC2, deployment/runtime configuration, and backend troubleshooting.
- Do not create a duplicate `TWM_Docs/` change for backend-only technical or operational documentation.
- Keep affected Backend-owned technical or operational documentation aligned with the implementation.

## Verification

- Require unit tests for backend code changes. Prompt-only Markdown changes do not require unit tests that assert prompt text.
- Validate prompt-only changes through representative behavioral cases, prompt release/version checks, structured-output checks, and relevant manual or workflow verification.
- When a change includes both prompt and backend code, run the relevant backend unit tests in addition to prompt-behavior verification.
- Validate both Scout and Meridian handoff behavior when routing or shared context changes.
- For API changes, verify normalized FastAPI responses rather than only raw n8n/LLM output.
- Report Backend checks, affected documentation verification, known limitations, and rollback instructions separately from UI results.

## Git delivery

- Use a backend-specific branch and pull request.
- Stage only intended files in a dirty worktree.
