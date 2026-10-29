"""Tests for app_secret_rollout — run: python3 -m unittest discover terraform/lambda-src/tests"""

import importlib.util
import os
import sys
import types
import unittest


class NotFound(Exception):
    pass


class NotActive(Exception):
    pass


class FakeECS:
    def __init__(self, missing=()):
        self.calls, self.missing = [], set(missing)
        self.exceptions = types.SimpleNamespace(ServiceNotFoundException=NotFound, ServiceNotActiveException=NotActive)

    def update_service(self, cluster, service, forceNewDeployment):
        if service in self.missing:
            raise NotFound(service)
        self.calls.append((cluster, service, forceNewDeployment))


def load(ecs):
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: ecs
    sys.modules["boto3"] = boto3
    path = os.path.join(os.path.dirname(__file__), "..", "app_secret_rollout", "handler.py")
    spec = importlib.util.spec_from_file_location("app_secret_rollout_handler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def event(name, operation="Update"):
    return {"detail-type": "Parameter Store Change", "detail": {"name": name, "operation": operation}}


class AppSecretRolloutTest(unittest.TestCase):
    def test_secret_update_restarts_the_apps_service(self):
        ecs = FakeECS()
        result = load(ecs).handler(event("/javabin/apps/cupcake/cupcake-backend/sp_user"), None)
        self.assertEqual(result["status"], "restarted")
        self.assertEqual(ecs.calls, [("javabin-platform", "cupcake-cupcake-backend", True)])

    def test_platform_managed_and_non_update_events_are_ignored(self):
        ecs = FakeECS()
        h = load(ecs)
        for name in ("/javabin/apps/cupcake/frosting/auth", "/javabin/apps/cupcake/frosting/auth-status",
                     # A new secret always comes with a new ID; the ID's Update restarts
                     "/javabin/apps/cupcake/frosting/cognito-client-secret",
                     "/javabin/apps/core/api/db-master-password", "/javabin/platform/github-app-key",
                     "/javabin/apps/cupcake/frosting/login-1/cognito-client-id",
                     "/javabin/apps/cupcake/frosting/network", "/javabin/apps/cupcake/frosting/peers-status"):
            self.assertEqual(h.handler(event(name), None)["status"], "ignored")
        self.assertEqual(h.handler(event("/javabin/apps/cupcake/frosting/x", "Create"), None)["status"], "ignored")
        self.assertEqual(ecs.calls, [])

    def test_cognito_client_changes_restart_the_app(self):
        # app-auth-sync writes these only when the value changed (new client, IdP cutover)
        for param in ("cognito-client-id", "cognito-client-identity-provider"):
            ecs = FakeECS()
            result = load(ecs).handler(event(f"/javabin/apps/cupcake/cupcake/{param}"), None)
            self.assertEqual(result["status"], "restarted", param)
            self.assertEqual(ecs.calls, [("javabin-platform", "cupcake-cupcake", True)])

    def test_peer_url_and_m2m_credentials_restart_the_caller(self):
        ecs = FakeECS()
        h = load(ecs)
        for name in ("/javabin/apps/cupcake/frosting/peer-moresleep-moresleep-url",
                     "/javabin/apps/cupcake/frosting/m2m-client-secret"):
            self.assertEqual(h.handler(event(name), None)["status"], "restarted")

    def test_missing_service_is_not_an_error(self):
        ecs = FakeECS(missing={"cupcake-frosting"})
        self.assertEqual(load(ecs).handler(event("/javabin/apps/cupcake/frosting/key"), None)["status"], "no-service")


if __name__ == "__main__":
    unittest.main()
