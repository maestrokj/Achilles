"""Bot-side Mattermost REST client: /users/me and /posts under /api/v4.

Unlike the Slack/Telegram envelopes, Mattermost speaks plain HTTP: a refusal is
a non-2xx status with a JSON body carrying `id` (a stable slug) and `message`
(human text) — so the unwrap reads the status code, not an "ok" field.
"""

import httpx

from achilles.mattermost.constants import API_PATH, MATTERMOST_HTTP_TIMEOUT


class MattermostApiError(Exception):
    """API refusal — the server's message is the message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MattermostClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"{base_url}{API_PATH}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=MATTERMOST_HTTP_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, object]:
        if response.is_success:
            data: dict[str, object] = response.json()
            return data
        try:
            message = str(response.json().get("message") or "")
        except ValueError:
            message = ""
        raise MattermostApiError(message or f"HTTP {response.status_code}")

    async def get_me(self) -> dict[str, object]:
        """The bot's own account — proves the token and names the bot."""
        return self._unwrap(await self._http.get("/users/me"))

    async def create_post(
        self, *, channel_id: str, message: str, root_id: str | None = None
    ) -> dict[str, object]:
        """Post into the channel the DM arrived in; root_id keeps the thread."""
        payload: dict[str, object] = {"channel_id": channel_id, "message": message}
        if root_id:
            payload["root_id"] = root_id
        return self._unwrap(await self._http.post("/posts", json=payload))
