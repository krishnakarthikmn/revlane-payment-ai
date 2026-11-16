"""Settings read from the environment and the repository's `.env` file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://revlane:revlane@localhost:5432/revlane"

    llm_provider: str = "anthropic"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_stub: bool = True

    provider_live_calls: bool = False

    # "local" keeps encrypted values in Postgres under VAULT_KEY (development only).
    vault_backend: str = "local"
    vault_key: str = ""

    # Shopper tokens are keyed hashes; each tenant's key is derived from this.
    token_hash_root_key: str = ""

    # "local" writes files under STORAGE_PATH (development only).
    storage_backend: str = "local"
    storage_path: str = "./data/objects"

    # The switch that stops every agent for every tenant.
    agents_enabled: bool = True
    # Carry out queued runs inside the API process, for setups with no worker.
    agent_runs_inline: bool = False

    # Requests one client may make in a minute, by kind of route; and the
    # largest body a route other than an upload accepts.
    rate_limit_per_minute: int = 1_200
    rate_limit_chat_per_minute: int = 60
    rate_limit_uploads_per_minute: int = 120
    rate_limit_sign_in_per_minute: int = 10
    max_body_bytes: int = 1024 * 1024

    # Sign-in. Session tokens are signed with this secret; with it empty nobody can sign in.
    session_secret: str = ""
    session_hours: int = 12
    # Set true wherever the console is served over HTTPS.
    session_cookie_secure: bool = False
    # A bearer token for the metrics endpoint. With it empty the endpoint is switched off.
    metrics_token: str = ""

    # The demo tenant, created at start-up so there is somewhere to sign in to.
    demo_tenant_id: UUID = UUID("00000000-0000-4000-8000-000000000001")
    demo_tenant_name: str = "Acme Travel"
    # The demo tenant's people, as in the prototype. Decision 6. They can sign in
    # only if DEMO_USER_PASSWORD is set.
    demo_user_password: str = ""
    demo_user_email: str = "priya.menon@acmetravel.example"
    demo_user_name: str = "Priya Menon"
    demo_second_approver_email: str = "arjun@acmetravel.example"
    demo_second_approver_name: str = "Arjun"

    web_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @property
    def storage_root(self) -> Path:
        path = Path(self.storage_path)
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def llm_mode(self) -> Literal["stub", "live"]:
        """The stub is used unless a key is set and stub mode is switched off."""
        if self.llm_stub or not self.llm_api_key:
            return "stub"
        return "live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
