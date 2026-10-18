"""Alembic environment.

Runs **synchronously** even though the application is async: migrations have no
reason to be concurrent, and psycopg3 serves both modes behind the same
``postgresql+psycopg://`` URL, so there is one driver and no second code path.

The URL comes from ``DCP_DATABASE_URL`` through the settings object rather than
from ``alembic.ini``, so a credential never sits in a committed file.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base, all_models
from app.db.partitions import PARTITIONED_TABLES

# Import every model so Base.metadata is complete before autogenerate runs.
all_models()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the URL, preferring an explicit -x url= for test harnesses."""
    overrides = context.get_x_argument(as_dictionary=True)
    if "url" in overrides:
        return str(overrides["url"])
    # Honour a URL set programmatically on the Config (the test harness does
    # this). Without it, alembic.command.* would silently fall back to settings
    # and migrate the wrong database.
    configured = config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured
    return get_settings().database_url.get_secret_value()


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Keep partitions out of autogenerate.

    Partitions are created by a maintenance job, not by migrations, so
    autogenerate must not propose dropping the ones it finds in the database.
    """
    return not (
        type_ == "table"
        and name is not None
        and name.startswith(tuple(f"{table}_" for table in PARTITIONED_TABLES))
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (``alembic upgrade head --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        # See tests/integration/test_migrations.py: function defaults such as
        # now() always compare unequal, producing permanent false drift.
        compare_server_default=False,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # See tests/integration/test_migrations.py: function defaults such as
            # now() always compare unequal, producing permanent false drift.
            compare_server_default=False,
            include_object=_include_object,
            # Keeps constraint names stable between model and database
            # (see the naming convention in app/db/base.py).
            render_as_batch=False,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
