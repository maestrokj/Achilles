"""Bot-side Telegram Bot API client: getMe, sendMessage, setWebhook, deleteWebhook.

The Bot API answers HTTP 200 with {"ok": false, "description": …} on refusal and
wraps success in {"ok": true, "result": …}, so errors and payloads both live in
the envelope unwrap, not in status codes.
"""

import httpx

from achilles.telegram.constants import TELEGRAM_API_BASE_URL, TELEGRAM_HTTP_TIMEOUT


class TelegramApiError(Exception):
    """Bot API refusal — the description is the message."""

    def __init__(self, description: str) -> None:
        super().__init__(description)
        self.description = description


class TelegramBotClient:
    def __init__(self, token: str, *, base_url: str = TELEGRAM_API_BASE_URL) -> None:
        # Every method is a call under /bot<token>/… — bake the token into the base.
        self._http = httpx.AsyncClient(
            base_url=f"{base_url}/bot{token}",
            timeout=TELEGRAM_HTTP_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, object]:
        response.raise_for_status()
        data: dict[str, object] = response.json()
        if not data.get("ok"):
            raise TelegramApiError(str(data.get("description") or "unknown_error"))
        return data

    async def get_me(self) -> dict[str, object]:
        return self._unwrap(await self._http.post("/getMe"))

    async def send_message(
        self, *, chat_id: str, text: str, parse_mode: str = "HTML"
    ) -> dict[str, object]:
        return self._unwrap(
            await self._http.post(
                "/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            )
        )

    async def set_webhook(self, *, url: str, secret_token: str) -> dict[str, object]:
        # Only message updates interest v1 — narrow the subscription at the source.
        return self._unwrap(
            await self._http.post(
                "/setWebhook",
                json={"url": url, "secret_token": secret_token, "allowed_updates": ["message"]},
            )
        )

    async def delete_webhook(self) -> dict[str, object]:
        return self._unwrap(await self._http.post("/deleteWebhook"))

    async def get_webhook_info(self) -> dict[str, object]:
        """Telegram's own view of the webhook: registered url + last delivery error.

        The honest source of truth for "is the bot actually receiving updates" —
        getMe only proves the token is live, not that Telegram can reach us.
        """
        return self._unwrap(await self._http.post("/getWebhookInfo"))
