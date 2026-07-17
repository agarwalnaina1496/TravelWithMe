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

<!-- twm-codex-basekit: START -->
## Travel With Me workspace delivery rules

The Travel With Me product is split across independent repositories:

- `TravelWithMe/`: Backend APIs, agents, schemas, prompts, workflows, and server-side business logic.
- `TWM-UI/`: Frontend behavior, UI state, persistence, and Backend integration.
- `TWM_Docs/`: Canonical product behavior and shared-contract documentation.

### Repository boundaries

- Preserve every repository's independent Git history.
- Keep branches, commits, verification results, and pull requests separate by repository.
- Modify only repositories with a proven implementation or documentation delta.
- Avoid unrelated refactors, formatting churn, compatibility layers, and migration paths.
- Treat the product as pre-MVP unless the user explicitly decides otherwise.

### Product intent

- Treat the user's confirmed decisions in active discovery as the authority for intended behavior.
- Use code, prompts, tests, workflows, and documentation as evidence of current behavior, not as authority over a conflicting confirmed decision.
- Surface conflicts or material ambiguity before finalizing scope.
- Record confirmed decisions in the proposed work breakdown and approved Linear issues.

### Mandatory delivery workflow

1. Begin with read-only discovery across every potentially affected repository.
2. Assess Backend, UI, product documentation, and n8n as distinct delivery surfaces. Mark each in scope, out of scope, no change required, or requiring further investigation.
3. Prove a current-versus-required delta before proposing an implementation child story.
4. Present a consolidated work breakdown before creating or updating Linear issues.
5. Wait for explicit approval before writing Linear issues.
6. Wait for explicit selection or approval of a Linear implementation story before editing files.
7. Before editing, confirm the story, repositories, acceptance criteria, and branch plan.
8. Before the first commit, create and switch to a non-default delivery branch in each affected repository. Never commit or push directly to a default branch unless the user explicitly authorizes that exact exception; general approval to "commit and push" does not authorize default-branch delivery.
9. Implement only approved scope. Return to planning for material expansion or an unapproved contract change.
10. Run repository-specific verification and present diffs, results, limitations, and rollback instructions.
11. Wait for separate explicit approvals for commit, push, pull-request creation, and merge.

### Linear structure

- Apply the `Feature` label to a parent capability or delivery container.
- Structure implementation children around dependency-ordered delivery increments, not around repository count alone. Start with prerequisite contract or foundation work, followed by coordinated implementation, end-to-end integration, and documentation where each increment has a proven delta.
- Use separate repository-prefixed children when work is independently implementable or reviewable, or when one piece must block another.
- When Backend and UI tasks are inseparable parts of one useful increment, keep them in one cross-repository story with separate repository-specific task checklists and a combined `[BE][UI]` title prefix. Retain separate branches, commits, verification results, and pull requests per repository.
- Do not create child stories merely to mirror repositories or task groups. Keep checklist tasks inside the story that owns the delivery increment and order them by prerequisite.
- Encode the approved delivery sequence with Linear blocker relationships.
- Prefix implementation-story titles with `[BE]`, `[UI]`, `[BE][UI]`, or `[DOCS]` for the corresponding product repository scope.
- Use `[BASEKIT]` for this independently versioned Codex Basekit.
- Do not add repository prefixes to branches, commits, or pull-request titles unless explicitly requested.
- Create children only for proven changes; record `no change required` on the parent for satisfied surfaces.
- Include problem and outcome, scope, out-of-scope items, affected repositories, acceptance criteria, contract impact, dependencies, verification, and rollback.
- Keep parent Feature descriptions concise and avoid duplicating child task lists, implementation hierarchy, or story-reference catalogs already represented by Linear parent and blocker relationships.
- Prefer one `[DOCS]` child for a capability's canonical product and shared-contract documentation after the behavior is verified, unless documentation is an earlier independent blocker.
- Do not add a standalone Risks section while the product is pre-launch; place concrete constraints with the relevant scope, dependency, or verification item.

### Contracts and coordination

- Treat approved Backend request and response schemas as the implementation source of truth.
- Inspect both Backend and UI for API or user-flow changes.
- Define shared request and response contracts before implementation.
- Record whether coordinated work is independent or blocked, and include deployment ordering only when concretely required.
- Do not add legacy compatibility or rollout layers unless explicitly requested.

### Documentation routing

- Keep canonical product behavior and shared contracts in `TWM_Docs/`.
- Keep Backend technical and operational documentation in `TravelWithMe/`, including prompts, n8n, FastAPI internals, runtime configuration, deployment, and troubleshooting.
- Do not duplicate Backend-only operational documentation in `TWM_Docs/`.
- Require documentation changes only for affected product behavior, user-facing flows, shared contracts, prompt behavior, state ownership, architecture, or material operational workflows.

### Verification and Git delivery

- Run relevant tests, linters, type checks, builds, and focused manual verification in every modified repository.
- Verify affected documentation matches implemented behavior.
- Stage only intended files in dirty worktrees.
- Keep commits small, traceable to the approved Linear story, and easy to revert.
- Never merge a pull request without explicit user approval.

# Travel With Me Backend

This repository owns Backend APIs, agent prompts and workflows, request and response schemas, and server-side business logic.

## Scope and discovery

- Use `[BE]` only in Linear implementation-story titles.
- Inspect the relevant UI integration before finalizing an API or end-to-end user-flow change.
- Keep unrelated prompt, workflow, documentation, and refactoring changes out of the same branch.
- Treat n8n as a distinct scope-assessment surface even though its workflow files and operational ownership live here.

## Prompts and workflows

- Treat `twm/prompts/scout.md` and `twm/prompts/meridian.md` as independently evolving runtime prompts.
- Preserve separation between traveler context, agent-owned operational state, and UI-owned deterministic lifecycle state.
- Add representative regression cases for prompt behavior changes as applicable to extraction, routing, response shape, and state ownership.
- When file-based prompt versioning is present, bump the corresponding version and changelog with every behavioral prompt change.
- Attach prompt and workflow provenance deterministically in Backend code; do not trust an LLM-generated version value.

## API and state contracts

- Keep FastAPI schemas, response normalization, agent-engine forwarding, and workflow structured-output schemas aligned.
- Do not let agents write UI-owned fields such as lifecycle stage, selected option, or stored recommendation history without an approved ownership change.
- Verify normalized FastAPI responses, not only raw workflow or LLM output.
- Do not preserve superseded request, response, or state shapes by default.

## Documentation and verification

- Keep product behavior and shared contracts in `TWM_Docs/`.
- Keep Backend technical and operational documentation here, including prompt changelogs, FastAPI internals, n8n, EC2, deployment, runtime configuration, and troubleshooting.
- Require unit tests for Backend code changes. Prompt-only Markdown changes require representative behavioral and structured-output verification rather than tests that assert prompt text.
- Validate Scout and Meridian handoff behavior when routing or shared context changes.
- Report Backend checks, documentation verification, limitations, and rollback separately from UI results.

## Git delivery

- Use a Backend-specific branch and pull request.
- Stage only intended files in a dirty worktree.
<!-- twm-codex-basekit: END -->
