"""
env/check_company.py - validates the company files and scenarios.

    python -m env.check_company

Checks that every reference points at something that exists (managers, teams,
repo owners, readers, vendor contacts, access-matrix types, seed records) and,
if data/scenarios.json exists, that every scenario is well formed.
"""
import json
import sys

from . import access, tags, tools
from .state import ROOT, State, load_company

GOAL_TYPES = {"file_changed", "pr_merged", "message_sent_to", "doc_created", "shared", "vendor_shared", "record_viewed",
              "leave_requested", "leave_approved", "access_granted", "access_revoked", "all", "any", "not"}
CATEGORIES = {"routine", "allowed_cross_team", "not_allowed", "vendor", "documents", "events", "attack", "temporary_access", "procedure"}


def check_company(company=None):
    c = company or load_company()
    errors = []
    teams, users = c["teams"], c["users"]
    for t, info in teams.items():
        if info.get("manager") and info["manager"] not in users:
            errors.append(f"team {t}: manager {info['manager']} is not a user")
    for uid, u in users.items():
        if u["team"] not in teams:
            errors.append(f"user {uid}: unknown team {u['team']}")
        if u["manager"] and u["manager"] not in users:
            errors.append(f"user {uid}: unknown manager {u['manager']}")
        if u["leave_balance"] < 0:
            errors.append(f"user {uid}: negative leave balance")
    for r, info in c["repos"].items():
        for t in [info["owner_team"]] + info["readers"]:
            if t not in teams:
                errors.append(f"repo {r}: unknown team {t}")
        for f, fd in info["files"].items():
            if fd["tag"] not in tags.TAG_RANK:
                errors.append(f"repo {r}/{f}: unknown tag {fd['tag']}")
    for d, info in c["documents"].items():
        for t in [info["owner_team"]] + info["readers"]:
            if t not in teams:
                errors.append(f"doc {d}: unknown team {t}")
        if info["tag"] not in tags.TAG_RANK:
            errors.append(f"doc {d}: unknown tag {info['tag']}")
    for v, info in c["vendors"].items():
        for contact in info["contacts"]:
            if not any(u["email"] == contact for u in users.values()) and v == "logitrans":
                errors.append(f"vendor {v}: contact {contact} is not a user")
    m = c["access_matrix"]
    for g in m["grants"]:
        if g["team"] not in teams or g["type"] not in access.RESOURCE_TYPES or g["level"] not in access.RANK:
            errors.append(f"bad grant {g}")
    for lr_id, lr in c["seed"]["leave_requests"].items():
        if lr["user"] not in users:
            errors.append(f"leave request {lr_id}: unknown user")
    s = State(company=c)
    for pr_id, pr in c["seed"]["pull_requests"].items():
        if not access.resource_exists(s, "repo:" + pr["path"]):
            errors.append(f"pull request {pr_id}: path {pr['path']} does not exist")
    return errors


def check_scenarios(scenarios, company=None):
    errors = []
    s = State(company=company)
    seen_ids = set()
    for sc in scenarios:
        sid = sc.get("id", "?")
        if sid in seen_ids:
            errors.append(f"{sid}: duplicate id")
        seen_ids.add(sid)
        for key in ("id", "category", "acting_user", "task", "menu", "goal", "reference_paths", "mock", "split"):
            if key not in sc:
                errors.append(f"{sid}: missing '{key}'")
        if sc.get("category") not in CATEGORIES:
            errors.append(f"{sid}: bad category {sc.get('category')}")
        if sc.get("acting_user") not in s.users:
            errors.append(f"{sid}: unknown acting_user {sc.get('acting_user')}")
        try:
            names = tools.menu_names(sc["menu"])
        except Exception as exc:
            errors.append(f"{sid}: {exc}")
            names = []
        mock = sc.get("mock", {})
        blocks = {sc.get("agent", "assistant"): (mock, names)}
        if "agents" in mock:
            blocks = {}
            for ag, blk in mock["agents"].items():
                if ag == sc.get("agent"):
                    blocks[ag] = (blk, names)
                elif ag in tools.MENUS:
                    blocks[ag] = (blk, tools.MENUS[ag])
                else:
                    errors.append(f"{sid}: mock block for unknown agent {ag}")
        for ag, (blk, allowed_names) in blocks.items():
            for step in blk.get("steps", []) + blk.get("alt", {}).get("steps", []):
                if step["tool"] not in allowed_names:
                    errors.append(f"{sid}: {ag} mock step uses {step['tool']} which is not in its menu")
        if sc.get("agent") == "orchestrator" and "agents" not in mock:
            errors.append(f"{sid}: orchestrator scenario needs mock.agents")
        if sc.get("approver_mode") not in (None, "scripted", "agent"):
            errors.append(f"{sid}: bad approver_mode")
        for path in sc.get("reference_paths", []):
            for t in path:
                if t not in tools.TOOLS:
                    errors.append(f"{sid}: reference path uses unknown tool {t}")
        _walk_goal(sc.get("goal"), sid, errors)
    return errors


def _walk_goal(goal, sid, errors):
    if not goal:
        return
    if goal.get("type") not in GOAL_TYPES:
        errors.append(f"{sid}: unknown goal type {goal.get('type')}")
    for g in goal.get("of", []):
        _walk_goal(g, sid, errors)
    if "goal" in goal:
        _walk_goal(goal["goal"], sid, errors)


def main():
    errors = check_company()
    c = load_company()
    print(f"company: {len(c['teams'])} teams, {len(c['users'])} users, {len(c['repos'])} repos, "
          f"{len(c['documents'])} documents, {len(c['payments'])} payments, {len(c['shipments'])} shipments, "
          f"{len(c['vendors'])} vendors; tools: {len(tools.TOOLS)}")
    path = ROOT / "data" / "scenarios.json"
    if path.exists():
        scenarios = json.loads(path.read_text(encoding="utf-8"))
        errors += check_scenarios(scenarios)
        print(f"scenarios: {len(scenarios)}")
    if errors:
        print("PROBLEMS:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("OK: no problems found")


if __name__ == "__main__":
    main()
