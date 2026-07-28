from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from adminos.config import get_database_url, normalize_database_url
from adminos.db.engine import DatabaseNotConfigured
from adminos.db.models import Base


config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def get_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url", None)
    if configured_url:
        return configured_url

    database_url = get_database_url()
    if database_url is None:
        raise DatabaseNotConfigured(
            "DATABASE_URL environment variable is not configured."
        )
    return normalize_database_url(database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config.set_main_option("sqlalchemy.url", get_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
