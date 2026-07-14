"""Lane `interactive`: fast, low-latency jobs (email tx, notifications, messenger inbound).

Run: `saq achilles.infra.worker.interactive.settings`.
"""

from typing import Any

from achilles.email.jobs import send_invite_email, send_password_reset
from achilles.infra.worker.base import Lane, lane_settings
from achilles.mattermost.jobs import mattermost_inbound
from achilles.notifications.jobs import deliver_email, deliver_webhook, raise_event
from achilles.slack.jobs import slack_inbound
from achilles.telegram.jobs import telegram_inbound

settings: dict[str, Any] = lane_settings(
    Lane.INTERACTIVE,
    functions=[
        slack_inbound,
        telegram_inbound,
        mattermost_inbound,
        send_invite_email,
        send_password_reset,
        deliver_email,
        deliver_webhook,
        raise_event,
    ],
)
