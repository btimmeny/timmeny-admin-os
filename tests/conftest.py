import sys
from pathlib import Path
from typing import Sequence


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from adminos.capabilities.config import CapabilityConfig  # noqa: E402
from adminos.capabilities.screens import ScreenConfig  # noqa: E402


DEFAULT_SCREEN_ID = "test-review-v1"
DEFAULT_ACTIONS = ["gmail.label", "gmail.archive", "gmail.draft_reply", "monday.create_task"]
DEFAULT_EXECUTION = ["gmail.label", "gmail.archive", "gmail.draft_reply"]
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
        "presentation": {"screen": DEFAULT_SCREEN_ID},
        "playbook": {"id": "test", "steps": list(DEFAULT_STEPS)},
        "recommendation_policy": {
            "version": f"{key}.test",
            "categories": ["obligation", "other"],
        },
        "allowed_actions": list(DEFAULT_ACTIONS),
        "execution": {"permitted_actions": list(DEFAULT_EXECUTION)},
    }
    definition.update(overrides)
    return CapabilityConfig.model_validate(definition)


DEFAULT_COLUMNS = [
    {"label": "#", "source": "index", "align": "right"},
    {"label": "Group", "source": "group"},
    {"label": "What it is", "source": "what_it_is"},
    {"label": "Key Facts", "source": "key_facts"},
    {"label": "Recommended Action", "source": "recommended_action"},
    {"label": "Why", "source": "why"},
    {"label": "Confidence", "source": "confidence", "format": "percent", "align": "right"},
    {"label": "Decision", "source": "decision"},
]

DEFAULT_SCREEN_ACTIONS = [
    {"id": "approve", "label": "Do what is recommended", "decision": "approve"},
    {"id": "archive", "label": "Archive it", "decision": "override", "action": "gmail.archive"},
    {"id": "dismiss", "label": "Leave it alone", "decision": "dismiss"},
    {"id": "defer", "label": "Not today", "decision": "defer"},
]


def build_screen(screen_id: str = DEFAULT_SCREEN_ID, **overrides: object) -> ScreenConfig:
    """A valid presentation contract with the shipped column set."""
    definition: dict[str, object] = {
        "id": screen_id,
        "title": "Test review",
        "columns": [dict(column) for column in DEFAULT_COLUMNS],
        "sort": [{"source": "received", "direction": "desc"}],
        "actions": [dict(action) for action in DEFAULT_SCREEN_ACTIONS],
    }
    definition.update(overrides)
    return ScreenConfig.model_validate(definition)
