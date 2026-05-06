# Kachu Multi-Tenant Productization Roadmap

## Goal

Turn Kachu from a single-brand deployment with tenant-shaped data into a product-grade multi-tenant platform where multiple brands can use the same service safely.

## Status Snapshot

Current assessment as of 2026-05-03:

- Alpha multi-tenant core is implemented.
- The primary LINE control surface is tenant-safe.
- The last full automated regression run passed end-to-end (`265 passed`), and the newly added admin or tooling slice now has focused regression coverage (`25 passed`).
- The roadmap is no longer purely forward-looking: Phases 1-4 are largely complete, Phase 5 is largely complete, and Phase 6 is materially advanced but not fully complete.

Practical reading:

- "Multi-tenant alpha-ready": yes, subject to normal staged rollout caution.
- "Fully product-grade multi-tenant SaaS": not yet.

Target product shape:

- One shared Kachu backend deployment.
- One shared Kachu LINE OA for alpha.
- Multiple tenants, each with isolated knowledge, workflows, approvals, schedules, and channel credentials.
- Each tenant can bind its own Meta, Google Business Profile, and GA4 connectors.
- Each boss interacts with Kachu through LINE, and every inbound or outbound action resolves to the correct tenant before any workflow is executed.

## Current State Summary

The codebase already has meaningful tenant groundwork:

- Most persistence models are tenant-scoped in `src/kachu/persistence/tables.py`.
- Repository methods support tenant isolation and active-tenant iteration in `src/kachu/persistence/repository.py`.
- Scheduler jobs iterate through active tenants rather than a hard-coded tenant in `src/kachu/scheduler.py`.
- Google review webhook can map inbound events to tenants by Google location in `src/kachu/google/webhook.py` and `src/kachu/persistence/repository.py`.
- Google and Meta OAuth flows already carry `tenant_id` in OAuth state and save connector credentials per tenant in `src/kachu/auth/oauth.py`.

The initial productization blocker was the LINE interaction path, which used to assume a single global boss. That runtime gap is now largely closed:

- LINE webhook routing now resolves `tenant_id` from tenant memberships in `src/kachu/line/webhook.py`.
- Internal notification and report endpoints now resolve tenant recipients instead of pushing to a global boss ID.
- Dashboard APIs now require explicit `tenant_id` rather than silently falling back to a boss setting.
- `LINE_BOSS_USER_ID` remains only as a legacy/local-dev fallback setting for compatibility.

Conclusion: the platform is now tenant-safe on the primary LINE control surface for alpha scope. Remaining work is mainly around role modeling, admin UX, and reducing historical compatibility surfaces.

Additional validated status:

- `TenantMembershipTable`, membership repository APIs, and backfill migration are implemented.
- LINE webhook tenant resolution, unbound-user rejection, and postback tenant mismatch rejection are implemented.
- Tenant-scoped LINE recipients are implemented for approval prompts, GA4 reports, proactive nudges, comment notifications, and related push paths.
- Tenant-scoped OAuth completion notifications now also resolve through tenant recipients instead of using raw tenant IDs.
- Membership role is now active in runtime control: manager can receive notifications, but owner-only approval, schedule, retry, cancel, and replay postbacks are enforced in the LINE entry layer.
- Dashboard APIs now require explicit tenant context.
- Dashboard UI now includes tenant-aware connector reconnect or disconnect actions and SaaS operation controls for export, deactivation, and deletion.
- Dashboard APIs and UI now also expose forced Google connector refresh, tenant plan or feature-flag controls, and tenant health visibility.
- Tenant runtime gating now centralizes plan enforcement and feature-flag evaluation in `src/kachu/tenant_runtime.py`.
- The last full automated regression suite passed (`265 passed`), and the new dashboard or runtime slice passes focused regression (`25 passed`).

## Product Decisions

### Alpha Scope

Adopt the simplest product shape that can be sold and operated:

- One shared Kachu LINE OA.
- One tenant equals one brand.
- One tenant initially has one owner and optional additional managers.
- One LINE user initially belongs to one tenant.
- Each tenant supports one active Meta connector, one active Google Business connector, and one active GA4 connector.

### Deferred Scope

Defer these until after alpha:

- One user managing multiple tenants.
- Separate customer-facing and boss-facing LINE channels.
- Complex RBAC beyond owner and manager.
- Per-tenant branded frontend domains.

## Recommended Architecture

### Identity and Membership

Introduce an explicit tenant membership layer instead of deriving tenant identity from environment variables.

Recommended minimum schema for alpha:

1. `kachu_tenant_memberships`
   - `id`
   - `tenant_id`
   - `line_user_id`
   - `role` (`owner` or `manager`)
   - `display_name`
   - `is_active`
   - `created_at`
   - `updated_at`

Optional product-grade follow-up schema:

1. `kachu_users`
   - canonical application user row
2. `kachu_user_identities`
   - provider identities like LINE user ID
3. `kachu_tenant_memberships`
   - many-to-many membership model

For alpha, the minimum table is enough. It avoids blocking productization on a larger auth redesign.

### Channel Routing

Every inbound message must resolve tenant context before any business logic runs.

Required routing rule:

1. Extract `line_user_id` from the webhook payload.
2. Query active membership by `line_user_id`.
3. Resolve `tenant_id`, `role`, and tenant-scoped notification targets.
4. Reject or soft-fail if no membership exists.
5. Only then continue into onboarding, approvals, publish, consultation, or scheduling logic.

### Outbound Notifications

Any push message must be tenant-scoped, not globally addressed.

Required outbound rule:

1. Resolve the target tenant.
2. Resolve the correct active recipient list for that tenant.
3. Push to those recipients only.

## Delivery Plan

### Phase 1: Tenant Membership Foundation [Completed]

Objective: establish a real source of truth for who controls each tenant.

Tasks:

1. Add `TenantMembershipTable` to `src/kachu/persistence/tables.py`.
2. Add repository methods in `src/kachu/persistence/repository.py`:
   - `create_tenant_membership(...)`
   - `get_active_membership_by_line_user_id(...)`
   - `list_active_memberships(tenant_id)`
   - `get_owner_line_user_ids(tenant_id)`
3. Keep `TenantTable.line_user_id` temporarily for backfill compatibility only.
4. Add migration to create the new table.
5. Add a one-time backfill migration to populate memberships from existing `TenantTable.line_user_id` values.

Exit criteria:

- There is a durable DB mapping between LINE user IDs and tenants.
- No new code depends on `TenantTable.line_user_id` as the primary runtime source.

Implementation status:

- Completed in `src/kachu/persistence/tables.py`, `src/kachu/persistence/repository.py`, and the corresponding Alembic migration.
- Backfill compatibility remains intentionally preserved through `TenantTable.line_user_id`, but it is no longer the primary runtime source.

### Phase 2: Refactor LINE Webhook Resolution [Completed]

Objective: remove the single-boss assumption from the primary interaction path.

Tasks:

1. Update `src/kachu/line/webhook.py` so tenant resolution comes from membership lookup, not `LINE_BOSS_USER_ID`.
2. Replace current logic that sets `tenant_id = settings.LINE_BOSS_USER_ID ...` with a repository lookup.
3. Split the current concept of `is_boss` into:
   - `has_active_membership`
   - `role`
4. Add clear fallback behavior for unknown LINE users:
   - optional invite/join message
   - or safe rejection message
5. Update postback flows so `tenant_id` is validated against the acting user membership.

Exit criteria:

- Two different boss LINE accounts can send commands through the same LINE OA without crossing tenant data.
- Tenant resolution no longer depends on environment-level boss identity.

Implementation status:

- Completed in `src/kachu/line/webhook.py` with membership-first resolution.
- Unknown LINE users now receive a safe rejection.
- Membership-bound postbacks are validated against the acting tenant.
- Owner-only approval, schedule-control, and runtime-control postbacks are now enforced for membership-bound actors.

### Phase 3: Refactor Outbound Recipient Resolution [Completed]

Objective: stop sending tenant messages to a global boss recipient.

Tasks:

1. Audit all uses of `settings.LINE_BOSS_USER_ID` in:
   - `src/kachu/tools/router.py`
   - `src/kachu/scheduler.py`
   - `src/kachu/proactive_monitor.py`
   - `src/kachu/intent_router.py`
   - any approval and escalation helpers
2. Introduce a helper such as `list_tenant_notification_recipients(tenant_id)`.
3. Replace global pushes with tenant-scoped recipient lists.
4. Ensure scheduler-triggered tasks send reports only to the tenant's configured owners or managers.

Exit criteria:

- Scheduled reports, approval prompts, escalation notices, and publish results are sent only to recipients belonging to the same tenant.

Implementation status:

- Completed across `src/kachu/line/push.py`, `src/kachu/tools/router.py`, `src/kachu/scheduler.py`, `src/kachu/proactive_monitor.py`, and `src/kachu/intent_router.py`.
- Tenant notifications now include both owners and managers through the notification recipient helper.
- OAuth completion LINE pushes also use tenant recipient resolution.
- Real runtime paths no longer use `LINE_BOSS_USER_ID` when concrete membership or owner lookup exists.
- Legacy fallback is kept only for compatibility surfaces such as local dev, partial stubs, or explicit legacy tests.

### Phase 4: Remove Single-Tenant Fallbacks from Tools and Dashboard [Largely Completed]

Objective: ensure all internal entry points require explicit tenant context.

Tasks:

1. Remove fallback-to-boss logic from `src/kachu/dashboard/router.py`.
2. Audit internal endpoints in `src/kachu/tools/router.py` that assume `LINE_BOSS_USER_ID` or `DEFAULT_TENANT_ID`.
3. Convert internal flows to either:
   - require explicit `tenant_id`, or
   - resolve tenant from authenticated actor context.
4. Keep `DEFAULT_TENANT_ID` only for local dev or emergency ops, not product runtime.

Exit criteria:

- Internal operations do not silently collapse to a global tenant.

Implementation status:

- Dashboard fallback-to-boss behavior has been removed.
- Internal dashboard APIs now require explicit `tenant_id`.
- Historical compatibility settings such as `LINE_BOSS_USER_ID` and `DEFAULT_TENANT_ID` still exist, but they are no longer the intended product runtime path.

Outstanding gap:

- Full removal of historical compatibility surfaces is still deferred to a later cleanup release.

### Phase 5: Connector and Webhook Hardening [Largely Completed]

Objective: make channel integrations fully tenant-safe and operable.

Tasks:

1. Standardize connector metadata stored in `ConnectorAccountTable`:
   - Meta page ID
   - Meta page name
   - IG user ID
   - Google location ID
   - Google location name
2. Keep using `tenant_id` in OAuth state for connect flows.
3. Add tenant-scoped reconnect and disconnect operations.
4. Add audit events for connect, disconnect, refresh, and failure states.
5. Ensure inbound Google webhooks always resolve tenants from stored connector metadata before using any fallback.

Exit criteria:

- Connector state can be reasoned about per tenant.
- Operational failures are localized to a single tenant.

Implementation status:

- Tenant-aware OAuth state handling is in place.
- Google webhook tenant resolution by connector metadata is in place.
- Dashboard admin APIs now expose connector disconnect operations.
- Dashboard UI now exposes tenant-aware reconnect and disconnect actions for Google and Meta connectors.
- Google Business and GA4 token refresh now flow through a shared runtime helper.
- Dashboard admin APIs and UI now expose forced connector refresh and refresh-failure state for Google connectors.
- Audit coverage now includes connector refresh success and connector refresh failure events.

Outstanding gaps:

- Multi-tenant connector isolation is implemented in core runtime, but operational tooling can still be deepened around lifecycle audit breadth and recovery paths.

### Phase 6: Product Operations Layer [Partially Completed]

Objective: make the service supportable as a real SaaS.

Tasks:

1. Add tenant-scoped audit events for critical actions.
2. Add tenant-scoped feature flags and plan enforcement.
3. Add tenant-scoped budget limits for LLM and automation usage.
4. Add tenant-scoped health visibility in the admin dashboard.
5. Add tenant-scoped deactivation flow.
6. Add tenant-scoped data export and deletion playbooks.

Exit criteria:

- One tenant can fail, pause, or churn without affecting others.

Implementation status:

- Some tenant-scoped audit and budget foundations exist.
- Dashboard admin APIs now expose tenant-scoped deactivation, export, and deletion operations.
- Dashboard UI now provides a browser-operable control surface for export, deactivation, and deletion flows.
- Tenant-scoped feature flags are now persisted and editable through dashboard admin APIs and UI.
- Tenant plan and expiry are now enforced in tool runtime for GA4, Meta, and cross-channel capability gates.
- Tenant health snapshots are now available through dashboard admin APIs and UI.
- The remaining gap is no longer baseline SaaS control surfaces, but deeper budget enforcement and post-alpha cleanup of compatibility paths.

## Migration Plan

Migration status summary:

- Membership table and backfill migration are implemented.
- Runtime has already switched to membership-first reads.
- Cleanup migration work is intentionally incomplete because compatibility fields are still retained for controlled transition.

### Migration 1: Membership Table

Create `kachu_tenant_memberships` with indexes:

- unique active owner membership constraint can be deferred
- index on `line_user_id`
- index on `tenant_id`
- index on `(tenant_id, is_active)`

### Migration 2: Backfill Existing Boss Bindings

Backfill from existing tenants:

1. For every tenant where `TenantTable.line_user_id` is non-empty:
   - insert an active membership row with role `owner`
2. Log rows skipped because of empty or duplicate line user IDs.

### Migration 3: Runtime Switch

After deployment:

1. deploy code that reads memberships first
2. keep `TenantTable.line_user_id` as fallback for one release only
3. run smoke tests
4. remove fallback in the next release

### Migration 4: Cleanup

After stable rollout:

1. stop writing to `TenantTable.line_user_id`
2. optionally drop or deprecate the field in a later migration

## Concrete File-Level Task List

Current status summary:

- Persistence layer tasks: completed for alpha scope.
- LINE entry layer tasks: completed for alpha scope.
- Notification and scheduler layer tasks: completed for alpha scope.
- Dashboard and internal tools: largely completed for alpha scope, including product operations UI.
- Connector and OAuth layer: largely completed.

### Persistence Layer

Files:

- `src/kachu/persistence/tables.py`
- `src/kachu/persistence/repository.py`
- `alembic/versions/...`

Tasks:

1. Add membership table model. [Done]
2. Add repository CRUD and lookup helpers. [Done]
3. Add migration and backfill migration. [Done]
4. Add repository tests for membership resolution. [Done]

### LINE Entry Layer

Files:

- `src/kachu/line/webhook.py`
- `tests/test_line_webhook_resilience.py`
- new test file if needed for membership resolution

Tasks:

1. Replace global boss resolution. [Done]
2. Validate postback actor and tenant alignment. [Done]
3. Support unknown-user behavior. [Done]
4. Add regression tests for two bosses using the same OA. [Partially done via tenant-routing regression coverage; explicit named two-boss scenario can still be expanded]

### Notification and Scheduler Layer

Files:

- `src/kachu/scheduler.py`
- `src/kachu/tools/router.py`
- `src/kachu/proactive_monitor.py`
- `src/kachu/intent_router.py`

Tasks:

1. Replace all pushes to `LINE_BOSS_USER_ID` with tenant recipient resolution. [Done for primary runtime paths]
2. Ensure approval reminders and reports are tenant-scoped. [Done for alpha scope]
3. Add tests covering two tenants receiving separate notifications. [Mostly done through tenant recipient regression coverage]

### Dashboard and Internal Tools

Files:

- `src/kachu/dashboard/router.py`
- `src/kachu/static/dashboard.html`
- `src/kachu/tools/router.py`

Tasks:

1. Remove implicit boss fallback. [Done for dashboard APIs]
2. Require explicit tenant selection or authenticated tenant context. [Done for dashboard APIs; broader authenticated actor context can still improve]
3. Add tenant selector in internal ops UI if needed. [Done in dashboard UI via explicit tenant input]

### Connector and OAuth Layer

Files:

- `src/kachu/auth/oauth.py`
- `src/kachu/google/webhook.py`
- `src/kachu/google/business_client.py`
- `tests/test_google_oauth_discovery.py`

Tasks:

1. Keep tenant-aware OAuth state handling. [Done]
2. Add connector disconnect or rotate flows. [Disconnect done; rotate and token-recovery UX still pending]
3. Add tests for multiple tenants with distinct Google locations and Meta pages. [Partially done]

## Test Matrix

Current validation status:

- The automated suite currently passes in full (`265 passed`).
- Core multi-tenant routing and recipient isolation have regression coverage.
- Connector and operations scenarios now include dashboard disconnect, tenant deactivation, tenant export, and tenant deletion coverage, but still have less depth than the core LINE routing path.

### Core Multi-Tenant Routing

1. Boss A sends LINE message, workflow uses tenant A only.
2. Boss B sends LINE message, workflow uses tenant B only.
3. Unknown LINE user receives safe rejection or invite flow.
4. Postback forged with another tenant ID is rejected.

### Knowledge Isolation

1. Tenant A uploads documents, tenant B retrieval never sees them.
2. Tenant A consultation history never appears in tenant B diagnosis.
3. Tenant A brief refresh does not overwrite tenant B brief.

### Approval and Publish Isolation

1. Approval prompt from tenant A is only sent to tenant A recipients.
2. Tenant B cannot approve tenant A run by postback replay.
3. Scheduled publish for tenant A uses tenant A connectors only.

### Connector Isolation

1. Google webhook for location A triggers tenant A only.
2. Google webhook for location B triggers tenant B only.
3. Meta publish for tenant A never uses tenant B page ID.

### Operational Safety

1. Deactivating tenant A leaves tenant B unaffected.
2. Tenant A connector expiry does not block tenant B workflows.
3. Tenant A budget exhaustion does not block tenant B workflows.

## Rollout Plan

### Week 1

1. Add membership table and repository support.
2. Add migration and backfill.
3. Add unit tests for membership resolution.

### Week 2

1. Refactor LINE webhook tenant resolution.
2. Refactor notification recipient lookup.
3. Add two-tenant LINE workflow tests.

### Week 3

1. Remove dashboard and tools fallbacks.
2. Harden connector flows and webhook mapping.
3. Add two-tenant connector tests.

### Week 4

1. Add operational guardrails and audit coverage.
2. Run full contract and smoke validation.
3. Execute staged production rollout with a small tenant cohort.

## Alpha Acceptance Criteria

Kachu can be considered alpha-ready for multi-tenant product use only if all of the following are true:

1. Two independent brands can use the same Kachu deployment and the same LINE OA without any cross-tenant leakage.
2. Each brand can connect its own Meta and Google accounts independently.
3. Scheduled posts, approval flows, consultation, knowledge retrieval, and reports remain tenant-scoped.
4. Unknown or unauthorized LINE users cannot control a tenant by messaging the shared OA.
5. All tenant-routing behavior is covered by automated tests.

Current assessment:

- Criteria 1, 3, 4, and 5 are satisfied for alpha scope.
- Criterion 2 is satisfied at the runtime integration level and now has a practical product operations surface through dashboard admin UI, but the broader SaaS layer is still not complete enough to call the whole product surface "fully complete".
- Therefore Kachu should be considered multi-tenant alpha-ready, not fully productized.

## Recommendation

Do not restart broad architectural work. The highest-leverage next slice is now narrower:

1. deepen owner vs manager role enforcement beyond the current LINE postback control layer
2. deepen lifecycle audit breadth and tenant budget enforcement on top of the new plan or feature gate layer
3. add more automated multi-tenant contract coverage around connector failure isolation and admin controls
4. continue reducing historical compatibility surfaces after stable rollout

That sequence closes the remaining gap between an alpha-ready multi-tenant runtime and a more supportable product-grade SaaS layer.