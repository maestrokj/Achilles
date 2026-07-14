"""Request-scope publish helper shared by all route modules."""

from fastapi import Request

from achilles.infra.worker.base import Lane, publish


async def publish_lane(
    request: Request, lane: Lane, function_name: str, *, job_id: str, **kwargs: object
) -> None:
    """Publish a job to the given lane using the app's configured redis."""
    await publish(
        request.app.state.settings.redis_durable_url,
        request.state.redis.durable,
        lane,
        function_name,
        job_id=job_id,
        **kwargs,
    )
