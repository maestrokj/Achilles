"""platform_settings editor mechanics: partial patch, merged-row rules, redis mirror."""

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.admin import maintenance
from achilles.admin.schemas import PlatformSettingsOut, PlatformSettingsPatch
from achilles.api.problems import field_validation_error
from achilles.knowledge_store.models import PlatformSettings
from achilles.knowledge_store.services import platform
from achilles.knowledge_store.services.backup_schedule import normalize_cadence

# Columns where PATCH null means "clear it" (nullable in platform_settings);
# an explicit null anywhere else would write NULL into a NOT NULL column.
NULLABLE_FIELDS = frozenset(
    {
        "org_logo_url",
        "org_description",
        "ai_monthly_budget",
        "chat_weekly_token_budget",
        "agent_weekly_token_budget",
        "curation_weekday",
    }
)


def settings_out(row: PlatformSettings, *, smtp_configured: bool) -> PlatformSettingsOut:
    out = PlatformSettingsOut.model_validate(row)
    out.smtp_configured = smtp_configured
    return out


def _check_merged_rules(row: PlatformSettings) -> None:
    """Cross-field rules only the merged row can answer (the DB CHECKs backstop)."""
    row.curation_weekday = normalize_cadence(
        row.curation_frequency, row.curation_weekday, field="curation_weekday"
    )
    if row.ai_budget_alert_enabled and row.ai_monthly_budget is None:
        raise field_validation_error("ai_budget_alert_enabled", "the alert needs a monthly budget")
    # A session nests: an access token inside a refresh window inside the absolute
    # ceiling. Inverted, the outer bound silently truncates the inner one.
    if row.access_token_ttl > row.refresh_token_ttl:
        raise field_validation_error(
            "access_token_ttl", "must not exceed the refresh token lifetime"
        )
    if row.refresh_token_ttl > row.session_absolute_ttl:
        raise field_validation_error(
            "refresh_token_ttl", "must not exceed the absolute session lifetime"
        )


async def apply_patch(
    session: AsyncSession, redis: Redis, patch: PlatformSettingsPatch
) -> PlatformSettings:
    row = await platform.get_platform_settings(session)
    for field in patch.model_fields_set:
        value = getattr(patch, field)
        if value is None and field not in NULLABLE_FIELDS:
            raise field_validation_error(field, "null is not allowed")
        setattr(row, field, value)
    _check_merged_rules(row)
    await session.flush()
    if "maintenance_mode" in patch.model_fields_set:
        await maintenance.set_enabled(redis, enabled=row.maintenance_mode)
    return row
