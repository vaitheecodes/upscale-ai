import os
from collections.abc import Callable, Iterator, Sequence

import pymupdf
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import Settings
from app.db import Base, alembic_config, make_engine, upgrade_database
from app.main import create_app

ADMIN_KEY = "test-admin-key"


@pytest.fixture(scope="session")
def database_url(tmp_path_factory) -> str:
    """SQLite by default; set TEST_DATABASE_URL to run the suite on Postgres.

    The test database is wiped and rebuilt from migrations at session start.
    """
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}"


@pytest.fixture(scope="session")
def migrated_engine(database_url) -> Iterator[Engine]:
    engine = make_engine(database_url)
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "base")
    upgrade_database(engine)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(migrated_engine) -> Iterator[None]:
    yield
    with migrated_engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            names = ", ".join(t.name for t in Base.metadata.sorted_tables)
            connection.execute(text(f"TRUNCATE {names} CASCADE"))
        else:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())


@pytest.fixture
def settings(tmp_path, database_url) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        upload_dir=tmp_path / "uploads",
        admin_api_key=ADMIN_KEY,
        transcription_provider="none",
    )


@pytest.fixture
def app(settings):
    app = create_app(settings)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def create_tenant(client) -> Callable[..., dict]:
    def _create(name: str = "Demo Moodle", **extra) -> dict:
        resp = client.post(
            "/admin/tenants", json={"name": name, **extra}, headers={"X-Admin-Key": ADMIN_KEY}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _create


@pytest.fixture
def auth(create_tenant) -> dict[str, str]:
    tenant = create_tenant("Tenant A", lms_type="moodle", lms_base_url="https://moodle-a.example.edu")
    return {"X-API-Key": tenant["api_key"]}


@pytest.fixture
def other_auth(create_tenant) -> dict[str, str]:
    return {"X-API-Key": create_tenant("Tenant B")["api_key"]}


@pytest.fixture
def make_pdf() -> Callable[[Sequence[str]], bytes]:
    """Build a PDF in memory with one page per text."""

    def _make(page_texts: Sequence[str]) -> bytes:
        doc = pymupdf.open()
        for text in page_texts:
            page = doc.new_page()
            if text:
                overflow = page.insert_textbox(page.rect + (36, 36, -36, -36), text, fontsize=6)
                assert overflow >= 0, "test text does not fit on the page"
        data = doc.tobytes()
        doc.close()
        return data

    return _make
