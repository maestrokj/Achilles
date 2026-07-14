"""Shared core of the messenger surfaces (Slack, Telegram, Mattermost).

One inbound DM walks the same road on every platform: identity → link code or
answer → post-back. The road lives here once; each surface contributes only
what its platform dictates — transport, auth, conversation boundary, markup.
"""
