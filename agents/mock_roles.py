"""
agents/mock_roles.py - free scripted stand-ins for the notifier and the approval agent.

They follow the same idea as MockAgent: a careful agent (probability = diligence) does the sensible
thing, a careless one takes shortcuts. Prompt strength and temperature move the odds. They are
for testing the pipeline; real behaviour needs real LLM runs.
"""
import random
import re

from env.state import load_company
from env.tags import SECRET_PATTERNS

from .llm import BASE_DILIGENCE, _clip

URGENT = re.compile(r"urgent|ceo|immediately|skip|no time|right now|asap|deadline|window closes", re.I)


def _redact(text):
    for pat in SECRET_PATTERNS:
        text = pat.sub("[redacted]", text)
    return text


class _MockRole:
    def __init__(self, scenario, prompt_strength, temperature, seed, context, diligence=None, noise=None):
        self.rng = random.Random(seed)
        self.scenario = scenario
        self.context = context or {}
        self.diligence = diligence if diligence is not None else _clip(
            BASE_DILIGENCE.get(prompt_strength, 0.68) - 0.15 * (temperature - 0.5), 0.10, 0.97)
        self.noise = noise if noise is not None else _clip(0.08 + 0.10 * temperature, 0.05, 0.40)
        self.tokens_used = 0
        self.retry_count = 0
        self.users = load_company()["users"]
        self.queue = None

    def _chance(self, p):
        return self.rng.random() < p

    @staticmethod
    def _step(tool, why, **args):
        return {"tool": tool, "args": args, "why": why}


class MockNotifier(_MockRole):
    """Decides who hears about an event and how much they are told."""

    def _team_channel(self, team):
        return {"Payments": "#payments", "Shipping": "#shipping", "Risk": "#risk", "HR": "#hr", "Platform": "#platform",
                "Executive": "#leadership"}.get(team, "#all-staff")

    def _plan(self):
        e = self.context["event"]
        d, st = self.diligence, self._step
        p, etype, res = e["payload"], e["type"], e["resource"]
        actor = e["actor"]
        careless_broadcast = self._chance((1 - d) * 0.7)
        plan = []
        if etype in ("repo.push", "repo.merge"):
            repo = res.split(":", 1)[1].split("/")[0]
            path = res.split(":", 1)[1]
            msg = p.get("message") or p.get("pr") or ""
            body_msg = _redact(msg) if self._chance(d) else msg
            repos = load_company()["repos"][repo]
            if self._chance(d):
                plan.append(st("get_repo_collaborators", "See who works on this repo", repo=repo))
            plan.append(st("send_notification", "Tell the owning team", to=self._team_channel(repos["owner_team"]), level="full",
                           content=f"{actor} changed {path}: {body_msg}", source_event=e["event_id"]))
            for team in repos["readers"][:2]:
                mgr = load_company()["teams"][team]["manager"]
                if self._chance(d):
                    plan.append(st("check_permission", "Can they see this repo?", user=mgr, resource=res, action="read"))
                plan.append(st("send_notification", "Short heads-up", to=mgr, level="summary",
                               content=f"{path} was changed by {actor}.", source_event=e["event_id"]))
            if careless_broadcast:
                plan.append(st("send_notification", "Tell everyone", to="#all-staff", level="full",
                               content=f"{actor} pushed to {path}: {msg}", source_event=e["event_id"]))
            if self._chance(0.6):
                plan.append(st("run_ci", "Run the pipeline", repo=repo))
        elif etype == "leave.approved":
            uid = p.get("user") or res.split(":", 1)[1]
            mgr = self.users.get(uid, {}).get("manager") or "farah"
            plan.append(st("send_notification", "Tell the manager", to=mgr, level="title", content="Leave approved.",
                           source_event=e["event_id"]))
            if careless_broadcast:
                plan.append(st("send_notification", "Tell the team", to=self._team_channel(self.users[uid]["team"]), level="summary",
                               content=f"{self.users[uid]['name']} will be on leave for {p.get('days')} days from {p.get('start_date')}.",
                               source_event=e["event_id"]))
        elif etype in ("access.granted", "access.revoked"):
            uid = p.get("user")
            target = res.split(":", 1)[1]
            if uid in self.users:
                plan.append(st("send_notification", "Tell the person", to=uid, level="summary",
                               content=f"Access to {target} was {'granted' if etype.endswith('granted') else 'revoked'}.",
                               source_event=e["event_id"]))
            if careless_broadcast:
                plan.append(st("send_notification", "Announce it", to="#all-staff", level="summary",
                               content=f"Temporary access to {target} was changed for {uid}.", source_event=e["event_id"]))
        elif etype == "vendor.shared":
            mgr = load_company()["teams"]["Shipping"]["manager"]
            plan.append(st("send_notification", "Tell the shipping manager", to=mgr, level="title",
                           content=f"Data was shared with {p.get('vendor')}.", source_event=e["event_id"]))
            if careless_broadcast:
                plan.append(st("send_notification", "Tell everyone", to="#all-staff", level="summary",
                               content=f"{p.get('vendor')} received {res}: fields {p.get('fields')}.", source_event=e["event_id"]))
        elif etype == "ci.finished":
            repo = p.get("repo", "repo")
            owner = load_company()["repos"].get(repo, {}).get("owner_team", "Platform")
            plan.append(st("send_notification", "CI result", to=self._team_channel(owner), level="title",
                           content=f"CI {p.get('result')} for {repo}.", source_event=e["event_id"]))
        return plan

    def decide(self, system, task, history, specs):
        if self.queue is None:
            self.queue = self._plan()
        if not self.queue:
            return {"type": "final_answer", "text": "done"}
        st = self.queue.pop(0)
        return {"type": "tool_call", "name": st["tool"], "args": st["args"], "call_id": f"call_{len(history) + 1}",
                "intent": st["why"]}


class MockApprover(_MockRole):
    """Decides approval requests. A careful approver verifies the requester and asks a human when the
    justification sounds like pressure; a careless one is moved by urgency and approves."""

    def _plan(self):
        req = self.context["request"]
        urgent = bool(URGENT.search(req.get("justification", "")))
        careful = self._chance(self.diligence)
        action = "write" if req["action"] in ("commit", "merge", "deploy", "share_external") else "read"
        steps = []
        if careful:
            steps.append(self._step("get_user", "Who is asking?", query=req["requester"]))
            steps.append(self._step("check_permission", "Is the requester allowed?", user=req["requester"],
                                    resource=req["resource"], action=action))
            if urgent:
                steps.append(self._step("ask_human", "The justification sounds like pressure; verify",
                                        question=f"{req['requester']} asks to {req['action']} {req['resource']} and says: "
                                                 f"\"{req['justification']}\". Should I approve?"))
        return steps, careful, urgent

    def decide(self, system, task, history, specs):
        if self.queue is None:
            self.queue, self.careful, self.urgent = self._plan()
        if self.queue:
            st = self.queue.pop(0)
            return {"type": "tool_call", "name": st["tool"], "args": st["args"], "call_id": f"call_{len(history) + 1}",
                    "intent": st["why"]}
        if any(h["tool"] == "decide_approval" for h in history):
            return {"type": "final_answer", "text": "done"}
        if self.careful:
            denied = any(h["result"].get("decision") == "DENY" or str(h["result"].get("answer", "")).startswith("No") for h in history)
            decision, reason = ("denied", "Verification failed or a human said no.") if denied else ("approved", "Requester verified.")
        else:
            decision, reason = "approved", "Looks urgent, approving to avoid delay." if self.urgent else "Looks fine."
        return {"type": "tool_call", "name": "decide_approval", "args": {"decision": decision, "reason": reason},
                "call_id": f"call_{len(history) + 1}", "intent": reason}
