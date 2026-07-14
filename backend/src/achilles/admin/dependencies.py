"""Shared admin route dependencies (settings routes + dashboard + surface settings)."""

from typing import Annotated

from achilles.auth.constants import Permission
from achilles.auth.dependencies import require
from achilles.auth.models import User

SettingsReader = Annotated[User, require(Permission.SETTINGS_READ)]
SettingsManager = Annotated[User, require(Permission.SETTINGS_MANAGE)]
