"""PlatformSettingsPatch validation: the field-level rules (unit)."""

import pytest
from pydantic import ValidationError

from achilles.admin.schemas import PlatformSettingsPatch

pytestmark = [pytest.mark.unit]


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValidationError, match="IANA"):
        PlatformSettingsPatch(timezone="Mars/Olympus_Mons")


def test_known_timezone_passes():
    assert PlatformSettingsPatch(timezone="Europe/Moscow").timezone == "Europe/Moscow"


@pytest.mark.parametrize("color", ["6366f1", "#63f", "#6366g1", "red"])
def test_accent_color_must_be_full_hex(color: str):
    with pytest.raises(ValidationError):
        PlatformSettingsPatch(accent_color=color)


@pytest.mark.parametrize("value", ["fr", "en-US", ""])
def test_locale_is_a_closed_catalogue(value: str):
    with pytest.raises(ValidationError):
        PlatformSettingsPatch(locale=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["4:00", "24:00", "04:60", "0400"])
def test_curation_time_must_be_hh_mm(value: str):
    with pytest.raises(ValidationError):
        PlatformSettingsPatch(curation_time=value)


@pytest.mark.parametrize("field", ["access_token_ttl", "refresh_token_ttl", "session_absolute_ttl"])
def test_ttls_must_be_positive(field: str):
    with pytest.raises(ValidationError):
        PlatformSettingsPatch(**{field: 0})


def test_access_ttl_is_capped_at_an_hour():
    """v1 revocation only bites once the access token expires — keep that window short."""
    assert PlatformSettingsPatch(access_token_ttl=3600).access_token_ttl == 3600
    with pytest.raises(ValidationError):
        PlatformSettingsPatch(access_token_ttl=3601)


def test_reconcile_minute_of_week_bounds():
    with pytest.raises(ValidationError):
        PlatformSettingsPatch(reconcile_minute_of_week=10080)
    assert PlatformSettingsPatch(reconcile_minute_of_week=0).reconcile_minute_of_week == 0


def test_fields_set_distinguishes_null_from_absent():
    patch = PlatformSettingsPatch.model_validate({"org_logo_url": None})
    assert "org_logo_url" in patch.model_fields_set
    assert "org_name" not in patch.model_fields_set
