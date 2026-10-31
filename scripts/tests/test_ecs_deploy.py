"""Tests for ecs-deploy.sh — run: python3 -m unittest discover -s scripts/tests

The script runs against a fake `aws` that answers from a scenario file and
records every call, so the tests check which revision is registered, what the
service is updated to and how the rollout result is judged.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "ecs-deploy.sh")

ACCOUNT = "553637109631"
REPO = f"{ACCOUNT}.dkr.ecr.eu-central-1.amazonaws.com/cupcake-cupcake"
DIGEST = "sha256:" + "a" * 64
IMAGE = f"{REPO}@{DIGEST}"
TD_ARN = f"arn:aws:ecs:eu-central-1:{ACCOUNT}:task-definition/cupcake-cupcake"
SERVICE_ARN = f"arn:aws:ecs:eu-central-1:{ACCOUNT}:service/javabin-platform/cupcake-cupcake"

# Answers from the scenario file. describe-services pops `polls` after the
# first (pre-deploy) call, repeating the last one.
FAKE_AWS = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_AWS_STATE"]
with open(state_path) as f:
    st = json.load(f)
args = sys.argv[1:]
st["calls"].append(args)
def opt(name):
    return args[args.index(name) + 1] if name in args else None
def out(obj):
    print(json.dumps(obj))
cmd = args[1]
fail = st.get("fail", {}).get(cmd)
if fail and (fail != "tagged" or "tags" in json.load(open(opt("--cli-input-json")[7:]))):
    st["calls"][-1] = args + ["<failed>"]
    json.dump(st, open(state_path, "w"))
    sys.stderr.write("AccessDeniedException\n")
    sys.exit(254)
if cmd == "describe-services":
    if st["described"] == 0:
        svc = st["service"]
    else:
        polls = st["polls"]
        svc = polls[min(st["described"] - 1, len(polls) - 1)]
    st["described"] += 1
    out({"services": [svc] if svc else [], "failures": []})
elif cmd == "describe-task-definition":
    out({"taskDefinition": st["base"]})
elif cmd == "list-tags-for-resource":
    out({"tags": st["tags"].get(opt("--resource-arn"), [])})
elif cmd == "register-task-definition":
    body = json.load(open(opt("--cli-input-json")[7:]))
    st["registered"] = body
    out({"taskDefinition": dict(body, taskDefinitionArn=st["new_td"])})
elif cmd == "update-service":
    out({"service": {"deployments": [{"id": "ecs-svc/new", "status": "PRIMARY"},
                                     {"id": "ecs-svc/old", "status": "ACTIVE"}]}})
elif cmd == "list-tasks":
    out({"taskArns": [t["taskArn"] for t in st.get("stopped", [])]})
elif cmd == "describe-tasks":
    out({"tasks": st.get("stopped", [])})
else:
    sys.exit(f"fake aws: unexpected {args}")
json.dump(st, open(state_path, "w"))
'''


def base_td(revision=4, image=REPO):
    return {
        "taskDefinitionArn": f"{TD_ARN}:{revision}",
        "family": "cupcake-cupcake",
        "revision": revision,
        "status": "ACTIVE",
        "requiresAttributes": [{"name": "ecs.capability.execution-role-awslogs"}],
        "compatibilities": ["EC2", "FARGATE"],
        "registeredAt": "2026-10-30T10:50:37+01:00",
        "registeredBy": "arn:aws:sts::x:assumed-role/ci",
        "cpu": "256",
        "memory": "512",
        "containerDefinitions": [{
            "name": "cupcake",
            "image": image,
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": "/ecs/cupcake/cupcake", "awslogs-stream-prefix": "cupcake"}},
        }],
    }


def service(td_revision=4, deployments=None):
    return {
        "serviceArn": SERVICE_ARN,
        "status": "ACTIVE",
        "taskDefinition": f"{TD_ARN}:{td_revision}",
        "deployments": deployments or [{"id": "ecs-svc/old", "status": "PRIMARY", "rolloutState": "COMPLETED",
                                        "taskDefinition": f"{TD_ARN}:{td_revision}"}],
        "events": [{"createdAt": "2026-10-30T12:11:43+01:00",
                    "message": "(service cupcake-cupcake) deployment failed: tasks failed to start."}],
    }


def deployment(id_, status, state, td, reason=""):
    return {"id": id_, "status": status, "rolloutState": state, "taskDefinition": td,
            "rolloutStateReason": reason, "runningCount": 1, "desiredCount": 1}


NEW_TD = f"{TD_ARN}:5"
COMPLETED = service(deployments=[deployment("ecs-svc/new", "PRIMARY", "COMPLETED", NEW_TD)])
IN_PROGRESS = service(deployments=[deployment("ecs-svc/new", "PRIMARY", "IN_PROGRESS", NEW_TD),
                                   deployment("ecs-svc/old", "ACTIVE", "COMPLETED", f"{TD_ARN}:4")])
ROLLED_BACK = service(deployments=[
    deployment("ecs-svc/rollback", "PRIMARY", "IN_PROGRESS", f"{TD_ARN}:4"),
    deployment("ecs-svc/new", "ACTIVE", "FAILED", NEW_TD, "ECS deployment circuit breaker: tasks failed to start."),
])
STOPPED_TASK = {
    "taskArn": f"arn:aws:ecs:eu-central-1:{ACCOUNT}:task/javabin-platform/5ee10451fc2d",
    "taskDefinitionArn": NEW_TD,
    "stopCode": "EssentialContainerExited",
    "stoppedReason": "Essential container in task exited",
    "stoppedAt": "2026-10-30T12:11:40+01:00",
    "containers": [{"name": "cupcake", "exitCode": 1, "reason": "OutOfMemoryError: container killed"}],
}


def deploy(polls, *, base=None, svc=None, tags=None, fail=None, stopped=None, timeout="60"):
    """Run ecs-deploy.sh; return (exit code, output, final fake state)."""
    tmp = tempfile.mkdtemp()
    try:
        bin_dir = os.path.join(tmp, "bin")
        os.mkdir(bin_dir)
        with open(os.path.join(bin_dir, "aws"), "w") as f:
            f.write(FAKE_AWS)
        os.chmod(os.path.join(bin_dir, "aws"), 0o755)
        state_path = os.path.join(tmp, "state.json")
        state = {
            "calls": [], "described": 0, "service": svc if svc is not None else service(),
            "polls": polls, "base": base or base_td(), "new_td": NEW_TD,
            "tags": tags if tags is not None else {
                SERVICE_ARN: [{"key": "team", "value": "cupcake"}, {"key": "javabin:desired-count", "value": "2"}],
                f"{TD_ARN}:4": [{"key": "team", "value": "cupcake"}],
            },
            "fail": fail or {}, "stopped": stopped or [],
        }
        with open(state_path, "w") as f:
            json.dump(state, f)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_AWS_STATE=state_path,
                   CLUSTER="javabin-platform", SERVICE="cupcake-cupcake", IMAGE=IMAGE,
                   POLL_INTERVAL="0", DEPLOY_TIMEOUT=timeout)
        proc = subprocess.run(["sh", SCRIPT], env=env, capture_output=True, text=True)
        with open(state_path) as f:
            return proc.returncode, proc.stdout + proc.stderr, json.load(f)
    finally:
        shutil.rmtree(tmp)


def calls(state, cmd):
    return [c for c in state["calls"] if c[1] == cmd]


class RegisterPinnedRevisionTest(unittest.TestCase):
    def test_registers_copy_of_latest_revision_pinned_to_digest(self):
        code, out, st = deploy([COMPLETED])
        self.assertEqual(code, 0, out)
        reg = st["registered"]
        self.assertEqual(reg["containerDefinitions"][0]["image"], IMAGE)
        for field in ("taskDefinitionArn", "revision", "status", "requiresAttributes",
                      "compatibilities", "registeredAt", "registeredBy"):
            self.assertNotIn(field, reg)
        self.assertEqual(reg["cpu"], "256")
        self.assertEqual(reg["tags"], [{"key": "team", "value": "cupcake"}])
        # base is the family's latest revision, not the one the service runs
        self.assertEqual(calls(st, "describe-task-definition")[0][-3], "cupcake-cupcake")

    def test_replaces_a_previously_pinned_image(self):
        code, out, st = deploy([COMPLETED], base=base_td(image=f"{REPO}@sha256:{'b' * 64}"))
        self.assertEqual(code, 0, out)
        self.assertEqual(st["registered"]["containerDefinitions"][0]["image"], IMAGE)

    def test_leaves_sidecars_from_other_repos_alone(self):
        base = base_td()
        base["containerDefinitions"].append({"name": "otel", "image": "public.ecr.aws/aws-observability/aws-otel-collector:v0.40"})
        code, out, st = deploy([COMPLETED], base=base)
        self.assertEqual(code, 0, out)
        images = [c["image"] for c in st["registered"]["containerDefinitions"]]
        self.assertEqual(images, [IMAGE, "public.ecr.aws/aws-observability/aws-otel-collector:v0.40"])

    def test_fails_when_no_container_uses_the_repo(self):
        code, out, st = deploy([COMPLETED], base=base_td(image="nginx:1.27"))
        self.assertNotEqual(code, 0)
        self.assertIn("No container", out)
        self.assertEqual(calls(st, "register-task-definition"), [])

    def test_registers_untagged_when_tagging_is_denied(self):
        code, out, st = deploy([COMPLETED], fail={"register-task-definition": "tagged"})
        self.assertEqual(code, 0, out)
        self.assertNotIn("tags", st["registered"])
        self.assertIn("without tags", out)

    def test_missing_service_fails_with_hint(self):
        code, out, _ = deploy([COMPLETED], svc={})
        self.assertNotEqual(code, 0)
        self.assertIn("tf-apply creates it", out)


class UpdateServiceTest(unittest.TestCase):
    def test_sets_task_definition_and_desired_count_from_tag(self):
        code, out, st = deploy([COMPLETED])
        self.assertEqual(code, 0, out)
        update = calls(st, "update-service")[0]
        self.assertEqual(update[update.index("--task-definition") + 1], NEW_TD)
        self.assertEqual(update[update.index("--desired-count") + 1], "2")
        self.assertNotIn("--force-new-deployment", update)

    def test_keeps_desired_count_without_tag(self):
        code, out, st = deploy([COMPLETED], tags={})
        self.assertEqual(code, 0, out)
        self.assertNotIn("--desired-count", calls(st, "update-service")[0])

    def test_deploys_even_when_a_deployment_is_in_progress(self):
        busy = service(deployments=[deployment("ecs-svc/tf", "PRIMARY", "IN_PROGRESS", f"{TD_ARN}:4"),
                                    deployment("ecs-svc/old", "ACTIVE", "COMPLETED", f"{TD_ARN}:3")])
        code, out, st = deploy([COMPLETED], svc=busy)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls(st, "update-service")), 1)


class RolloutResultTest(unittest.TestCase):
    def test_waits_until_completed(self):
        code, out, st = deploy([IN_PROGRESS, IN_PROGRESS, COMPLETED])
        self.assertEqual(code, 0, out)
        self.assertEqual(st["described"], 4)
        self.assertIn("Deployment complete", out)

    def test_circuit_breaker_rollback_fails_with_stopped_reasons(self):
        code, out, _ = deploy([IN_PROGRESS, ROLLED_BACK], stopped=[STOPPED_TASK])
        self.assertNotEqual(code, 0)
        self.assertIn("was replaced by deployment ecs-svc/rollback", out)
        self.assertIn("circuit breaker", out)
        self.assertIn("Essential container in task exited", out)
        self.assertIn("exit code 1, OutOfMemoryError", out)
        self.assertIn("log group /ecs/cupcake/cupcake", out)
        self.assertIn("deployment failed: tasks failed to start", out)

    def test_superseded_by_a_later_deploy_passes_with_warning(self):
        newer = service(deployments=[deployment("ecs-svc/later", "PRIMARY", "IN_PROGRESS", f"{TD_ARN}:6"),
                                     deployment("ecs-svc/new", "ACTIVE", "IN_PROGRESS", NEW_TD)])
        code, out, _ = deploy([IN_PROGRESS, newer])
        self.assertEqual(code, 0, out)
        self.assertIn("superseded by a newer deploy", out)

    def test_failed_rollout_without_rollback_fails(self):
        # First deploy of a service: nothing to roll back to
        failed = service(deployments=[deployment("ecs-svc/new", "PRIMARY", "FAILED", NEW_TD, "tasks failed to start.")])
        code, out, _ = deploy([failed])
        self.assertNotEqual(code, 0)
        self.assertIn("failed: tasks failed to start.", out)
        self.assertIn("(none listed)", out)

    def test_completed_with_other_task_definition_fails(self):
        other = service(deployments=[deployment("ecs-svc/new", "PRIMARY", "COMPLETED", f"{TD_ARN}:4")])
        code, out, _ = deploy([other])
        self.assertNotEqual(code, 0)
        self.assertIn("expected " + NEW_TD, out)

    def test_timeout_fails(self):
        code, out, _ = deploy([IN_PROGRESS], timeout="0")
        self.assertNotEqual(code, 0)
        self.assertIn("did not complete within 0s", out)

    def test_diagnostics_survive_missing_permissions(self):
        code, out, _ = deploy([ROLLED_BACK], fail={"list-tasks": True})
        self.assertNotEqual(code, 0)
        self.assertIn("AccessDeniedException", out)
        self.assertIn("was replaced by deployment", out)


if __name__ == "__main__":
    unittest.main()
