"""
Phase 4: KachuExecutionPolicyResolver

Reads a tenant's TenantApprovalProfile and returns PolicyHints that are
injected into workflow_input when creating AgentOS tasks. The plan builders
in AgentOS kachu_workflows already read `approval_timeout_seconds` and
`require_direction_check` from workflow_input, so no AgentOS core change
is needed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from .persistence import KachuRepository

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

_HIGH_TRUST_RATE = 0.85    # acceptance rate above this → shorten timeout
_HIGH_TRUST_DELTA = 0.10   # edit magnitude below this → shorten timeout
_LOW_TRUST_RATE = 0.50     # acceptance rate below this → add direction check

_DEFAULT_TIMEOUT = 86400           # 24 h  (base)
_HIGH_TRUST_TIMEOUT = 21600        # 6 h
_LOW_TRUST_EXTRA_HINTS = (
    "【注意：老闆最近多次拒絕草稿，請特別貼近品牌風格，避免制式語氣。】"
)


@dataclass
class PolicyHints:
    """Hints passed via workflow_input to plan builders."""
    approval_timeout_seconds: int = _DEFAULT_TIMEOUT
    # When True, generate-drafts receives a conservative tone warning
    require_direction_check: bool = False
    # Raw extra context string injected into generation prompts
    generation_context: str = ""
    # Summary for logging / trace
    source: str = "default"

    def to_workflow_input_patch(self) -> dict:
        """Return a dict suitable for merging into AgentOS workflow_input."""
        return {
            "approval_timeout_seconds": self.approval_timeout_seconds,
            "require_direction_check": self.require_direction_check,
            "policy_generation_context": self.generation_context,
        }


class KachuExecutionPolicyResolver:
    """
    Reads TenantApprovalProfile and returns PolicyHints.

    Usage (in intent_router or scheduler before task creation):

        resolver = KachuExecutionPolicyResolver(repo)
        hints = resolver.resolve(tenant_id)
        workflow_input.update(hints.to_workflow_input_patch())
    """

    def __init__(self, repo: "KachuRepository") -> None:
        self._repo = repo

    def resolve(self, tenant_id: str) -> PolicyHints:
        """Return policy hints for the tenant based on historical behaviour."""
        try:
            profile = self._repo.get_approval_profile(tenant_id)
        except SQLAlchemyError as exc:
            logger.warning("Could not load approval profile for %s: %s", tenant_id, exc)
            return PolicyHints(source="error_fallback")

        if profile is None or profile.total_decisions < 3:
            # Not enough data — use defaults
            return PolicyHints(source="insufficient_data")

        rate = profile.recent_acceptance_rate
        delta = profile.median_edit_delta

        if rate >= _HIGH_TRUST_RATE and delta < _HIGH_TRUST_DELTA:
            logger.info(
                "PolicyResolver: HIGH-TRUST tenant=%s rate=%.2f delta=%.2f → timeout=%ds",
                tenant_id, rate, delta, _HIGH_TRUST_TIMEOUT,
            )
            return PolicyHints(
                approval_timeout_seconds=_HIGH_TRUST_TIMEOUT,
                source="high_trust",
            )

        if rate < _LOW_TRUST_RATE:
            logger.info(
                "PolicyResolver: LOW-TRUST tenant=%s rate=%.2f → direction_check=True",
                tenant_id, rate,
            )
            return PolicyHints(
                require_direction_check=True,
                generation_context=_LOW_TRUST_EXTRA_HINTS,
                source="low_trust",
            )

        return PolicyHints(source="normal")
