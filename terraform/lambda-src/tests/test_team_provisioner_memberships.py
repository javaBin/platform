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
    def test_team_permission_set_uses_team_guardrails_and_reprovisions(self):
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

        result = h._ensure_team_permission_set("cupcake", "group-1")

        attach = sso.attach_customer_managed_policy_reference_to_permission_set.call_args.kwargs
        self.assertEqual(attach["CustomerManagedPolicyReference"], {"Name": "javabin-team-cupcake-guardrails"})
        detach = sso.detach_customer_managed_policy_reference_from_permission_set.call_args.kwargs
        self.assertEqual(detach["CustomerManagedPolicyReference"], {"Name": "javabin-developer-boundary"})
        policy = json.loads(sso.put_inline_policy_to_permission_set.call_args.kwargs["InlinePolicy"])
        sids = {st["Sid"] for st in policy["Statement"]}
        self.assertTrue({"DenyPlatformManagedParameters", "DenyCreatingParameters"} <= sids)
        self.assertEqual(result["provisioned"], {"status": "SUCCEEDED"})


if __name__ == "__main__":
    unittest.main()
