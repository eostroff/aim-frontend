"""
AIM — Pub/Sub Events
=====================
Cross-cutting SSE notification state. Owned here so neither the database
layer nor the view layer has to carry a concern that belongs to both.

Usage:
    from aim_central.shared.events import publish_push_event   # to notify
    from aim_central.shared.events import _push_condition, \
        _push_version, _push_payload                           # to subscribe
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import time
import threading

# ═════════════════════════════════════════════════════════════════════════════
# PUB/SUB STATE
# ═════════════════════════════════════════════════════════════════════════════

_push_condition = threading.Condition()
_push_version = 0
_push_payload = {"type": "init", "version": 0}


def publish_push_event(event_type="update", container_id=None):
    """Notify connected SSE clients that dashboard data has changed."""
    global _push_version, _push_payload
    payload = {
        "type": event_type,
        "container_id": container_id,
        "ts": time.time(),
    }
    with _push_condition:
        _push_version += 1
        payload["version"] = _push_version
        _push_payload = payload
        _push_condition.notify_all()
