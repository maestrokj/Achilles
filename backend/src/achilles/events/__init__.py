"""Events — the platform push channel (api/_workzone/live-updates.html).

One multiplexed SSE stream per tab carries every push concern: board nudges
("this board changed — refetch it") and the notification bell's counter. The
stream never carries domain data; the client answers a nudge with an ordinary
REST refetch, so auth, ACL and schemas stay on REST.
"""
