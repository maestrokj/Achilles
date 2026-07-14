"""Slack — the inbound chat-bot surface (slack/index.html).

v1 is DM-only: a thread is a conversation, the webhook acks < 3 s and the work
runs on the interactive lane. Identity enters through Auth's identity_mapping
(source='slack'); workspace membership grants nothing. The shared core is the
slack_settings singleton — a mirror of the Email SMTP pattern.
"""
