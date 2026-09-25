from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from thoth.adapters.storage import behavior_execution_schema as behavior_execution_schema
from thoth.adapters.storage import evaluation_run_schema as evaluation_run_schema
from thoth.adapters.storage import research_schema as research_schema
from thoth.adapters.storage import resource_scope_schema as resource_scope_schema
from thoth.adapters.storage import test_validity_schema as test_validity_schema
from thoth.adapters.storage.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

override_url = os.getenv("THOTH_ALEMBIC_URL")
if override_url:
    config.set_main_option("sqlalchemy.url", override_url)

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
