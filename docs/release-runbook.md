# Phase 6 Release Runbook

## Goal

Ship Kachu without relying on ad hoc manual verification.

## Required sequence

1. Sync the local Kachu and AgentOS source trees to the remote host.
2. Run the release gate.
3. Build images.
4. Apply schema migration if applicable.
5. Start or restart services.
6. Run smoke validation.
7. Only then treat the release as done.

## Single entry point

From the Kachu workspace root:

```bash
python scripts/release_check.py
```

For production rollout with explicit stages:

```bash
python scripts/deploy_phase6_prod.py --host root@your-server
```

The deploy helper now syncs the local Kachu and AgentOS worktrees to the remote host before build.
Use `--skip-sync` only when you have already updated the remote source tree by some other controlled path.

## What the release gate covers

- Kachu automated tests
- AgentOS automated tests
- in-process smoke test with temporary tenant seed + cleanup

## Smoke contract

The smoke flow must verify:
- `retrieve-context` returns preference, episode, and shared context hints
- `check-draft-direction` returns a usable direction brief
- `generate-drafts` accepts policy context and direction context
- `GoalParser` quick reply includes a valid `workflow=` postback
- Kachu photo workflow plan includes `check-draft-direction` when requested

## Production rule

`healthy` means the container is alive.
It does not prove that Kachu workflows are usable.

For production, run smoke in one of these safe ways:
- inside the Kachu container
- against a temporary tenant with cleanup
- without real LINE push or irreversible publish side effects

The provided Phase 6 deploy helper follows this model by running remote smoke inside Kachu and AgentOS containers.

## Failure handling

If the release gate fails:
- stop the rollout
- fix the failing slice first
- rerun the same failing check
- rerun the full release gate before deploy proceeds