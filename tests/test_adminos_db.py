from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from adminos.db import engine as engine_module
from adminos.db.engine import DatabaseNotConfigured, get_engine, session_scope
from adminos.db.models import Base, Evidence


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TABLES = {
    "operational_objects",
    "evidence",
    "classifications",
    "external_mappings",
    "workflow_runs",
    "workflow_steps",
    "decisions",
}


@pytest.fixture
def sqlite_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path / 'admin-os.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    engine_module.dispose_connection()
    yield url
    engine_module.dispose_connection()


def upgrade_to_head(url: str) -> None:
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def test_get_engine_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine_module.dispose_connection()

    with pytest.raises(DatabaseNotConfigured):
        get_engine()


def test_engine_is_rebuilt_when_the_url_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'one.db'}")
    engine_module.dispose_connection()
    first = get_engine()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'two.db'}")
    second = get_engine()

    assert first is not second
    engine_module.dispose_connection()


def test_migration_creates_every_table(sqlite_url: str) -> None:
    upgrade_to_head(sqlite_url)

    tables = set(inspect(get_engine()).get_table_names())

    assert EXPECTED_TABLES <= tables


def test_migration_matches_the_models(sqlite_url: str) -> None:
    """The baseline migration must not drift from adminos.db.models."""
    upgrade_to_head(sqlite_url)

    with create_engine(sqlite_url).connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_server_default": False, "compare_type": False},
        )
        differences = compare_metadata(context, Base.metadata)

    assert differences == []


def test_migration_downgrades_cleanly(sqlite_url: str) -> None:
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "adminos/db/migrations"))
    config.set_main_option("sqlalchemy.url", sqlite_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    tables = set(inspect(create_engine(sqlite_url)).get_table_names())

    assert not (EXPECTED_TABLES & tables)


def test_session_scope_persists_and_rolls_back(sqlite_url: str) -> None:
    upgrade_to_head(sqlite_url)

    with session_scope() as session:
        session.add(Evidence(source_system="gmail", source_thread_id="thread-1"))

    with pytest.raises(RuntimeError):
        with session_scope() as session:
            session.add(Evidence(source_system="gmail", source_thread_id="thread-2"))
            session.flush()
            raise RuntimeError("boom")

    with session_scope() as session:
        thread_ids = {row.source_thread_id for row in session.query(Evidence).all()}

    assert thread_ids == {"thread-1"}
