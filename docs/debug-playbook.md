# Kachu Phase 6 Debug Playbook

## First principle

Debug by `task_id` or `run_id` whenever possible.
Do not start from broad log scanning if an execution identifier is already available.

## Event chain to expect

On the AgentOS side, a healthy run should usually show this trace sequence:

- `task.created`
- `plan.built`
- `run.started`
- `step.started`
- `tool.started`
- `tool.completed`
- `approval.requested` / `approval.pending` when gated
- `approval.decided` when resumed
- `publish.attempted` / `publish.succeeded` for publish steps
- `run.completed` or `run.failed`

## Symptom → first check

### Boss says nothing arrived in LINE

1. Find the Kachu workflow record by `agentos_run_id` or recent task.
2. Check AgentOS run status.
3. If waiting approval, inspect the corresponding Kachu pending approval record.
4. Check Kachu push suppression conditions: rate limit, quiet hours, token config.

### Run exists but no useful output was produced

1. Inspect AgentOS evidence for the run.
2. Confirm whether `retrieve-context`, `check-draft-direction`, and `generate-drafts` each wrote evidence.
3. If evidence is missing, inspect the corresponding adapter step payload.

### Boss approved but run did not continue

1. Confirm Kachu ApprovalBridge received the LINE postback.
2. Check AgentOS pending approval for the run.
3. Verify `approval.decided` and `run.resumed` appear in traces.

### Scheduler path behaves differently from boss-triggered path

1. Compare workflow payloads for scheduler and interactive entry.
2. Check whether policy hints were injected on both paths.
3. Re-run the parity tests before changing business logic.

## Minimal investigation order

1. AgentOS run trace
2. AgentOS evidence chain
3. Kachu workflow record / pending approval record
4. Kachu application logs
5. External platform credential or API layer