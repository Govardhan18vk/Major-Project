"""
env/state.py - the simulated ShopSphere company at one moment in time.

A fresh State is built for EVERY run from the JSON files in data/company/
(plus the scenario's own seeds), so runs never affect each other. Nothing
here enforces access control: in open mode (data collection) tools only
change the State and leave a trace; the labeller judges the trace later.
"""
import copy
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMPANY_DIR = ROOT / "data" / "company"
COMPANY_FILES = ["teams", "users", "repos", "documents", "payments", "shipments", "vendors", "access_matrix", "seed"]
COMPANY_DOMAIN = "shopsphere.local"
DEFAULT_START = "2026-11-02T09:00:00"
MINUTES_PER_STEP = 5

# Team agents act with their own team's authority (the "agent_authorized" fact in the change log).
# The requester's own authority is recorded separately as "authorized". A gap between the two is the
# confused-deputy case. assistant, orchestrator, vendor_agent, notifier and approver have no team of their own.
AGENT_TEAM = {"payments_agent": "Payments", "shipping_agent": "Shipping", "hr_agent": "HR",
              "risk_agent": "Risk", "platform_agent": "Platform"}

_CACHE = {}


def company_dir():
    return Path(os.environ.get("SAFETASK_COMPANY_DIR", DEFAULT_COMPANY_DIR))


def load_company(directory=None):
    """Read the company JSON files once, then hand out deep copies."""
    d = Path(directory) if directory else company_dir()
    key = str(d)
    if key not in _CACHE:
        data = {}
        for name in COMPANY_FILES:
            with open(d / f"{name}.json", encoding="utf-8") as fh:
                data[name] = json.load(fh)
        _CACHE[key] = data
    return copy.deepcopy(_CACHE[key])


class State:
    def __init__(self, scenario=None, company=None):
        sc = scenario or {}
        c = company or load_company()
        self.teams = c["teams"]
        self.users = c["users"]
        self.repos = c["repos"]
        self.docs = c["documents"]
        self.payments = c["payments"]
        self.shipments = c["shipments"]
        self.vendors = c["vendors"]
        self.matrix = c["access_matrix"]
        for did, d in sc.get("seed_docs", {}).items():       # documents a scenario brings with it (e.g. a hostile vendor file)
            self.docs[did] = {"readers": [], "origin": "internal", "vendor_visible": False, "kind": "document",
                              "markers": [], **d}
        self.leave_requests = c["seed"]["leave_requests"]
        self.pull_requests = c["seed"]["pull_requests"]

        # who is acting, and through which agent chain (Stage 3 fills the chain)
        self.acting_user = sc.get("acting_user")
        self.requester = self.acting_user          # the person the whole run is for; never changes
        self.agent = sc.get("agent", "assistant")
        self.chain = [self.acting_user, self.agent] if self.acting_user else []

        # multi-agent runtime (filled by agents/orchestrator.py): callables for delegate and approval
        self.runtime = {}
        self.delegation_depth = 0
        self.current_step = None
        self.pending_approval = None      # set while an approver agent is deciding
        self.approval_mode = sc.get("approver_mode", "scripted")

        # simulated clock
        self.clock = datetime.fromisoformat(sc.get("start_time", DEFAULT_START))

        # mutable traces
        self.inboxes = {uid: [] for uid in self.users}
        self.outbox = []          # every message/notification/share that left an agent's hands, with flow info
        self.events = []
        self.changes = []         # the state diff: one dict per state-changing action
        self.approvals = []
        self.human_log = []
        self.temp_access = []     # {"user","resource","level","expires_at","granted_by"}
        self.seen = {}            # resource -> {"tag", "markers"}  (what the agent has read in this run)
        self.audit = []
        self._counters = {}
        self.event_depth = 0
        self.current_event_id = None

        # scenario configuration
        self.faults = list(sc.get("faults", []))
        self.fault_counts = {}
        self.approver_cfg = dict(sc.get("approver", {"default": "no"}))

        # per-call scratch fields, reset by the loop before each tool call
        self.reset_trace()

        for m in sc.get("seed_messages", []):
            self.deliver(m["to"], {"from": m["from"], "subject": m["subject"], "body": m["body"],
                                   "origin": m.get("origin", "internal"), "seeded": True})

    # ------------------------------------------------------------------ per-call trace
    def reset_trace(self):
        self.last_read = []        # [{"resource","tag"}] returned to the agent by the tool
        self.last_flow = None      # flow dict if the tool sent something out
        self.last_untrusted = False
        self.last_fault = None
        self.last_event_id = None

    # ------------------------------------------------------------------ small helpers
    def new_id(self, prefix):
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}_{self._counters[prefix]}"

    def advance(self, minutes=MINUTES_PER_STEP):
        self.clock += timedelta(minutes=minutes)

    def now(self):
        return self.clock.isoformat(timespec="minutes")

    def user_team(self, uid):
        return self.users.get(uid, {}).get("team")

    def resolve_user(self, query):
        """Accept a user id, full name, first name, email or role ('hr manager')."""
        if query is None:
            return None
        q = str(query).strip().lower()
        if q in self.users:
            return q
        norm = lambda s: re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()
        nq = norm(q)
        for uid, u in self.users.items():
            if q == u["email"].lower() or nq == norm(u["name"]) or nq == norm(u["role"]):
                return uid
        for uid, u in self.users.items():       # first name only, e.g. "Meera"
            if nq == norm(u["name"]).split()[0]:
                return uid
        return None

    def team_members(self, team):
        return [uid for uid, u in self.users.items() if u["team"] == team]

    def deliver(self, to_uid, msg):
        if to_uid not in self.inboxes:
            return None
        m = dict(msg)
        m["id"] = self.new_id("msg")
        m["time"] = self.now()
        m.setdefault("origin", "internal")
        self.inboxes[to_uid].append(m)
        return m["id"]

    def resolve_recipient(self, to):
        """Classify where a message goes. kind is one of: internal_user, team_channel,
        wide_channel, vendor, lookalike, external, unknown."""
        t = str(to).strip()
        low = t.lower()
        if low in ("#all-staff", "#all", "#general"):
            return {"kind": "wide_channel", "ids": [u for u, x in self.users.items() if x["team"] != "Vendor"],
                    "address": low, "vendor": None}
        for team, info in self.teams.items():
            if info.get("channel") and low == info["channel"].lower():
                return {"kind": "team_channel", "ids": self.team_members(team), "address": low, "vendor": None, "team": team}
        uid = self.resolve_user(t)
        if uid:
            u = self.users[uid]
            if u["team"] == "Vendor":
                vid = next((v for v, x in self.vendors.items() if u["email"].endswith("@" + x["domain"])), None)
                return {"kind": "vendor", "ids": [uid], "address": u["email"], "vendor": vid}
            return {"kind": "internal_user", "ids": [uid], "address": u["email"], "vendor": None}
        if "@" in t:
            domain = low.split("@")[-1]
            if domain == COMPANY_DOMAIN:
                return {"kind": "unknown", "ids": [], "address": low, "vendor": None}
            for vid, v in self.vendors.items():
                if domain == v["domain"]:
                    return {"kind": "vendor", "ids": [], "address": low, "vendor": vid}
                if domain in v["lookalike_domains"]:
                    return {"kind": "lookalike", "ids": [], "address": low, "vendor": vid}
            if "shopsphere" in domain:
                return {"kind": "lookalike", "ids": [], "address": low, "vendor": None}
            return {"kind": "external", "ids": [], "address": low, "vendor": None}
        return {"kind": "unknown", "ids": [], "address": low, "vendor": None}

    # ------------------------------------------------------------------ change log (state diff)
    def record_change(self, kind, **detail):
        entry = {"kind": kind, "actor": self.acting_user, "agent": self.agent, "time": self.now(),
                 "step": getattr(self, "current_step", None), **detail}
        team = AGENT_TEAM.get(self.agent)
        if team and "resource" in detail:
            from . import access
            entry["agent_team"] = team
            entry["agent_authorized"] = access.RANK[access.team_access_level(self, team, detail["resource"])] >= access.RANK["write" if kind in ("commit", "merge") else "read"]
        self.changes.append(entry)
        return entry

    def state_diff(self):
        """Readable one-line summaries of everything that changed in this run."""
        out = []
        for c in self.changes:
            k = c["kind"]
            if k in ("commit", "merge"):
                out.append(f"{k}:{c['resource']}")
            elif k == "message_sent":
                out.append(f"message_sent:{c['to']}[{c['channel']}]")
            elif k == "shared":
                out.append(f"shared:{c['what']}->{c['to']}[{c['channel']}]")
            elif k == "vendor_share":
                out.append(f"vendor_share:{c['vendor']}:{c['source']}")
            elif k == "doc_created":
                out.append(f"doc_created:{c['doc_id']}[{c['tag']}]")
            elif k in ("access_granted", "access_revoked"):
                out.append(f"{k}:{c['user']}:{c['resource']}")
            elif k in ("leave_requested", "leave_approved"):
                out.append(f"{k}:{c['request_id']}")
            elif k == "approval":
                out.append(f"approval:{c['action']}:{c['decision']}")
            else:
                out.append(k)
        return out
