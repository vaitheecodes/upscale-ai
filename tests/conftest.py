import re
from collections.abc import Callable, Iterator, Sequence

import pymupdf
import pytest
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from app.config import Settings
from app.db import PROJECT_ROOT, Base, make_engine, upgrade_database
from app.main import create_app

ADMIN_KEY = "test-admin-key"
_SAFE_TEST_DB_NAME = re.compile(r"^[A-Za-z0-9_]+_test$")


class _TestEnv(BaseSettings):
    """TEST_DATABASE_URL from the environment or the project's .env file."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    test_database_url: str | None = None


def _recreate_postgres_db(url: str, drop_only: bool = False) -> None:
    db_url = make_url(url)
    name = db_url.database or ""
    if not _SAFE_TEST_DB_NAME.match(name):
        raise pytest.UsageError(
            f"TEST_DATABASE_URL database {name!r} must end in '_test'; it is dropped and recreated."
        )
    admin = create_engine(db_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        if not drop_only:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def database_url(tmp_path_factory) -> Iterator[str]:
    """SQLite by default; set TEST_DATABASE_URL (env or .env) to use Postgres.

    A Postgres test database is a throw-away: created fresh for the session
    (with pgvector, like dev), built from migrations and dropped afterwards.
    """
    url = _TestEnv().test_database_url
    if not url:
        yield f"sqlite:///{tmp_path_factory.mktemp('db') / 'test.db'}"
        return
    if make_url(url).get_backend_name() != "postgresql":
        yield url
        return

    _recreate_postgres_db(url)
    try:
        engine = make_engine(url)
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        engine.dispose()
        yield url
    finally:
        _recreate_postgres_db(url, drop_only=True)


@pytest.fixture(scope="session")
def migrated_engine(database_url) -> Iterator[Engine]:
    engine = make_engine(database_url)
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
