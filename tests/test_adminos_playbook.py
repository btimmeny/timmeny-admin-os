"""The playbook is configuration with versions, and changing it takes two steps.

What these hold to: a playbook naming something that does not exist is refused
rather than quietly shortened, a change is written down before it is in force,
and a revision becomes the playbook only when somebody says so.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from adminos.capabilities.config import CapabilityConfig, LoadedCapabilities
from adminos.domain.playbook import (
    ChangeRefused,
    PlaybookChange,
    PlaybookDocument,
    ValidationCode,
    apply_changes,
    parse_playbook,
    read_change,
    validate_playbook,
)
from adminos.domain.playbook_store import (
    RevisionRefused,
    RevisionStatus,
    confirm_revision,
    propose_change,
    read_active_playbook,
    read_revisions,
)
from tests.conftest import build_capability


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_PLAYBOOK = REPOSITORY_ROOT / "config/assistant-playbook.yaml"
TEST_PLAYBOOK = REPOSITORY_ROOT / "tests/data/playbook_pair.yaml"
NOW = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)


def load(*keys: str) -> LoadedCapabilities:
    capabilities: tuple[CapabilityConfig, ...] = tuple(
        build_capability(key=key, position=position * 10)
        for position, key in enumerate(keys or ("admin", "financial_taxes"), start=1)
    )
    return LoadedCapabilities(
        version="test.1",
        digest="d" * 64,
        channel="email",
        capabilities=capabilities,
    )


def playbook() -> PlaybookDocument:
    return parse_playbook(TEST_PLAYBOOK.read_bytes())


def changes(*asked: dict[str, object]) -> list[PlaybookChange]:
    return [read_change(change) for change in asked]


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    url = f"sqlite:///{tmp_path / 'playbook.db'}"
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    monkeypatch.setenv("ASSISTANT_PLAYBOOK_PATH", str(TEST_PLAYBOOK))

    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    with factory() as open_session:
        yield open_session


def test_the_shipped_playbook_validates_against_the_shipped_capabilities() -> None:
    """The file this service starts from must be one it can run."""
    from adminos.capabilities.config import load_capabilities

    document = parse_playbook(SHIPPED_PLAYBOOK.read_bytes())
    report = validate_playbook(document, load_capabilities(REPOSITORY_ROOT / "config/capabilities.yaml"))

    assert report.valid, [error.message for error in report.errors]


def test_a_step_naming_a_capability_nobody_configured_is_an_error() -> None:
    """The whole reason to validate: a missing group is a silent omission."""
    document = playbook().model_copy(deep=True)
    document.activities[0].steps[0] = document.activities[0].steps[0].model_copy(
        update={"capability_key": "legal"}
    )

    report = validate_playbook(document, load())

    assert not report.valid
    error = report.errors[0]
    assert error.code == ValidationCode.UNKNOWN_CAPABILITY
    assert "legal" in error.message
    assert error.path == "activities.email_review.steps[0].capability_key"


def test_an_activity_nobody_implements_is_an_error_naming_the_key() -> None:
    document = playbook().model_copy(deep=True)
    document.activities[1] = document.activities[1].model_copy(
        update={"activity_key": "reading_the_paper"}
    )

    report = validate_playbook(document, load())

    assert not report.valid
    assert report.errors[0].code == ValidationCode.UNKNOWN_ACTIVITY
    assert "reading_the_paper" in report.errors[0].message


def test_an_activity_that_is_known_and_not_built_is_a_warning_not_an_error() -> None:
    """Objectives is real work Brian does. Admin OS just cannot do it yet."""
    report = validate_playbook(playbook(), load())

    assert report.valid
    assert [warning.code for warning in report.warnings] == [
        ValidationCode.ACTIVITY_NOT_BUILT
    ]
    assert "not built yet" in report.warnings[0].message


def test_two_activities_at_the_same_position_have_no_order() -> None:
    document = playbook().model_copy(deep=True)
    document.activities[1] = document.activities[1].model_copy(update={"order": 10})

    report = validate_playbook(document, load())

    assert [error.code for error in report.errors] == [ValidationCode.AMBIGUOUS_ORDER]


def test_an_enabled_activity_with_every_step_off_has_nothing_to_do() -> None:
    document = playbook().model_copy(deep=True)
    document.activities[0] = document.activities[0].model_copy(
        update={
            "steps": [step.model_copy(update={"enabled": False}) for step in document.activities[0].steps]
        }
    )

    report = validate_playbook(document, load())

    assert [error.code for error in report.errors] == [ValidationCode.NO_ENABLED_STEPS]


def test_moving_an_activity_reorders_it_and_says_so() -> None:
    changed = apply_changes(
        playbook(),
        changes(
            {
                "operation": "move_activity",
                "activity_key": "objectives_review",
                "before_activity_key": "email_review",
            }
        ),
        load(),
    )

    assert [activity.activity_key for activity in changed.document.enabled()] == [
        "objectives_review",
        "email_review",
        "session_closeout",
    ]
    assert changed.summary == ("Objectives moves before Email.",)


def test_enabling_an_activity_the_playbook_never_had_brings_it_in() -> None:
    """"Add a calendar review" and "turn it back on" are the same sentence."""
    changed = apply_changes(
        playbook(),
        changes({"operation": "enable_activity", "activity_key": "calendar_review"}),
        load(),
    )

    added = changed.document.find("calendar_review")
    assert added is not None and added.enabled
    assert [step.capability_key for step in added.steps][:2] == [
        "todays_calendar",
        "calendar_conflicts",
    ]
    assert changed.summary == ("Calendar is added to the session.",)


def test_a_change_naming_something_the_playbook_does_not_have_is_refused() -> None:
    with pytest.raises(ChangeRefused) as refusal:
        apply_changes(
            playbook(),
            changes(
                {
                    "operation": "disable_step",
                    "activity_key": "email_review",
                    "capability_key": "legal",
                }
            ),
            load(),
        )

    assert "legal" in str(refusal.value)


def test_the_first_read_seeds_the_playbook_from_the_file(session: Session) -> None:
    active = read_active_playbook(session, load(), now=NOW)

    assert active.revision.number == 1
    assert active.revision.status == RevisionStatus.ACTIVE
    assert active.document.name == "Test session playbook"
    assert active.report.valid


def test_a_proposal_changes_nothing_until_it_is_confirmed(session: Session) -> None:
    """The line the whole design rests on: saying it is not making it so."""
    before = read_active_playbook(session, load(), now=NOW)

    proposal = propose_change(
        session,
        load(),
        changes(
            {
                "operation": "move_activity",
                "activity_key": "objectives_review",
                "before_activity_key": "email_review",
            }
        ),
        rationale="Objectives set the day up.",
        now=NOW,
    )
    still = read_active_playbook(session, load(), now=NOW)

    assert proposal.revision.status == RevisionStatus.PROPOSED
    assert proposal.revision.based_on_revision_id == before.revision.id
    assert still.revision.id == before.revision.id
    assert [activity.activity_key for activity in still.document.enabled()][0] == "email_review"


def test_confirming_a_proposal_makes_it_the_playbook_and_keeps_the_old_one(
    session: Session,
) -> None:
    first = read_active_playbook(session, load(), now=NOW)
    proposal = propose_change(
        session,
        load(),
        changes({"operation": "disable_activity", "activity_key": "objectives_review"}),
        now=NOW,
    )

    active = confirm_revision(session, load(), proposal.revision.id, now=NOW)
    revisions = read_revisions(session)

    assert active.revision.id == proposal.revision.id
    assert active.revision.status == RevisionStatus.ACTIVE
    assert active.revision.activated_at == NOW
    assert [revision.status for revision in revisions] == [
        RevisionStatus.ACTIVE,
        RevisionStatus.SUPERSEDED,
    ]
    assert revisions[-1].id == first.revision.id
    assert "objectives_review" not in [
        activity.activity_key for activity in active.document.enabled()
    ]


def test_a_proposal_the_playbook_has_moved_on_from_is_refused(session: Session) -> None:
    """What is confirmed has to be what was read back, and it no longer is."""
    read_active_playbook(session, load(), now=NOW)
    stale = propose_change(
        session,
        load(),
        changes({"operation": "disable_activity", "activity_key": "objectives_review"}),
        now=NOW,
    )
    meanwhile = propose_change(
        session,
        load(),
        changes(
            {
                "operation": "move_activity",
                "activity_key": "objectives_review",
                "before_activity_key": "email_review",
            }
        ),
        now=NOW,
    )
    confirm_revision(session, load(), meanwhile.revision.id, now=NOW)

    with pytest.raises(RevisionRefused) as refusal:
        confirm_revision(session, load(), stale.revision.id, now=NOW)

    assert "Propose the change again" in str(refusal.value)
    assert read_active_playbook(session, load(), now=NOW).revision.id == meanwhile.revision.id


def test_a_proposal_that_would_not_run_is_written_down_and_cannot_be_confirmed(
    session: Session,
) -> None:
    """Refusing to record it would leave Brian told no with nothing to look at."""
    read_active_playbook(session, load(), now=NOW)
    proposal = propose_change(
        session,
        load(),
        changes(
            {
                "operation": "disable_step",
                "activity_key": "email_review",
                "capability_key": "admin",
            },
            {
                "operation": "disable_step",
                "activity_key": "email_review",
                "capability_key": "financial_taxes",
            },
        ),
        now=NOW,
    )

    assert not proposal.report.valid
    with pytest.raises(RevisionRefused):
        confirm_revision(session, load(), proposal.revision.id, now=NOW)
    assert read_active_playbook(session, load(), now=NOW).revision.number == 1


def test_a_playbook_whose_capability_disappeared_falls_back_to_one_that_works(
    session: Session,
) -> None:
    """A capability removed underneath an active revision stops it being runnable."""
    read_active_playbook(session, load(), now=NOW)
    proposal = propose_change(
        session,
        load(),
        changes(
            {
                "operation": "add_step",
                "activity_key": "email_review",
                "capability_key": "career_advisor_calls",
                "label": "Career",
            }
        ),
        now=NOW,
    )
    confirm_revision(
        session, load("admin", "financial_taxes", "career_advisor_calls"), proposal.revision.id, now=NOW
    )

    active = read_active_playbook(session, load("admin", "financial_taxes"), now=NOW)

    assert active.fell_back_from is not None
    assert active.fell_back_from.id == proposal.revision.id
    assert active.fell_back_from.status == RevisionStatus.INVALID
    assert active.revision.number == 1
    assert active.report.valid
