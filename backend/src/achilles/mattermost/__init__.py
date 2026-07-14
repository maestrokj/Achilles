"""Mattermost surface: the DM assistant for a self-hosted Mattermost server.

Third implementation of the shared messenger core. The transport is the odd
one out: Mattermost has no HTTP push for DMs (outgoing webhooks are
public-channels-only), so a singleton WebSocket listener in the scheduler
receives `posted` events and enqueues jobs — nothing is exposed publicly and
no webhook secret exists. Replies go back over REST, threaded by root_id.
"""
