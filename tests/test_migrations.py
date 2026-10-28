import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from app.config import Settings
from app.db import Base, alembic_config, make_engine, upgrade_database
from app.main import create_app

TABLES = {"tenants", "sources", "chunks", "learners", "learning_events"}


def test_migrated_schema_matches_models(migrated_engine):
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_upgrade_downgrade_roundtrip(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    upgrade_database(engine)
    assert TABLES <= set(inspect(engine).get_table_names())

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}

    upgrade_database(engine)
    assert TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_app_refuses_unmigrated_database(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'empty.db'}", upload_dir=tmp_path)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        create_app(settings)
    # And it must not have created any tables on its own.
    assert inspect(make_engine(settings.database_url)).get_table_names() == []
