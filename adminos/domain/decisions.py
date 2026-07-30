from enum import StrEnum


HUMAN_ACTOR = "human"
SYSTEM_ACTOR = "system"
"""What signs a step nobody was asked to take.

Reserved for what needs no agreement, such as settling the plan of a review
with no work in it. A decision about mail is never signed with it.
"""

RULE_ACTOR_PREFIX = "rule:"
"""Prefix that marks an approval nobody was asked for.

An actor of `rule:<id>` is how an automatable rule signs its own approvals, so
the audit distinguishes what Brian decided from what a promoted rule decided
on his behalf.
"""


class ItemState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    FAILED = "failed"
    DISMISSED = "dismissed"
    DEFERRED = "deferred"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    OVERRIDE = "override"
    DISMISS = "dismiss"
    DEFER = "defer"
