# Kachu / AgentOS Boundary Contract

This document defines the boundary that Phase 6 is trying to protect.

## Product vs runtime

Kachu owns:
- product workflows and boss-facing behavior
- tenant-specific policy resolution
- memory, shared context, onboarding, and business knowledge
- external platform adapters such as LINE, Google Business, GA4, Meta
- boss approval experience and approval bridge behavior

AgentOS owns:
- task, run, step lifecycle
- approval gating and approval decision state
- retry, replay, timeout, checkpoint, idempotency
- evidence persistence and trace recording
- product adapter execution contract

## Change rule for cross-boundary fields

When a new field is added to a Kachu workflow payload, verify all of these layers before merge:

1. Kachu request model
2. AgentOS workflow plan builder step input
3. AgentOS adapter payload forwarding
4. Kachu tool router handler
5. Contract or parity tests

If one layer is missing, the feature is not complete.

## High-risk payload paths

### `kachu_photo_content`

- `workflow_input.approval_timeout_seconds`
- `workflow_input.require_direction_check`
- `workflow_input.policy_generation_context`
- `context.direction_check`
- `retrieve-context.workflow_type`
- `retrieve-context.run_id`

### `kachu_google_post`

- `workflow_input.topic`
- `workflow_input.trigger_source`
- `workflow_input.approval_timeout_seconds`
- `workflow_input.require_direction_check`
- `workflow_input.policy_generation_context`

### `notify-approval`

- `tenant_id`
- `run_id`
- `workflow`
- `drafts`

## Boundary anti-patterns

Do not:
- add product-specific branching to AgentOS core runtime
- make AgentOS decide which Kachu business policy to use
- hide Kachu payload mapping inside generic runtime models
- treat container health as a substitute for workflow smoke validation

## Acceptance bar

A Phase 6 boundary change is acceptable only when:
- the product responsibility remains in Kachu
- the runtime responsibility remains in AgentOS
- the new field is covered by at least one contract/parity test