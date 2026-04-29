# Kachu Phase 6 Contract Test Matrix

This matrix is the Phase 6 reference for high-risk workflow boundaries.

## Photo content

| Surface | Current test coverage |
|---|---|
| Tool API smoke: `retrieve-context` | `tests/test_photo_content_e2e.py::test_retrieve_context_stub` |
| Tool API smoke: `generate-drafts` | `tests/test_photo_content_e2e.py::test_generate_drafts_stub` |
| Tool API smoke: `notify-approval` | `tests/test_photo_content_e2e.py::test_notify_approval_stores_pending` |
| Interactive dispatch parity | `tests/test_phase6_contracts.py::test_photo_content_dispatch_merges_policy_hints_and_calendar_hint` |
| Scheduler policy parity | `tests/test_phase4_policy.py::test_scheduler_google_posts_includes_policy_hints` |
| AgentOS adapter forwarding | `C:/Users/User/Desktop/AgentOS/tests/test_kachu_adapter.py::test_retrieve_context_forwards_workflow_type_and_run_id` |
| Direction-check + policy context forwarding | `C:/Users/User/Desktop/AgentOS/tests/test_kachu_adapter.py::test_generate_drafts_receives_direction_check_and_policy_context` |
| Plan shape | `tests/test_photo_content_e2e.py` product path and `scripts/smoke_phase6.py` / production smoke |

## Google post

| Surface | Current test coverage |
|---|---|
| Workflow plan builder | `tests/test_phase2_workflows.py::test_google_post_pipeline_builds_plan` |
| Boss-triggered dispatch parity | `tests/test_phase6_contracts.py::test_google_post_dispatch_keeps_policy_hints_on_boss_request` |
| Scheduler policy parity | `tests/test_phase4_policy.py::test_scheduler_google_posts_includes_policy_hints` |
| Tool API path | `tests/test_phase2_workflows.py::test_generate_google_post_stub` and related Google post tests |
| Approval path | `tests/test_phase2_workflows.py::test_notify_approval_respects_rate_limit` |

## GA4 report

| Surface | Current test coverage |
|---|---|
| Workflow plan builder | `tests/test_phase2_workflows.py::test_ga4_report_pipeline_builds_plan` |
| GoalParser to workflow postback | `tests/test_phase5_features.py::test_build_quick_reply_items` |
| Shared context handoff | `tests/test_phase5_features.py` shared context tests |

## Knowledge update

| Surface | Current test coverage |
|---|---|
| Workflow plan builder | `tests/test_phase2_workflows.py::test_knowledge_update_pipeline_builds_plan` |
| Intent classification | `tests/test_photo_content_e2e.py::test_intent_router_classifies_text` |

## Cross-run runtime trace

| Surface | Current test coverage |
|---|---|
| Task/plan/run/approval/publish trace chain | `C:/Users/User/Desktop/AgentOS/tests/test_phase6_tracing.py::test_default_workflow_trace_chain_includes_phase6_events` |
| Approval lifecycle | `C:/Users/User/Desktop/AgentOS/tests/test_approval_lifecycle.py` |

## Rule for future changes

When a new step or payload field is added, add or update at least one test in each relevant layer:

1. Kachu tool or dispatch test
2. AgentOS adapter or workflow plan test
3. Smoke path if the change crosses service boundaries