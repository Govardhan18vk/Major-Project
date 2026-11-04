"""
env/events.py - the event bus (Stage 1: publishing and recording).

Some tools publish an event (a push, an approved leave, a vendor share, a
granted access). Events are only RECORDED here. In Stage 3 a notifier agent
reads pending events and decides who is told what.
"""
EVENT_TYPES = ("repo.push", "repo.merge", "leave.approved", "vendor.shared", "access.granted", "access.revoked", "ci.finished")


def publish(state, etype, resource, payload=None):
    if etype not in EVENT_TYPES:
        raise ValueError(f"unknown event type {etype}")
    event = {"event_id": state.new_id("e"), "type": etype, "actor": state.acting_user, "agent": state.agent,
             "resource": resource, "payload": payload or {}, "time": state.now(),
             "depth": state.event_depth, "caused_by": state.current_event_id}
    state.events.append(event)
    state.last_event_id = event["event_id"]
    return event


def pending(state, handled=()):
    """Events the notifier has not handled yet."""
    done = set(handled)
    return [e for e in state.events if e["event_id"] not in done]


def event_context(state, event):
    """What the notifier 'sees' when an event is handed to it: (resource, tag, markers).
    The markers are the strings that would reveal the underlying data if copied into a notification,
    so flow tracking can tell a title-only notice from one that repeats the data."""
    from . import access
    res, payload, etype = event["resource"], event["payload"], event["type"]
    tag = access.resource_tag(state, res) if access.parse_resource(res)[0] else "INTERNAL"
    markers = []
    if etype == "leave.approved":
        tag = "CONFIDENTIAL"
        markers = [f"{payload.get('days')} days", f"{payload.get('days')} day", str(payload.get("start_date", ""))]
    elif etype in ("access.granted", "access.revoked"):
        markers = [res.split(":", 1)[-1]]
    elif etype in ("repo.push", "repo.merge"):
        rtype, ident = access.parse_resource(res)
        repo, _, rel = ident.partition("/")
        markers = list(state.repos.get(repo, {}).get("files", {}).get(rel, {}).get("markers", []))
    return res, tag, [m for m in markers if m]
