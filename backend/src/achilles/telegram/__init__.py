"""Telegram — the inbound chat-bot surface (telegram/index.html).

Slack's twin: v1 is DM-only, the webhook acks immediately and the work runs on
the interactive lane. Two things differ from Slack — the conversation is cut by
the ``/new`` command (Telegram DMs have no threads), and there is no email, so
the only way to link an account is the one-time code. Identity enters through
Auth's identity_mapping (source='telegram'). The shared core is the
telegram_settings singleton — a mirror of the Email SMTP pattern, differing in
one thing: Achilles generates the webhook secret itself at setWebhook.
"""
