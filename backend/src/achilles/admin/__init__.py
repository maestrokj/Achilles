"""Admin Panel backend: the thin facade over platform_settings.

The module owns no domain data (admin-panel/index.html — truth lives in the
domain modules); it owns the org-wide configuration contract: the settings
editor, the public branding read, the maintenance switch and, later, the
dashboard aggregate. Dependencies point outward (admin -> KS/auth); the one
inward import is the maintenance switch (auth.dependencies and main consult
achilles.admin.maintenance).
"""
