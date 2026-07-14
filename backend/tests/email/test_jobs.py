"""Queued letter jobs: language chain, error handling, silent-drop gates (integration)."""

from typing import cast

import pytest
from saq.types import Context
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.email import jobs as email_jobs
from achilles.email import smtp
from achilles.email.constants import PermanentSendError, TransientSendError
from achilles.knowledge_store.services import platform
from tests.auth.integration.conftest import Outbox, set_smtp
from tests.factories.users import create_user

pytestmark = [pytest.mark.integration, pytest.mark.p1]

CTX = cast("Context", None)

INVITE_KWARGS: dict[str, object] = {
    "to": "new@example.com",
    "token": "tok-abc",
    "role": "member",
    "inviter_name": "Anna",
    "ttl_hours": 48,
}


async def test_invite_letter_speaks_the_org_language(
    db_session: AsyncSession, outbox: Outbox
) -> None:
    row = await platform.get_platform_settings(db_session)
    row.locale = "en"
    await db_session.commit()

    await email_jobs.send_invite_email(CTX, **INVITE_KWARGS)
    (letter,) = outbox.letters
    assert letter.to == "new@example.com"
    assert letter.subject == "Anna invited you to Achilles"
    assert "/invite/tok-abc" in letter.text
    assert "as Member" in letter.text


async def test_reset_letter_speaks_the_recipient_language(
    db_session: AsyncSession, outbox: Outbox
) -> None:
    user = await create_user(db_session)
    user.locale = "en"
    await db_session.commit()
    await email_jobs.send_password_reset(CTX, email=user.email)
    (letter,) = outbox.letters
    assert letter.subject == "Reset your Achilles password"

    ru_user = await create_user(db_session)  # no personal locale → org default (ru)
    await email_jobs.send_password_reset(CTX, email=ru_user.email)
    assert outbox.letters[-1].subject == "Сброс пароля — Achilles"


async def test_smtp_switched_off_drops_silently(db_session: AsyncSession, outbox: Outbox) -> None:
    await set_smtp(db_session, enabled=False)
    await email_jobs.send_invite_email(CTX, **INVITE_KWARGS)
    assert outbox.letters == []


async def test_permanent_refusal_finishes_the_job(
    outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    del outbox

    async def refuse(*args: object, **kwargs: object) -> None:
        raise PermanentSendError("550 mailbox unavailable")

    monkeypatch.setattr(smtp, "send", refuse)
    await email_jobs.send_invite_email(CTX, **INVITE_KWARGS)  # no raise → no retry


async def test_transient_failure_raises_for_retry(
    outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    del outbox

    async def flaky(*args: object, **kwargs: object) -> None:
        raise TransientSendError("450 try again")

    monkeypatch.setattr(smtp, "send", flaky)
    with pytest.raises(TransientSendError):
        await email_jobs.send_invite_email(CTX, **INVITE_KWARGS)
