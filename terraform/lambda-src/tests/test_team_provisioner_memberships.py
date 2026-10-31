"""Tests for team_provisioner auth-membership recording — run: python3 -m unittest discover terraform/lambda-src/tests"""

import importlib.util
import json
import os
import sys
import types
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(__file__), "..")


class FakeDynamo:
    def __init__(self, pks):
        self.items = {pk: {"pk": {"S": pk}} for pk in pks}

    def put_item(self, TableName, Item):
        self.items[Item["pk"]["S"]] = Item

    def delete_item(self, TableName, Key):
        del self.items[Key["pk"]["S"]]

    def scan(self, TableName, **kw):
        return {"Items": [{"pk": i["pk"]} for i in self.items.values()]}


def load(dynamo):
    os.environ["MEMBERSHIPS_TABLE"] = "javabin-auth-memberships"
    os.environ["COGNITO_INTERNAL_POOL_ID"] = "pool"
    cognito = mock.MagicMock()
    cognito.exceptions.ResourceNotFoundException = KeyError
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: {"dynamodb": dynamo, "cognito-idp": cognito}.get(svc, mock.MagicMock())
    sys.modules["boto3"] = boto3
    sys.path.insert(0, SRC)
    sys.path.insert(0, os.path.join(SRC, "team_provisioner"))
    spec = importlib.util.spec_from_file_location("team_provisioner_handler", os.path.join(SRC, "team_provisioner", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MembershipsTest(unittest.TestCase):
    def test_group_sync_records_members_lowercased(self):
        dynamo = FakeDynamo([])
        h = load(dynamo)
        h.sync_group_to_cognito({"name": "helter", "google": "helter@java.no", "cognito": True,
                                 "members": ["Ada.Lovelace@java.no"]})
        self.assertEqual(dynamo.items["group#helter"]["members"], {"L": [{"S": "ada.lovelace@java.no"}]})

    def test_payload_without_members_does_not_wipe_membership(self):
        dynamo = FakeDynamo(["group#helter"])
        h = load(dynamo)
        h.sync_group_to_cognito({"name": "helter", "google": "helter@java.no", "cognito": True})
        self.assertNotIn("members", dynamo.items["group#helter"])  # untouched

    def test_prune_removes_gone_groups_but_keeps_team_and_client_rows(self):
        dynamo = FakeDynamo(["group#helter", "group#drift", "group#team-cupcake", "client#abc"])
        h = load(dynamo)
        self.assertEqual(h._prune_memberships({"helter"}), ["drift"])
        self.assertEqual(set(dynamo.items), {"group#helter", "group#team-cupcake", "client#abc"})


class TeamPermissionSetTest(unittest.TestCase):
    def _provisioner(self):
        h = load(FakeDynamo([]))
        sso = mock.MagicMock()
        sso.exceptions.ConflictException = type("Conflict", (Exception,), {})
        sso.exceptions.ResourceNotFoundException = type("NotFound", (Exception,), {})
        sso.provision_permission_set.return_value = {"PermissionSetProvisioningStatus": {"RequestId": "r1"}}
        sso.describe_permission_set_provisioning_status.return_value = {"PermissionSetProvisioningStatus": {"Status": "SUCCEEDED"}}
        h.sso_client = sso
        h._resolve_permission_set_arn = lambda name: "arn:ps"
        h._create_account_assignment = lambda *a, **k: {"assigned": True}
        h.time.sleep = lambda s: None
        h._guardrails_state = lambda name: "ok"
        return h, sso

    def test_team_guardrails_are_the_boundary_not_an_attached_policy(self):
        h, sso = self._provisioner()

        result = h._ensure_team_permission_set("cupcake", "group-1")

        boundary = sso.put_permissions_boundary_to_permission_set.call_args.kwargs
        self.assertEqual(boundary["PermissionsBoundary"],
                         {"CustomerManagedPolicyReference": {"Name": "javabin-team-cupcake-guardrails"}})
        # Attached as a policy, the guardrails' baseline "Allow *" granted everything
        sso.attach_customer_managed_policy_reference_to_permission_set.assert_not_called()
        detached = {c.kwargs["CustomerManagedPolicyReference"]["Name"]
                    for c in sso.detach_customer_managed_policy_reference_from_permission_set.call_args_list}
        self.assertEqual(detached, {"javabin-team-cupcake-guardrails", "javabin-developer-boundary"})
        view_only = sso.attach_managed_policy_to_permission_set.call_args.kwargs
        self.assertEqual(view_only["ManagedPolicyArn"], "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess")
        self.assertEqual(result["view_only"], "ok")
        self.assertEqual(result["provisioned"], {"status": "SUCCEEDED"})
        policy = json.loads(sso.put_inline_policy_to_permission_set.call_args.kwargs["InlinePolicy"])
        self.assertEqual(policy, h._team_inline_policy("cupcake"))

    def test_boundary_failure_leaves_policies_alone(self):
        h, sso = self._provisioner()
        sso.put_permissions_boundary_to_permission_set.side_effect = RuntimeError("AccessDenied")

        result = h._ensure_team_permission_set("cupcake", "group-1")

        self.assertIn("AccessDenied", result["error"])
        sso.detach_customer_managed_policy_reference_from_permission_set.assert_not_called()
        sso.put_inline_policy_to_permission_set.assert_not_called()
        sso.provision_permission_set.assert_not_called()

    def test_view_only_failure_still_sets_the_team_policy(self):
        h, sso = self._provisioner()
        sso.attach_managed_policy_to_permission_set.side_effect = RuntimeError("AccessDenied")

        result = h._ensure_team_permission_set("cupcake", "group-1")

        self.assertTrue(result["view_only"].startswith("error"))
        sso.put_inline_policy_to_permission_set.assert_called_once()
        self.assertEqual(result["provisioned"], {"status": "SUCCEEDED"})

    def test_inline_policy_allows_only_the_teams_own_resources(self):
        h = load(FakeDynamo([]))
        h.ACCOUNT_ID = "123456789012"
        policy = h._team_inline_policy("cupcake")
        st = {s["Sid"]: s for s in policy["Statement"]}

        # No IfExists: a request without the resource's team tag (untagged
        # resources, S3 objects, Route53, creates) is not allowed by it
        self.assertEqual(st["AllowTeamTaggedResources"]["Condition"],
                         {"StringEquals": {"aws:ResourceTag/team": "cupcake"}})
        for s in policy["Statement"]:
            if s["Effect"] != "Allow" or s["Sid"] == "AllowTeamTaggedResources":
                continue
            self.assertNotEqual(s["Action"], "*", s["Sid"])
            for action in s["Action"] if isinstance(s["Action"], list) else [s["Action"]]:
                self.assertNotIn(action.split(":")[1], ("*", "Delete*", "Put*"), s["Sid"])
            if s["Resource"] == "*":
                self.assertEqual(s["Sid"], "AllowUntaggedReads")
        self.assertTrue(all(a.split(":")[1].startswith(("List", "Describe", "Get"))
                            for a in st["AllowUntaggedReads"]["Action"]))
        self.assertEqual(st["AllowTeamParameters"]["Resource"], [
            "arn:aws:ssm:*:123456789012:parameter/javabin/apps/cupcake",
            "arn:aws:ssm:*:123456789012:parameter/javabin/apps/cupcake/*",
        ])
        self.assertEqual(st["AllowTeamLogs"]["Resource"], "arn:aws:logs:*:123456789012:log-group:/ecs/cupcake/*")
        # {team}-{name}-{account}: never a legacy bucket like javazone-backup
        self.assertEqual(st["AllowTeamBuckets"]["Resource"], [
            "arn:aws:s3:::cupcake-*-123456789012", "arn:aws:s3:::cupcake-*-123456789012/*"])
        self.assertEqual(st["AllowExecOnPlatformCluster"]["Resource"],
                         "arn:aws:ecs:*:123456789012:cluster/javabin-platform")
        deny = st["DenyCreatingParameters"]
        self.assertEqual(deny["Condition"], {"BoolIfExists": {"ssm:Overwrite": "false"}})
        # app-auth-sync's outputs are readable but not writable by the team
        managed = st["DenyPlatformManagedParameters"]
        self.assertTrue(any(r.endswith("/apps/*/*/auth-status") for r in managed["Resource"]))
        self.assertTrue(any(r.endswith("/apps/*/*/cognito-client-*") for r in managed["Resource"]))
        self.assertLess(len(json.dumps(policy)), 32768)  # permission set inline limit

    def _guardrails_with(self, tags=None, missing=False):
        h = load(FakeDynamo([]))
        iam = mock.MagicMock()
        iam.exceptions.NoSuchEntityException = type("NoSuchEntity", (Exception,), {})
        if missing:
            iam.list_policy_tags.side_effect = iam.exceptions.NoSuchEntityException()
        else:
            iam.list_policy_tags.return_value = {"Tags": [{"Key": k, "Value": v} for k, v in (tags or {}).items()]}
        h.iam_client = iam
        return h

    def test_guardrails_must_exist_and_be_terraform_managed(self):
        ok = {"managed-by": "terraform", "repo": "javaBin/platform"}
        self.assertEqual(self._guardrails_with(ok)._guardrails_state("javabin-team-x-guardrails"), "ok")
        self.assertTrue(self._guardrails_with(missing=True)._guardrails_state("p").startswith("pending"))
        self.assertTrue(self._guardrails_with({"managed-by": "me"})._guardrails_state("p").startswith("refused"))

    def test_missing_guardrails_leave_the_permission_set_untouched(self):
        h = load(FakeDynamo([]))
        h.sso_client = mock.MagicMock()
        h._resolve_permission_set_arn = lambda name: "arn:ps"
        h._guardrails_state = lambda name: "pending: policy not created yet"
        result = h._ensure_team_permission_set("newteam", "group-1")
        self.assertTrue(result["guardrails"].startswith("pending"))
        h.sso_client.detach_customer_managed_policy_reference_from_permission_set.assert_not_called()
        h.sso_client.put_permissions_boundary_to_permission_set.assert_not_called()
        h.sso_client.put_inline_policy_to_permission_set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
