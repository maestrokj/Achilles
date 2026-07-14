"""Shared route plumbing: every AI admin surface sits behind one permission."""

from typing import Annotated

from achilles.auth.constants import Permission
from achilles.auth.dependencies import require
from achilles.auth.models import User

AiAdmin = Annotated[User, require(Permission.AI_ADMIN)]
