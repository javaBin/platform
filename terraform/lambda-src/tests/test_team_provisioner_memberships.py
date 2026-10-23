"""Tests for team_provisioner auth-membership recording — run: python3 -m unittest discover terraform/lambda-src/tests"""

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
