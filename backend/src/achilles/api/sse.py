"""Server-sent events wire format — one home for the frame and the headers."""

import json

from pydantic import BaseModel

SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",  # nginx: do not buffer the stream
}

# Max silence before a keep-alive frame. Safely under nginx's 60s
# proxy_read_timeout; the client watchdog treats twice this as a dead connection.
HEARTBEAT_SECONDS = 25.0


def sse_frame(event: str, payload: BaseModel | dict[str, object]) -> str:
    data = payload.model_dump_json() if isinstance(payload, BaseModel) else json.dumps(payload)
    return f"event: {event}\ndata: {data}\n\n"
