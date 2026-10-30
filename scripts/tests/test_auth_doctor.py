"""Tests for auth-doctor.py — run: python3 -m unittest discover scripts/tests"""

import importlib.util
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("auth_doctor", os.path.join(HERE, "..", "auth-doctor.py"))
doc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doc)

DOMAIN = "javabin-internal.auth.eu-central-1.amazoncognito.com"
BASE = "/javabin/apps/cupcake/cupcake"
APP = {
    "name": "cupcake", "team": "cupcake",
    "routing": {"host": "cupcake.javazone.no", "priority": 100},
    "auth": {"pool": "internal", "client": "public",
             "callback_urls": ["https://cupcake.javazone.no/"], "logout_urls": ["https://cupcake.javazone.no/"],
             "groups": ["helter"]},
}
REQUEST = {"pool": "internal", "client_type": "public", "callback_urls": ["https://cupcake.javazone.no/"],
           "logout_urls": ["https://cupcake.javazone.no/"], "scopes": ["openid", "email", "profile"],
           "allowed_groups": ["helter"]}
STATUS = {"status": "synced", "client_id": "abc", "pool": "internal", "pool_domain": DOMAIN,
          "identity_providers": ["Google"], "identity_provider": "Google", "request_version": 3,
          "callback_urls": REQUEST["callback_urls"], "logout_urls": REQUEST["logout_urls"],
          "scopes": REQUEST["scopes"], "unknown_groups": [], "empty_groups": [], "drift_corrected": [],
          "synced_at": "2026-10-24T10:00:00+00:00"}
GOOGLE = "https://accounts.google.com/o/oauth2/v2/auth?hd=java.no&prompt=select_account&client_id=x"


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.params = {
            f"{BASE}/auth": {"Value": json.dumps(REQUEST), "Version": 3},
            f"{BASE}/auth-status": {"Value": json.dumps(STATUS), "Version": 9},
            f"{BASE}/cognito-client-id": {"Value": "abc", "Version": 1},
        }
        self.redirects = {"authorize": (302, GOOGLE), "logout": (302, "https://cupcake.javazone.no/")}
        doc.ssm_get = lambda name: self.params.get(name)
        doc.http_get = lambda url: self.redirects["authorize" if "/oauth2/authorize" in url else "logout"]

    def run_doctor(self, app=APP):
        return doc.diagnose(app, wait=0, tasks=False, sleep=lambda s: None)

    def levels(self, report):
        return {(r["level"], r["check"]) for r in report.results}

    def set_status(self, **changes):
        self.params[f"{BASE}/auth-status"]["Value"] = json.dumps({**STATUS, **changes})

    def test_healthy_app_passes(self):
        report = self.run_doctor()
        self.assertFalse(report.failed, report.results)
        self.assertIn(("PASS", "authorize"), self.levels(report))
        self.assertIn(("PASS", "logout"), self.levels(report))

    def test_redirect_mismatch_fails(self):
        self.redirects["authorize"] = (302, f"https://{DOMAIN}/error?error=redirect_mismatch")
        report = self.run_doctor()
        self.assertTrue(report.failed)
        self.assertIn("redirect_mismatch", json.dumps(report.results))

    def test_logout_mismatch_fails(self):
        # Live behaviour: a non-matching logout_uri lands on the hosted UI's /login
        for location in (f"https://{DOMAIN}/error?error=x", f"https://{DOMAIN}/login?client_id=abc"):
            self.redirects["logout"] = (302, location)
            self.assertIn(("FAIL", "logout"), self.levels(self.run_doctor()))

    def test_missing_account_chooser_warns(self):
        self.redirects["authorize"] = (302, "https://accounts.google.com/o/oauth2/v2/auth?client_id=x")
        report = self.run_doctor()
        self.assertFalse(report.failed)
        self.assertIn(("WARN", "authorize"), self.levels(report))

    def test_probe_sends_the_pools_extra_auth_params(self):
        urls = []
        doc.http_get = lambda url: (urls.append(url), self.redirects[
            "authorize" if "/oauth2/authorize" in url else "logout"])[1]
        self.run_doctor()
        authorize = [u for u in urls if "/oauth2/authorize" in u]
        self.assertTrue(authorize)
        self.assertTrue(all("prompt=select_account" in u for u in authorize), authorize)

    def test_aliased_env_without_identity_provider_warns(self):
        # Managed login drops prompt unless identity_provider is sent too
        app = {**APP, "auth": {**APP["auth"], "env": {"COGNITO_CLIENT_ID": "NUXT_PUBLIC_OIDC_CLIENT_ID"}}}
        report = self.run_doctor(app)
        self.assertFalse(report.failed)
        self.assertIn(("WARN", "chooser"), self.levels(report))

    def test_aliased_identity_provider_does_not_warn(self):
        env = {"COGNITO_CLIENT_ID": "NUXT_PUBLIC_OIDC_CLIENT_ID",
               "COGNITO_IDENTITY_PROVIDER": "NUXT_PUBLIC_OIDC_IDENTITY_PROVIDER"}
        app = {**APP, "auth": {**APP["auth"], "env": env}}
        self.assertNotIn(("WARN", "chooser"), self.levels(self.run_doctor(app)))

    def test_no_aliases_does_not_warn(self):
        self.assertNotIn(("WARN", "chooser"), self.levels(self.run_doctor()))

    def test_probe_names_the_identity_provider(self):
        urls = []
        doc.http_get = lambda url: (urls.append(url), self.redirects[
            "authorize" if "/oauth2/authorize" in url else "logout"])[1]
        self.run_doctor()
        self.assertTrue(all("identity_provider=Google" in u for u in urls if "/oauth2/authorize" in u), urls)

    def test_staying_on_hosted_ui_fails_for_federated_idp(self):
        self.redirects["authorize"] = (302, f"https://{DOMAIN}/login?client_id=abc")
        self.assertIn(("FAIL", "authorize"), self.levels(self.run_doctor()))

    def test_sync_error_and_stale_client_id_fail(self):
        self.set_status(status="error", error="internal pool has no identity provider ['Google']")
        self.assertIn(("FAIL", "status"), self.levels(self.run_doctor()))
        self.set_status(client_id="other")
        self.assertIn(("FAIL", "status"), self.levels(self.run_doctor()))

    def test_unknown_groups_fail_and_drift_warns(self):
        self.set_status(unknown_groups=["helters"], drift_corrected=["SupportedIdentityProviders"])
        levels = self.levels(self.run_doctor())
        self.assertIn(("FAIL", "groups"), levels)
        self.assertIn(("WARN", "drift"), levels)

    def test_request_differing_from_app_yaml_fails(self):
        app = {**APP, "auth": {**APP["auth"], "logout_urls": ["https://cupcake.javazone.no/bye"]}}
        self.assertIn(("FAIL", "request"), self.levels(self.run_doctor(app)))

    def test_old_status_version_is_reported(self):
        self.set_status(request_version=2)
        self.assertIn(("WARN", "status"), self.levels(self.run_doctor()))

    def test_validate_only_and_no_auth_are_skipped(self):
        for auth in ({"pool": "internal", "client": "none"}, None):
            app = {**APP, "auth": auth} if auth else {k: v for k, v in APP.items() if k != "auth"}
            report = self.run_doctor(app)
            self.assertFalse(report.failed)
            self.assertEqual(report.results[0]["level"], "SKIP")

    def test_status_that_is_not_json_fails_instead_of_crashing(self):
        self.params[f"{BASE}/auth-status"]["Value"] = '{"status":"synced","callback_ur'
        report = self.run_doctor()
        self.assertIn(("FAIL", "status"), self.levels(report))
        self.assertIn("not JSON", json.dumps(report.results))

    def test_missing_status_waits_briefly_not_the_full_wait(self):
        del self.params[f"{BASE}/auth-status"]
        clock = [0.0]
        doc.time.monotonic = lambda: clock[0]
        try:
            def sleep(s):
                clock[0] += s
            doc.diagnose(APP, wait=180, tasks=False, sleep=sleep)
        finally:
            import time
            doc.time.monotonic = time.monotonic
        self.assertLessEqual(clock[0], doc.MISSING_STATUS_WAIT + 5)


class TaskCheckTest(unittest.TestCase):
    def setUp(self):
        self.responses = {
            "describe-services": {"services": [{"deployments": [
                {"id": "ecs-svc/new", "status": "PRIMARY"}, {"id": "ecs-svc/old", "status": "ACTIVE"}]}]},
            "list-tasks": {"taskArns": ["arn/t1", "arn/t2"]},
            "describe-tasks": {"tasks": [
                {"taskArn": "arn/t1", "startedBy": "ecs-svc/new", "startedAt": "2026-10-24T10:05:00+00:00"},
                {"taskArn": "arn/t2", "startedBy": "ecs-svc/old", "startedAt": "2026-10-24T09:00:00+00:00"}]},
        }
        doc.aws = lambda *args: self.responses[args[1]]

    def check(self, status):
        report = doc.Report()
        doc.check_tasks(report, "cupcake", "cupcake", status)
        return report.results

    def test_draining_deployment_tasks_are_ignored(self):
        results = self.check({"client_id_changed_at": "2026-10-24T10:00:00+00:00"})
        self.assertEqual([r["level"] for r in results], ["PASS"])

    def test_stale_primary_task_is_a_warning_not_a_failure(self):
        results = self.check({"client_id_changed_at": "2026-10-24T11:00:00+00:00"})
        self.assertEqual([r["level"] for r in results], ["WARN"])
        self.assertIn("t1", results[0]["message"])

    def test_no_recorded_change_skips(self):
        # Old code rewrote cognito-client-id on every reconcile, so its
        # LastModifiedDate says nothing; only auth-status's timestamp counts.
        self.assertEqual([r["level"] for r in self.check({})], ["SKIP"])


if __name__ == "__main__":
    unittest.main()
