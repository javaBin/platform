#!/usr/bin/env python3
"""Fail an app's plan when it would put an ALB listener rule on a taken priority.

Listener rule priorities are unique per listener, and AWS only says so at
apply time (CreateRule: PriorityInUse). This reads the saved plan, takes every
aws_lb_listener_rule it creates or moves, and checks the priority against the
live rules on that listener (read-only elbv2:DescribeRules, which team roles
have). A rule the plan deletes or moves in the same run frees its old
priority.

Usage (tf-plan, after run-plan.sh, with the team credentials):
  python3 scripts/check-alb-priorities.py <tf_root>

Exits 1 with a ::error annotation on a conflict, and writes the conflicts to
alb-priority-errors.txt in the current directory, which post-plan-comment.sh
puts at the top of the PR comment (tf-plan posts the comment before it fails
the job). If the plan or the live rules cannot be read it only warns: apply
still catches a conflict, just later.
"""

import json
import os
import subprocess
import sys

# Keep in step with generate-modules.py (explicit routing.priority range, and
# the number of login rules that sit directly below the host rule).
EXPLICIT_PRIORITY_MAX = 999
LOGIN_MAX_RULES = 10

# Read by post-plan-comment.sh (both run from the app repo root).
ERRORS_FILE = "alb-priority-errors.txt"


def planned_rules(plan):
    """(wanted, freed): rules the plan creates or re-prioritises, and ARNs it deletes.

    wanted: [{address, listener_arn, priority}]
    """
    wanted, freed = [], set()
    for rc in plan.get("resource_changes") or []:
        if rc.get("type") != "aws_lb_listener_rule":
            continue
        change = rc.get("change") or {}
        actions = change.get("actions") or []
        before, after = change.get("before") or {}, change.get("after") or {}
        # delete-then-create (the default replace) frees the old priority
        # first; create-before-destroy does not.
        if actions and actions[0] == "delete" and before.get("arn"):
            freed.add(before["arn"])
        if "create" not in actions and "update" not in actions:
            continue
        if "update" in actions and before.get("priority") == after.get("priority"):
            continue
        if "update" in actions and before.get("arn"):
            freed.add(before["arn"])  # moves off its old priority in this run
        if not isinstance(after.get("priority"), int) or not after.get("listener_arn"):
            continue  # unknown until apply
        wanted.append({
            "address": rc.get("address", "aws_lb_listener_rule"),
            "listener_arn": after["listener_arn"],
            "priority": after["priority"],
        })
    return wanted, freed


def live_priorities(rules):
    """{priority: rule} for a describe-rules response (the default rule has none)."""
    return {int(r["Priority"]): r for r in rules if str(r.get("Priority", "")).isdigit()}


def rule_hosts(rule):
    hosts = []
    for cond in rule.get("Conditions") or []:
        if cond.get("Field") == "host-header":
            hosts += (cond.get("HostHeaderConfig") or {}).get("Values") or cond.get("Values") or []
    return ", ".join(hosts) or rule.get("RuleArn", "another rule")


def suggest_priority(taken, room=LOGIN_MAX_RULES):
    """The lowest explicit priority with `room` free numbers below it, or None."""
    for p in range(room + 1, EXPLICIT_PRIORITY_MAX + 1):
        if all(q not in taken for q in range(p - room, p + 1)):
            return p
    return None


def conflicts(wanted, freed, live):
    """Error messages for wanted rules whose priority a live rule holds."""
    errors = []
    taken = {p for p, r in live.items() if r.get("RuleArn") not in freed}
    for w in wanted:
        holder = live.get(w["priority"])
        if not holder or holder.get("RuleArn") in freed:
            continue
        free = suggest_priority(taken)
        hint = (f"set routing.priority in app.yaml to a free number, e.g. {free} (it leaves "
                f"{LOGIN_MAX_RULES} free below it for routing.login rules)") if free else \
            "set routing.priority in app.yaml to a free number 1-999"
        errors.append(
            f"{w['address']}: ALB listener priority {w['priority']} is already used by the rule for "
            f"{rule_hosts(holder)}, so apply would fail with PriorityInUse. Login rules take the "
            f"priorities directly below routing.priority. To fix: {hint}, or remove routing.priority "
            "to get one derived from team and name."
        )
    return errors


def _run_json(cmd, cwd=None):
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def write_errors(errors, path=ERRORS_FILE):
    """Leave the conflicts for the PR comment; remove a stale file when there are none."""
    if errors:
        with open(path, "w") as f:
            f.writelines(f"- {e}\n" for e in errors)
    elif os.path.exists(path):
        os.remove(path)


def main():
    tf_root = sys.argv[1] if len(sys.argv) > 1 else "terraform"
    write_errors([])
    try:
        plan = _run_json(["terraform", "show", "-json", "tfplan"], cwd=tf_root)
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        print(f"::warning::check-alb-priorities: cannot read the plan ({e}); skipping")
        return 0

    wanted, freed = planned_rules(plan)
    errors = []
    for listener in sorted({w["listener_arn"] for w in wanted}):
        try:
            rules = _run_json(["aws", "elbv2", "describe-rules", "--listener-arn", listener, "--output", "json"])
        except (subprocess.CalledProcessError, OSError, ValueError) as e:
            detail = getattr(e, "stderr", "") or e
            print(f"::warning::check-alb-priorities: cannot list rules on {listener} ({detail}); skipping")
            continue
        live = live_priorities(rules.get("Rules") or [])
        errors += conflicts([w for w in wanted if w["listener_arn"] == listener], freed, live)

    for error in errors:
        print(f"::error file=app.yaml::{error}")
        print(f"ERROR: {error}", file=sys.stderr)
    write_errors(errors)
    if not errors and wanted:
        print(f"ALB priorities free: {', '.join(str(w['priority']) for w in wanted)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
