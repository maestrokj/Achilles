"""Bot-side Slack Web API client: auth.test, users.info, chat.postMessage.

Deliberately independent of the harvester connector — that one is ETL with a
manifest and throttling; the bot needs three calls. The Web API answers HTTP
200 with {"ok": false, "error": …} on refusal, so errors live in the envelope
unwrap, not in status codes.
"""

import httpx

from achilles.slack.constants import SLACK_API_BASE_URL, SLACK_HTTP_TIMEOUT


class SlackApiError(Exception):
    """Web API refusal — the error slug is the message."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


class SlackBotClient:
    def __init__(self, token: str, *, base_url: str = SLACK_API_BASE_URL) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=SLACK_HTTP_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, object]:
        response.raise_for_status()
        data: dict[str, object] = response.json()
        if not data.get("ok"):
            raise SlackApiError(str(data.get("error") or "unknown_error"))
        return data

    async def auth_test(self) -> dict[str, object]:
        return self._unwrap(await self._http.post("/auth.test"))

    async def users_info(self, user_id: str) -> dict[str, object]:
        # users.info is a read method — url-encoded params, not a JSON body.
        return self._unwrap(await self._http.get("/users.info", params={"user": user_id}))

    async def post_message(
        self, *, channel: str, text: str, thread_ts: str | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return self._unwrap(await self._http.post("/chat.postMessage", json=payload))
