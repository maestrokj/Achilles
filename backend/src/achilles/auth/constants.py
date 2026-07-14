"""Auth & Security fixed values — design: docs/architecture/modules/auth-security/."""

from datetime import timedelta
from enum import StrEnum

from achilles.api import API_V1

# --- Identity (data-model.html: users) ---


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


class AuthProvider(StrEnum):
    LOCAL = "local"
    OKTA = "okta"  # v2 (SSO), schema-ready now
    AZURE_AD = "azure_ad"  # v2 (SSO), schema-ready now


class Locale(StrEnum):
    RU = "ru"
    EN = "en"


class DateFormat(StrEnum):
    DMY = "DD.MM.YYYY"
    MDY = "MM/DD/YYYY"
    ISO = "YYYY-MM-DD"


class AuditResult(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


# --- Permissions (authorization.html: endpoints check a permission, not a role) ---


class Permission(StrEnum):
    API_KEYS_OWN = "api_keys:own"
    API_KEYS_MANAGE = "api_keys:manage"
    USERS_INVITE = "users:invite"
    USERS_MANAGE = "users:manage"
    AUDIT_READ = "audit:read"
    KNOWLEDGE_ADMIN = "knowledge:admin"
    AI_ADMIN = "ai:admin"
    SETTINGS_READ = "settings:read"
    SETTINGS_MANAGE = "settings:manage"


_MEMBER_PERMISSIONS = frozenset({Permission.API_KEYS_OWN})
_ADMIN_PERMISSIONS = _MEMBER_PERMISSIONS | {
    Permission.API_KEYS_MANAGE,
    Permission.USERS_INVITE,
    Permission.USERS_MANAGE,
    Permission.KNOWLEDGE_ADMIN,
    Permission.AI_ADMIN,
    Permission.SETTINGS_READ,
}
# The Settings zone is Owner-write (platform-settings wireframe): Admin reads.
_OWNER_PERMISSIONS = _ADMIN_PERMISSIONS | {Permission.AUDIT_READ, Permission.SETTINGS_MANAGE}

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.MEMBER: _MEMBER_PERMISSIONS,
    UserRole.ADMIN: _ADMIN_PERMISSIONS,
    UserRole.OWNER: _OWNER_PERMISSIONS,
}


def has_permission(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(UserRole(role), frozenset())


# --- Token lifetimes (authentication.html) ---

ACCESS_TOKEN_TTL = timedelta(minutes=15)
# The Owner edits the access TTL from the Platform screen, and v1 revocation
# rests on that window: a deactivated account keeps answering until its token
# expires. A ceiling keeps that promise bounded no matter what is typed in.
ACCESS_TOKEN_TTL_MAX = timedelta(hours=1)
REFRESH_SLIDING_TTL = timedelta(days=30)
REFRESH_ABSOLUTE_TTL = timedelta(days=90)
REFRESH_ROTATION_GRACE = timedelta(seconds=10)
INVITE_TOKEN_TTL = timedelta(hours=48)
RESET_TOKEN_TTL = timedelta(hours=1)
LINK_CODE_TTL = timedelta(minutes=15)

# --- JWT (authentication.html#jwt-signing / #jwt-claims) ---

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "achilles"
JWT_AUDIENCE = "achilles-api"
JWT_ACTIVE_KID = "k1"
JWT_REQUIRED_CLAIMS = ("sub", "role", "exp", "iat", "jti", "iss", "aud")

# --- Refresh cookie (authentication.html#refresh-cookie) ---
# The docs write Path=/api/auth in unversioned shorthand; the canonical routes are versioned.

REFRESH_COOKIE_NAME = "__Secure-refresh"
REFRESH_COOKIE_PATH = f"{API_V1}/auth"

# --- argon2id (protection.html#crypto-core: m=19 MiB, t=2, p=1) ---

ARGON2_MEMORY_KIB = 19 * 1024
ARGON2_TIME_COST = 2
ARGON2_PARALLELISM = 1

# --- Password policy (protection.html#password-policy, NIST 800-63B) ---

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
ZXCVBN_MIN_SCORE = 3

# --- One-time token material (protection.html#crypto-core) ---

TOKEN_NBYTES = 32  # secrets.token_urlsafe(32) → 256 bits
# Messenger link code: short and human-typeable (relayed web → human → bot DM),
# not a long URL token. Crockford-ish alphabet drops look-alikes (0/O, 1/I/L).
LINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LINK_CODE_LENGTH = 8  # 32**8 ≈ 1.1e12 — ample under the 5-attempt/15-min guard
LINK_CODE_GROUP = 4  # display grouping: "K7P2-9XQ4"
API_KEY_PREFIX = "ach_"
API_KEY_DISPLAY_PREFIX_LEN = 8  # "ach_" + 4 chars shown in lists
API_KEY_RATE_LIMIT_RPM = 60  # per key (authentication.html#api-keys)
API_KEY_EXPIRY_CHOICES = frozenset({30, 90, 365})  # days; None → never expires
API_KEY_NAME_MAX_LEN = 80  # optional owner-facing label

# --- Brute-force barrier (protection.html#brute-force) ---

BRUTE_IP_LIMIT = 20
BRUTE_IP_WINDOW = timedelta(minutes=15)
BRUTE_ACCOUNT_FREE_ATTEMPTS = 2
BRUTE_ACCOUNT_BASE_DELAY = timedelta(seconds=1)
BRUTE_ACCOUNT_MAX_DELAY = timedelta(seconds=30)
BRUTE_ALERT_THRESHOLD = 10
LINK_CODE_MAX_ATTEMPTS = 5  # wrong codes per chat before the barrier drops
LINK_PLATFORMS = frozenset({"slack", "telegram", "mattermost"})
FORGOT_RESEND_LIMIT = 3  # reset-link requests per email per window
FORGOT_RESEND_WINDOW = timedelta(minutes=15)
# The per-email window alone is bypassed by spraying unique addresses — each
# request would land a worker job; the IP window caps that amplification.
FORGOT_IP_LIMIT = 20
FORGOT_IP_WINDOW = timedelta(minutes=15)

# --- Error codes (data-model.html#api-errors; the generic ones live in api/problems.py) ---

CODE_INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
CODE_TOKEN_EXPIRED = "TOKEN_EXPIRED"  # noqa: S105 — error code, not a secret
CODE_TOKEN_INVALID = "TOKEN_INVALID"  # noqa: S105 — error code, not a secret
CODE_PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"  # noqa: S105 — error code
CODE_ACCOUNT_DEACTIVATED = "ACCOUNT_DEACTIVATED"
CODE_LAST_OWNER_PROTECTED = "LAST_OWNER_PROTECTED"
CODE_SETUP_UNAVAILABLE = "SETUP_UNAVAILABLE"
CODE_SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
CODE_SMTP_NOT_CONFIGURED = "SMTP_NOT_CONFIGURED"
CODE_ALREADY_LINKED = "ALREADY_LINKED"
CODE_INVITE_EXPIRED = "INVITE_EXPIRED"
CODE_INVITE_USED = "INVITE_USED"
CODE_EMAIL_TAKEN = "EMAIL_TAKEN"
CODE_RESET_EXPIRED = "RESET_EXPIRED"
CODE_LINK_EXPIRED = "LINK_EXPIRED"
