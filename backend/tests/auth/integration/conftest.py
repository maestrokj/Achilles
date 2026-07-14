"""Integration scaffold: clean DB/Redis per test, direct DB access, login helpers.

Full-HTTP tests commit across several sessions (audit writes its own transaction),
so isolation is TRUNCATE + FLUSHDB rather than a savepoint rollback.
"""

import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from dataclasses import dataclass, field

import pytest
import respx
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import AsyncClient, Response
from redis.asyncio import Redis
from saq.job import Job as SaqJob
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from achilles.auth.security.passwords import HIBP_RANGE_URL
from achilles.config import Settings
from achilles.email import jobs as email_jobs
from achilles.email.compose import ComposedEmail
from achilles.email.models import SmtpSettings
from tests.conftest import FlushRedis
from tests.factories.users import DEFAULT_PASSWORD, create_user

KEYS_URL = "/api/v1/api-keys"


async def issue_key(client: AsyncClient, **body: object) -> dict[str, object]:
    """Create an API key as the currently authorized user; returns the response body."""
    resp = await client.post(KEYS_URL, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def issue_key_only(
    client: AsyncClient,
    db_session: AsyncSession,
    authorize: AuthorizeFn,
    *,
    sources: list[int] | None = None,
) -> str:
    """Login a fresh member, mint a key, drop the JWT — key-only surfaces see only the key."""
    user = await create_user(db_session)
    await authorize(user.email)
    body: dict[str, object] = {"sources": sources} if sources is not None else {}
    raw_key = str((await issue_key(client, **body))["key"])
    del client.headers["Authorization"]
    return raw_key


# Reverse-dependency order; TRUNCATE bypasses row triggers, so audit_log clears fine.
AUTH_TABLES = (
    "audit_log",
    "identity_mapping",
    "api_keys",
    "link_tokens",
    "reset_tokens",
    "invite_tokens",
    "refresh_tokens",
    "users",
)


@pytest.fixture(scope="session")
async def db_engine(test_settings: Settings) -> AsyncGenerator[AsyncEngine]:
    # Session-scoped pool (loop is session-scoped too, see pyproject): reusing
    # connections keeps the per-test TCP churn off Docker's port proxy.
    engine = create_async_engine(test_settings.database_url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def redis_durable(test_settings: Settings) -> AsyncGenerator[Redis]:
    client = Redis.from_url(test_settings.redis_durable_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture(autouse=True)
async def clean_state(db_engine: AsyncEngine, flush_redis: FlushRedis) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(AUTH_TABLES)} RESTART IDENTITY CASCADE"))
        # Seeded singleton: reset via UPDATE, never TRUNCATE.
        await conn.execute(RESET_SMTP_SETTINGS)
    await flush_redis()


@pytest.fixture(autouse=True)
def hibp_clean() -> Generator[respx.MockRouter]:
    """Answer HIBP with a clean range; block any other real egress."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.get(url__startswith=HIBP_RANGE_URL).mock(
            return_value=Response(200, text="0000000000000000000000000000000000A:5")
        )
        yield router


type LoginFn = Callable[..., Awaitable[Response]]
type AuthorizeFn = Callable[..., Awaitable[Response]]


@pytest.fixture
def login(client: AsyncClient) -> LoginFn:
    async def _login(
        email: str, password: str = DEFAULT_PASSWORD, *, remember_me: bool = False
    ) -> Response:
        return await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password, "remember_me": remember_me},
        )

    return _login


# --- Email scaffold: DB-driven availability + captured queue + inline worker ---

RESET_SMTP_SETTINGS = sa.text(
    "UPDATE smtp_settings SET host = NULL, port = NULL, security = 'starttls',"
    " username = NULL, password_enc = NULL, from_address = NULL, is_enabled = false,"
    " last_test_ok = NULL, last_test_at = NULL WHERE id = 1"
)

_TOKEN_IN_LINK = re.compile(r"/(?:invite|reset-password)/([^\s\"<]+)")


async def set_smtp(db_session: AsyncSession, *, enabled: bool) -> None:
    """Flip the smtp_settings singleton the way the SMTP screen would."""
    await db_session.execute(
        sa.update(SmtpSettings)
        .where(SmtpSettings.id == 1)
        .values(
            is_enabled=enabled,
            host="smtp.test" if enabled else None,
            port=25 if enabled else None,
            security="none",
            from_address="Achilles <no-reply@test.local>" if enabled else None,
        )
    )
    await db_session.commit()


@dataclass(frozen=True, slots=True)
class QueuedEmail:
    lane: str
    function: str
    job_id: str
    kwargs: dict[str, object]


@dataclass(frozen=True, slots=True)
class Letter:
    to: str
    subject: str
    text: str

    @property
    def token(self) -> str:
        """The action-link token — the same thing a person clicks in the letter."""
        match = _TOKEN_IN_LINK.search(self.text)
        assert match, f"no action link in letter to {self.to}"
        return match.group(1)


@dataclass
class Outbox:
    """What left the request path: queued jobs, and letters once drained."""

    jobs: list[QueuedEmail] = field(default_factory=list)
    letters: list[Letter] = field(default_factory=list)

    @property
    def invites(self) -> list[tuple[str, str, str]]:
        """(to, token, role) straight from the queued job payloads."""
        return [
            (str(j.kwargs["to"]), str(j.kwargs["token"]), str(j.kwargs["role"]))
            for j in self.jobs
            if j.function == "send_invite_email"
        ]

    async def drain(self) -> list[Letter]:
        """Run the queued email jobs inline (the worker's part), newest included."""
        pending, self.jobs = self.jobs, []
        for job in pending:
            fn = getattr(email_jobs, job.function)
            await fn(None, **job.kwargs)
        return self.letters


@pytest.fixture
async def outbox(
    app: FastAPI,
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Outbox:
    """SMTP configured + the interactive/background publish intercepted."""
    del app  # ordering: the app must exist before we patch its publish path
    box = Outbox()

    async def capture_publish(
        queue_url: str,
        redis: object,
        lane: object,
        function_name: str,
        *,
        job_id: str,
        **kwargs: object,
    ) -> bool:
        del queue_url, redis
        # Mirror SAQ's enqueue split: Job fields (retries, retry_delay, …)
        # configure the job; only the rest reach the function on drain().
        fn_kwargs = {k: v for k, v in kwargs.items() if k not in SaqJob.__dataclass_fields__}
        box.jobs.append(QueuedEmail(str(lane), function_name, job_id, fn_kwargs))
        return True

    async def capture_send(
        row: SmtpSettings, *, key: bytes, to: str, composed: ComposedEmail, send_timeout: float
    ) -> None:
        del row, key, send_timeout
        box.letters.append(Letter(to=to, subject=composed.subject, text=composed.text))

    monkeypatch.setattr("achilles.api.background.publish", capture_publish)
    monkeypatch.setattr("achilles.email.smtp.send", capture_send)
    monkeypatch.setattr(email_jobs, "app_settings", test_settings)
    await set_smtp(db_session, enabled=True)
    return box


@pytest.fixture
def authorize(client: AsyncClient, login: LoginFn) -> AuthorizeFn:
    """Login and put the access token on the client's default headers."""

    async def _authorize(email: str, password: str = DEFAULT_PASSWORD) -> Response:
        resp = await login(email, password)
        if resp.status_code == 200:
            client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        return resp

    return _authorize
