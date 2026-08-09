# Agent evaluation harness

This harness runs recorded Scout, Meridian, Guide, and Atlas completions
through the real `AgentExecutionService` pipeline (`twm/services/agent_engine/service.py`)
so that prompt-contract regressions are caught without a live n8n or
LangGraph credential.

## What it checks

For every case in `tests/resources/{scout,meridian,guide,atlas}_agent_cases.json`:

1. Every recorded fixture's `raw_output` parses and validates against the
   agent's Pydantic output model (`ScoutAgentOutput`, `MeridianAgentOutput`,
   `GuideAgentOutput`, `AtlasAgentOutput`), including one repair attempt if
   the fixture is intentionally malformed.
2. The normalized response satisfies every invariant declared in the case's
   `invariants` object (see `scripts/evaluation_harness/rubric.py`).
3. Where both a `direct` (LangGraph) and `n8n` fixture exist for the same
   case, the normalized responses match except for `agent_meta`
   (`scripts/evaluation_harness/parity.py`).
4. The fixture's recorded `prompt_version` is compared against the current
   `twm/prompts/versions.json` value; a mismatch is a warning, not a
   failure, since fixtures are expected to lag prompt releases until
   re-recorded.

## Fixtures are seed placeholders

Every file in `tests/resources/harness_fixtures/` has
`"recorded_by": "seed-placeholder"`. These are hand-authored completions
that satisfy the schemas and invariants, **not** real captured model output.
Someone with live n8n and LangGraph credentials should replace them with
actual recorded completions over time, keeping the same
`{agent}__{case_id}__{execution_path}.json` naming and `raw_output` shape
(a JSON-encoded string, not a nested object).

## Known n8n-parity gap for Guide and Atlas

`LangGraphAgentAdapter.invoke` raises `AgentAdapterError` for `guide` and
`atlas` (see `twm/services/agent_engine/langgraph.py`) — only `scout` and
`meridian` currently run on both engines. Guide and Atlas cases therefore
only have `n8n` fixtures, and the parity check in `run.py`/`test_harness.py`
is a no-op for them until a direct/LangGraph implementation exists.

## Running it

Run as a module from the repo root, not as a plain script — `python scripts/evaluation_harness/run.py`
does not put the repo root on `sys.path` and fails with `ModuleNotFoundError: No module named 'twm'`.

```bash
./venv/Scripts/python.exe -m scripts.evaluation_harness.run
./venv/Scripts/python.exe -m scripts.evaluation_harness.run --agent atlas
./venv/Scripts/python.exe -m pytest tests/unit/evaluation_harness -v
```

`run.py` exits non-zero on any rubric or schema failure, or any unexpected
parity difference. It prints a `[WARN]` line (without failing) for
invariant keys that have no rubric check yet. The only such key today is
`advisor_message_stored_in_conversation_context` (scout) — `ScoutAgentOutput`
has no field that could hold conversation-context provenance, so there is
no response-shape signal to assert on; the check raises `NotImplementedError`
rather than faking a pass. See `scripts/evaluation_harness/rubric.py` for
every implemented check.
