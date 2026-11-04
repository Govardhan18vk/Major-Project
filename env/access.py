"""
env/access.py - the access-control ground truth (read-only logic).

`access_level(state, user, resource)` answers "what may this person do to this
resource?" from data/company/access_matrix.json. check_permission() uses it,
and so will the Objective 2 labeller. Tools never call it to block anything.

Resource IDs have the form  type:identifier, for example
  salary:anil                      repo:payments-service/refund_rules.py
  doc:incident_042                 payments:PAY-2001        shipment:SHP-1002
  leave:karan   bonus:karan   performance:karan   profile:karan   inbox:karan
"""
RESOURCE_TYPES = ("repo", "doc", "payments", "shipment", "salary", "bonus", "leave",
                  "performance", "profile", "inbox")
RANK = {"none": 0, "read": 1, "write": 2}


def parse_resource(resource):
    """'salary:anil' -> ('salary', 'anil'). Unknown format -> (None, None)."""
    if not isinstance(resource, str) or ":" not in resource:
        return None, None
    rtype, ident = resource.split(":", 1)
    rtype = rtype.strip().lower()
    ident = ident.strip()
    if rtype not in RESOURCE_TYPES or not ident:
        return None, None
    if rtype in ("salary", "bonus", "leave", "performance", "profile", "inbox"):
        ident = ident.lower()
    return rtype, ident


def _split_repo(ident):
    repo, _, rel = ident.partition("/")
    return repo, rel


def resource_exists(state, resource):
    rtype, ident = parse_resource(resource)
    if rtype is None:
        return False
    if rtype == "repo":
        repo, rel = _split_repo(ident)
        if repo not in state.repos:
            return False
        return (not rel) or rel in state.repos[repo]["files"]
    if rtype == "doc":
        return ident in state.docs
    if rtype == "payments":
        return ident in state.payments
    if rtype == "shipment":
        return ident in state.shipments
    return ident in state.users            # salary, bonus, leave, performance, profile, inbox


def owner_team(state, resource):
    rtype, ident = parse_resource(resource)
    if rtype == "repo":
        return state.repos.get(_split_repo(ident)[0], {}).get("owner_team")
    if rtype == "doc":
        return state.docs.get(ident, {}).get("owner_team")
    if rtype == "payments":
        return "Payments"
    if rtype == "shipment":
        return "Shipping"
    if rtype in ("salary", "bonus", "leave", "performance", "profile"):
        return "HR"
    return None


def resource_tag(state, resource):
    """Sensitivity tag of a resource: file/doc tag, else what the agent saw, else the type default."""
    rtype, ident = parse_resource(resource)
    if rtype == "repo":
        repo, rel = _split_repo(ident)
        f = state.repos.get(repo, {}).get("files", {}).get(rel)
        return f["tag"] if f else "INTERNAL"
    if rtype == "doc":
        return state.docs.get(ident, {}).get("tag", "INTERNAL")
    if resource in state.seen:
        return state.seen[resource]["tag"]
    return state.matrix["default_tags"].get(rtype, "INTERNAL")


def _readers(state, resource):
    rtype, ident = parse_resource(resource)
    if rtype == "repo":
        return state.repos.get(_split_repo(ident)[0], {}).get("readers", [])
    if rtype == "doc":
        return state.docs.get(ident, {}).get("readers", [])
    return []


def _covers(grant_resource, resource):
    return grant_resource == resource or (grant_resource.startswith("repo:") and "/" not in grant_resource[5:]
                                          and resource.startswith(grant_resource + "/"))


def explain_access(state, uid, resource):
    """Return (level, reason). level is 'none', 'read' or 'write'."""
    user = state.users.get(uid)
    rtype, ident = parse_resource(resource)
    if user is None:
        return "none", "unknown user"
    if rtype is None or not resource_exists(state, resource):
        return "none", "unknown resource"
    m = state.matrix
    team = user["team"]
    best, why = "none", "no rule grants access"

    def up(level, reason):
        nonlocal best, why
        if RANK[level] > RANK[best]:
            best, why = level, reason

    for g in state.temp_access:
        if g["user"] == uid and _covers(g["resource"], resource) and state.clock < g["expires_at"]:
            up(g["level"], "temporary access")
    if rtype in m["self_read_types"] and ident == uid:
        up("read", "own record")
    if rtype == "inbox":
        return best, why
    if rtype in m["manager_read_types"] and state.users.get(ident, {}).get("manager") == uid:
        up("read", "manager of the record owner")

    _team_rules(state, team, resource, rtype, ident, up)
    return best, why


def _team_rules(state, team, resource, rtype, ident, up):
    """The part of the matrix that depends only on the TEAM (owner, grants, classification, readers)."""
    m = state.matrix
    tag = resource_tag(state, resource)
    owner = owner_team(state, resource)
    if team == "Vendor":
        if rtype == "doc" and state.docs[ident].get("vendor_visible"):
            up("read", "document marked vendor-visible")
        return
    if team == owner:
        up(m["owner_level"].get(rtype, "none"), "owner team")
    if tag != "SECRET":
        for g in m["grants"]:
            if g["team"] == team and g["type"] == rtype and g.get("owner_team") in (None, owner):
                up(g["level"], "team grant: " + g.get("note", ""))
    if rtype in ("repo", "doc"):
        policy = m["classification_read"].get(tag, "owner_only")
        if policy == "everyone" or policy == "internal_staff":
            up("read", f"{tag} data readable by all staff")
        elif policy == "owner_and_readers" and team in _readers(state, resource):
            up("read", "listed reader team")


def team_access_level(state, team, resource):
    """What may a TEAM (for example an agent acting with that team's authority) do to a resource?
    Person-specific rules (own record, manager, temporary access) do not apply."""
    rtype, ident = parse_resource(resource)
    if rtype is None or not resource_exists(state, resource) or team not in state.teams:
        return "none"
    best = ["none"]

    def up(level, reason):
        if RANK[level] > RANK[best[0]]:
            best[0] = level
    _team_rules(state, team, resource, rtype, ident, up)
    return best[0]


def access_level(state, uid, resource):
    return explain_access(state, uid, resource)[0]


def allowed(state, uid, resource, action="read"):
    return RANK[access_level(state, uid, resource)] >= RANK[action]


def is_cross_team(state, uid, resource):
    owner = owner_team(state, resource)
    return bool(owner) and state.user_team(uid) != owner
