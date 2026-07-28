import sys
from pathlib import Path
from typing import Sequence


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from adminos.capabilities.config import CapabilityConfig  # noqa: E402


DEFAULT_ACTIONS = ["gmail.label", "gmail.archive", "gmail.draft_reply", "monday.create_task"]
DEFAULT_STEPS = [
    "collect_evidence",
    "recommend",
    "await_decision",
    "prepare_actions",
    "execute_approved",
    "verify",
]


def build_capability(
    key: str = "financial_taxes",
    labels: Sequence[str] = ("financial/taxes",),
    **overrides: object,
) -> CapabilityConfig:
    """A valid capability, so a test states only what it is about."""
    definition: dict[str, object] = {
        "key": key,
        "name": key,
        "position": 10,
        "gmail": {"labels": list(labels)},
        "playbook": {"id": "test", "steps": list(DEFAULT_STEPS)},
        "recommendation_policy": {
            "version": f"{key}.test",
            "categories": ["obligation", "other"],
        },
        "allowed_actions": list(DEFAULT_ACTIONS),
    }
    definition.update(overrides)
    return CapabilityConfig.model_validate(definition)
