"""Test setup. Database tests run against a separate database, never the dev one."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import OperationalError, ProgrammingError

API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "postgresql+psycopg://revlane:revlane@localhost:5432/revlane"


def _test_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    base = make_url(os.environ.get("DATABASE_URL", DEFAULT_URL))
    return base.set(database=f"{base.database}_test").render_as_string(hide_password=False)


TEST_URL = _test_url()
# Everything the application opens during tests goes to the test database.
os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("VAULT_KEY", "test-only-vault-key")
os.environ.setdefault("TOKEN_HASH_ROOT_KEY", "test-only-token-root")
# Documents written during tests go to a throwaway folder, never the repository.
os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="revlane-test-objects-")
os.environ["LLM_STUB"] = "true"
os.environ["SESSION_SECRET"] = "test-only-session-secret"
os.environ["DEMO_USER_PASSWORD"] = "test-only-demo-password"
os.environ["METRICS_TOKEN"] = "test-only-metrics-token"


def alembic_config() -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", TEST_URL)
    return config


def _ensure_database() -> None:
    url = make_url(TEST_URL)
    try:
        with create_engine(url).connect():
            return
    except OperationalError:
        pass
    # A superuser (Docker, CI) can create it; otherwise `make db` does.
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    except (OperationalError, ProgrammingError) as error:
        raise RuntimeError(
            f"The test database {url.database} does not exist and could not be created. "
            "Run `make db`."
        ) from error


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    """A freshly migrated test database."""
    _ensure_database()
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
