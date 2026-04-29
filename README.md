# Kachu v2

Kachu v2 is an agent-native AI operations assistant for micro-business owners.

The product remains centered on three rules:
- LINE is the main operator surface.
- The boss confirms external actions before they happen.
- AgentOS is runtime infrastructure, not the product itself.

## Workspace layout

- `src/kachu/` — Kachu product logic, adapters, memory, router, webhook, scheduler
- `tests/` — Kachu product and integration tests
- `scripts/release_check.py` — one entry point for Phase 6 release validation
- `scripts/smoke_phase6.py` — in-process smoke test with temporary tenant seeding + cleanup
- `scripts/deploy_phase6_prod.py` — explicit release-check -> build -> up -> smoke production helper
- `docs/boundary-contract.md` — Kachu / AgentOS responsibility boundary
- `docs/contract-test-matrix.md` — workflow boundary coverage map
- `docs/release-runbook.md` — release gate and smoke sequence
- `docs/debug-playbook.md` — production debugging path by symptom and ID

## Local development

1. Install dependencies with `pip install -e .[dev]`.
2. Run the app with `uvicorn kachu.main:app --app-dir src --reload`.
3. Run tests with `python -m pytest`.

## Phase 6 release gate

Run the full Phase 6 gate from the Kachu workspace root:

```bash
python scripts/release_check.py
```

This runs:
- Kachu tests
- AgentOS tests
- Phase 6 in-process smoke test

Optional flags:
- `--skip-kachu-tests`
- `--skip-agentos-tests`
- `--skip-smoke`

## Phase 6 intent

Phase 6 is not about adding more agent tricks.
It is about making Kachu behave like a stable product:
- release checks are repeatable
- high-frequency paths stay contract-tested
- Kachu / AgentOS boundaries stay explicit
- production issues can be traced from `task_id` and `run_id`