import json
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from achilles.auth.security.crypto import derive_crypto_key

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE if _ENV_FILE.is_file() else None,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "Achilles"
    debug: bool = False
    log_level: str = "INFO"
    secret_key: str
    # Data-encryption key for *_enc columns (32 bytes, urlsafe base64).
    # Empty → derived from secret_key via HKDF; production sets its own.
    crypto_key: str = ""

    # Base URL of the instance as users reach it — absolute links in emails
    # (invite / reset / notification deep links) are built against it.
    public_base_url: str = "http://localhost:3000"

    # --- Database ---
    database_url: str
    redis_durable_url: str
    redis_cache_url: str

    # --- Connection pools ---
    pg_pool_size: int = 5
    pg_pool_max_overflow: int = 10
    pg_pool_recycle: int = 1800
    redis_max_connections: int = 20

    # --- Worker lanes (SAQ slots per process; see infra/worker/base.py) ---
    # Interactive stays snappy, background chews bulk. The agents figure is the
    # static per-process ceiling only — the live org-wide LLM limit is the DB
    # gate at mark_running (platform_settings.agent_max_concurrency), so it
    # sits above that knob's default; excess runs wait in queued.
    worker_concurrency_interactive: int = 10
    worker_concurrency_background: int = 4
    worker_concurrency_agents: int = 8

    # --- API rate limits (requests per minute, tiered by role) ---
    api_rate_limit_rpm_member: int = 120
    api_rate_limit_rpm_admin: int = 300
    api_rate_limit_rpm_owner: int = 300

    # --- CORS ---
    cors_origins: list[str]

    def derived_crypto_key(self) -> bytes:
        """The runtime AES key — the one derivation every worker/startup site shares."""
        return derive_crypto_key(crypto_key=self.crypto_key, secret_key=self.secret_key)

    def public_url(self, path: str) -> str:
        """An absolute link into the app (letters, deep links) — the one join point."""
        return f"{self.public_base_url.rstrip('/')}{path}"

    @field_validator("crypto_key")
    @classmethod
    def _validate_crypto_key(cls, v: str) -> str:
        if v:
            # Fail fast at startup: a malformed key must not surface later
            # as an undecryptable-secret error on a live request.
            derive_crypto_key(crypto_key=v, secret_key="")
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return [str(item) for item in parsed]
                except json.JSONDecodeError, ValueError:
                    pass
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


settings = Settings()  # type: ignore[call-arg]
