"""Aggregate router: infra tier (unversioned health · version) + the /api/v1 tier.

Tier map and ownership: docs/architecture/modules/api/index.html#surface-map.
Domain modules register their routers on ``v1_router`` below, before it is
included into ``api_router``.
"""

from fastapi import APIRouter

from achilles.admin.dashboard import router as admin_dashboard_router
from achilles.admin.routes import public_router as platform_public_router
from achilles.admin.routes import router as admin_settings_router
from achilles.agent_engine.router import admin_router as agents_admin_router
from achilles.agent_engine.router import limits_router as agent_limits_router
from achilles.agent_engine.router import router as agents_router
from achilles.ai_foundation.routes.prompt import router as ai_prompt_router
from achilles.ai_foundation.routes.registry import router as ai_registry_router
from achilles.ai_foundation.routes.tools import router as ai_tools_router
from achilles.ai_foundation.routes.usage import router as ai_usage_router
from achilles.api import PUBLIC_V1, V1_PREFIX
from achilles.api.health import router as health_router
from achilles.auth.routes.admin_users import router as admin_users_router
from achilles.auth.routes.api_keys import admin_router as admin_api_keys_router
from achilles.auth.routes.api_keys import router as api_keys_router
from achilles.auth.routes.invites import router as invites_router
from achilles.auth.routes.link import router as link_router
from achilles.auth.routes.password import router as auth_password_router
from achilles.auth.routes.profile import router as auth_profile_router
from achilles.auth.routes.session import router as auth_session_router
from achilles.email.routes.admin import router as smtp_admin_router
from achilles.events.routes import router as events_router
from achilles.harvester.routes.sources import router as sources_router
from achilles.harvester.routes.sources import webhook_router as harvester_webhook_router
from achilles.knowledge_store.routes.admin import router as knowledge_admin_router
from achilles.knowledge_store.routes.identity_admin import router as identity_admin_router
from achilles.knowledge_store.routes.retrieval import router as retrieval_router
from achilles.mattermost.routes.admin import router as mattermost_admin_router
from achilles.notifications.routes.admin import channels_router as notification_channels_router
from achilles.notifications.routes.admin import routes_router as notification_routes_router
from achilles.notifications.routes.feed import router as notifications_router
from achilles.public_api.routes import router as public_search_router
from achilles.query_engine.routes import conversations_router
from achilles.slack.routes.admin import router as slack_admin_router
from achilles.slack.routes.events import router as slack_events_router
from achilles.telegram.routes.admin import router as telegram_admin_router
from achilles.telegram.routes.events import router as telegram_events_router

v1_router = APIRouter(prefix=V1_PREFIX)
v1_router.include_router(auth_session_router)
v1_router.include_router(auth_password_router)
v1_router.include_router(auth_profile_router)
v1_router.include_router(admin_users_router)
v1_router.include_router(api_keys_router)
v1_router.include_router(admin_api_keys_router)
v1_router.include_router(invites_router)
v1_router.include_router(link_router)
v1_router.include_router(retrieval_router)
v1_router.include_router(knowledge_admin_router)
v1_router.include_router(identity_admin_router)
v1_router.include_router(sources_router)
v1_router.include_router(harvester_webhook_router)
v1_router.include_router(ai_registry_router)
v1_router.include_router(ai_tools_router)
v1_router.include_router(ai_prompt_router)
v1_router.include_router(ai_usage_router)
v1_router.include_router(conversations_router)
v1_router.include_router(agents_router)
v1_router.include_router(agents_admin_router)
v1_router.include_router(agent_limits_router)
v1_router.include_router(admin_settings_router)
v1_router.include_router(admin_dashboard_router)
v1_router.include_router(platform_public_router)
v1_router.include_router(slack_admin_router)
v1_router.include_router(slack_events_router)
v1_router.include_router(telegram_admin_router)
v1_router.include_router(telegram_events_router)
v1_router.include_router(mattermost_admin_router)
v1_router.include_router(smtp_admin_router)
v1_router.include_router(events_router)
v1_router.include_router(notifications_router)
v1_router.include_router(notification_channels_router)
v1_router.include_router(notification_routes_router)

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(v1_router)

# The external tier lives beside /api, not under it (api/index.html#surface-map).
public_router = APIRouter(prefix=PUBLIC_V1)
public_router.include_router(public_search_router)
