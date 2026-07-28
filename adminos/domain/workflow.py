from enum import StrEnum


WORKFLOW_NAME = "gmail_evidence_to_monday_task"


class RunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MappingState(StrEnum):
    """The lifecycle of an Admin OS to external-system identity.

    `pending` is written before the external call and is the recovery marker:
    it means an item may exist externally that Admin OS has not yet confirmed.
    """

    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
