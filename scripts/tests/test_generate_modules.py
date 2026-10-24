"""Tests for generate-modules.py — run: python3 -m unittest discover scripts/tests"""

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("generate_modules", os.path.join(HERE, "..", "generate-modules.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

ACCOUNT, REGION, REF = "553637109631", "eu-central-1", "main"

CUPCAKE = {  # javaBin/cupcake app.yaml (scalar auth)
    "name": "cupcake-backend",
    "team": "cupcake",
    "compute": {"cpu": 512, "memory": 1024, "port": 8080, "health_check": "/health"},
    "routing": {"host": "cupcake-backend.javazone.no", "priority": 101},
    "resources": {"secrets": [{"name": "sp_user", "env": "SP_USER"}]},
    "auth": "internal",
    "environment": {"SP_BASE_URL": "https://sleepingpill.javazone.no"},
}

FROSTING = {  # javaBin/frosting app.yaml (mapping auth)
    "name": "cupcake",
    "team": "cupcake",
    "compute": {"cpu": 256, "memory": 512, "port": 3000, "health_check": "/health"},
    "routing": {"host": "cupcake.javazone.no", "priority": 100},
    "auth": {
        "pool": "internal",
        "callback_urls": ["https://cupcake.javazone.no/auth/callback"],
        "logout_urls": ["https://cupcake.javazone.no/"],
    },
    "environment": {"CUPCAKE_BACKEND": "https://cupcake-backend.javazone.no"},
}

FULL = {
    "name": "cake-redux",
    "team": "pkom",
    "compute": {"cpu": 512, "memory": 1024, "port": 8080, "desired_count": 2,
                "readonly_root_filesystem": False},
    "routing": {"host": "cake-redux.javazone.no", "priority": 110},
    "resources": {
        "buckets": [{"name": "uploads", "env": "UPLOADS_BUCKET"}],
        "databases": [{"name": "sessions", "hash_key": "id", "env": "SESSIONS_TABLE"},
                      {"name": "main", "engine": "postgres", "env": "DATABASE_URL"}],
        "secrets": [{"name": "stripe-key", "env": "STRIPE_KEY"}],
        "queues": [{"name": "notifications", "env": "NOTIFICATIONS_QUEUE_URL"}],
    },
    "auth": {"pool": "external", "scopes": ["openid", "email"], "groups": ["admins", "editors"]},
    "environment": {"LOG_LEVEL": "info", "WORKERS": 4, "DEBUG": False,
                    "TEMPLATE": 'say "${hi}"', "LOOKS_LIKE_REF": "module.not.a.ref"},
    "alarms": {"cpu_threshold": 70},
}


def generate(app):
    return gen.generate(dict(app), ACCOUNT, REGION, REF)


class ValidationTest(unittest.TestCase):
    def assertRejected(self, app, fragment):
        with self.assertRaises(gen.AppYamlError) as cm:
            generate(app)
        self.assertIn(fragment, str(cm.exception))

    def test_unknown_top_level_field_is_rejected(self):
        self.assertRejected({**CUPCAKE, "rouitng": {}}, "unknown field(s) rouitng")

    def test_unknown_nested_field_is_rejected(self):
        self.assertRejected({**CUPCAKE, "compute": {"cpus": 1}}, "compute: unknown field(s) cpus")

    def test_unimplemented_features_explain_alternative(self):
        self.assertRejected({**CUPCAKE, "environments": {}}, "not supported")
        self.assertRejected({**CUPCAKE, "budget_alert_nok": 500}, "budget_nok in javaBin/registry")

    def test_team_must_be_lowercase_letters(self):
        self.assertRejected({**CUPCAKE, "team": "cup-cake"}, "team:")

    def test_routing_needs_priority(self):
        self.assertRejected({**CUPCAKE, "routing": {"host": "x.javazone.no"}}, "routing.priority")

    def test_host_must_be_under_an_app_domain(self):
        self.assertRejected({**CUPCAKE, "routing": {"host": "cupcake.example.com", "priority": 101}},
                            "routing.host")

    def test_host_must_be_one_label_deep_for_the_wildcard_cert(self):
        self.assertRejected({**CUPCAKE, "routing": {"host": "a.b.javazone.no", "priority": 101}},
                            "routing.host")

    def test_auth_needs_host(self):
        app = {k: v for k, v in CUPCAKE.items() if k != "routing"}
        self.assertRejected(app, "auth: requires routing.host")

    def test_auth_pool_both_is_rejected(self):
        self.assertRejected({**CUPCAKE, "auth": {"pool": "both"}}, "'both' is not supported")


class GenerationTest(unittest.TestCase):
    def test_both_auth_forms_produce_the_same_wiring(self):
        for app in (CUPCAKE, FROSTING):
            main = generate(app)["main.tf"]
            self.assertIn('module "auth"', main)
            self.assertIn("COGNITO_CLIENT_ID", main)
            self.assertIn("module.auth.client_secret_arn", main)
            self.assertIn("module.auth.issuer_url", main)

    def test_default_callback_urls_come_from_host(self):
        main = generate(CUPCAKE)["main.tf"]
        self.assertIn('["https://cupcake-backend.javazone.no/", "https://cupcake-backend.javazone.no/auth/callback"]', main)

    def test_env_values_are_stringified_and_escaped_not_treated_as_refs(self):
        main = generate(FULL)["main.tf"]
        self.assertIn('WORKERS                 = "4"', main)
        self.assertIn('DEBUG                   = "false"', main)
        self.assertIn('TEMPLATE                = "say \\"$${hi}\\""', main)
        self.assertIn('LOOKS_LIKE_REF          = "module.not.a.ref"', main)

    def test_domain_is_an_alias_for_routing_host(self):
        app = {**CUPCAKE, "routing": {"priority": 101}, "domain": "alias.javazone.no"}
        self.assertIn('"alias.javazone.no"', generate(app)["main.tf"])

    def test_dns_record_goes_in_the_zone_matching_the_host(self):
        for host, zone in (("cupcake-backend.javazone.no", "javazone.no"),
                           ("app.java.no", "java.no"), ("app.javabin.no", "javabin.no")):
            app = {**CUPCAKE, "routing": {"host": host, "priority": 101}}
            self.assertIn(f'module.platform.route53_zone_ids["{zone}"]', generate(app)["main.tf"])

    def test_worker_without_routing_has_no_target_group(self):
        app = {"name": "worker", "team": "core"}
        main = generate(app)["main.tf"]
        self.assertNotIn("target_group_arn", main)
        self.assertNotIn('module "alarms"', main)


class AuthFeaturesTest(unittest.TestCase):
    def test_public_client_gets_id_but_no_secret(self):
        app = {**FROSTING, "auth": {**FROSTING["auth"], "client": "public"}}
        main = generate(app)["main.tf"]
        self.assertIn('"public"', main)
        self.assertIn("COGNITO_CLIENT_ID", main)
        self.assertNotIn("COGNITO_CLIENT_SECRET", main)

    def test_validate_only_injects_issuer_without_client_or_host(self):
        app = {"name": "api", "team": "core", "auth": {"pool": "internal", "client": "none"}}
        main = generate(app)["main.tf"]
        self.assertIn("module.auth.well_known_url", main)
        self.assertNotIn("COGNITO_CLIENT_ID", main)
        self.assertNotIn("callback_urls", main)

    def test_env_aliases_expose_values_under_app_names(self):
        app = {**FROSTING, "auth": {"pool": "internal", "client": "public",
                                    "env": {"COGNITO_CLIENT_ID": "NUXT_PUBLIC_OIDC_CLIENT_ID",
                                            "COGNITO_ISSUER_URL": "NUXT_PUBLIC_OIDC_AUTHORITY"}}}
        main = generate(app)["main.tf"]
        self.assertRegex(main, r"NUXT_PUBLIC_OIDC_CLIENT_ID\s+= module.auth.client_id_arn")
        self.assertRegex(main, r"NUXT_PUBLIC_OIDC_AUTHORITY\s+= module.auth.issuer_url")

    def test_alias_of_value_not_injected_is_rejected(self):
        app = {**FROSTING, "auth": {"pool": "internal", "client": "public",
                                    "env": {"COGNITO_CLIENT_SECRET": "SECRET"}}}
        with self.assertRaises(gen.AppYamlError) as cm:
            generate(app)
        self.assertIn("not injected", str(cm.exception))

    def test_groups_restrict_the_client(self):
        app = {**CUPCAKE, "auth": {"pool": "internal", "groups": ["helter"]}}
        main = generate(app)["main.tf"]
        self.assertIn('allowed_groups', main)
        self.assertIn('["helter"]', main)

    def test_localhost_callbacks_are_passed_through(self):
        app = {**FROSTING, "auth": {"pool": "internal", "client": "public",
                                    "callback_urls": ["https://cupcake.javazone.no/", "http://localhost:3000/"]}}
        self.assertIn('"http://localhost:3000/"', generate(app)["main.tf"])


WIKI = {
    "name": "wiki",
    "team": "core",
    "compute": {"port": 8080, "health_check": "/"},
    "routing": {
        "host": "wiki.java.no",
        "priority": 120,
        "login": [
            {"paths": ["/admin/*"], "groups": ["styret"]},
            {"paths": ["/assets/*", "/health"], "public": True},
            {"groups": ["helter", "team-core"], "pool": "internal"},
        ],
    },
}


def routing_block(main_tf):
    start = main_tf.index('module "routing"')
    return main_tf[start:main_tf.index("\n}\n", start) + 2]


class LoginTest(unittest.TestCase):
    def with_login(self, login, priority=120):
        return {**WIKI, "routing": {"host": "wiki.java.no", "priority": priority, "login": login}}

    def assertRejected(self, login, fragment, priority=120):
        with self.assertRaises(gen.AppYamlError) as cm:
            generate(self.with_login(login, priority))
        self.assertIn(fragment, str(cm.exception))

    def test_rules_get_priorities_directly_below_the_host_rule(self):
        block = routing_block(generate(WIKI)["main.tf"])
        self.assertIn('  project                = "javabin"', block)
        self.assertIn("""  login_rules = {
    p1 = {
      groups   = ["styret"]
      paths    = ["/admin/*"]
      pool     = "internal"
      priority = 119
      public   = false
    }
    p2 = {
      groups   = []
      paths    = ["/assets/*", "/health"]
      pool     = "internal"
      priority = 118
      public   = true
    }
    p3 = {
      groups   = ["helter", "team-core"]
      paths    = ["/*"]
      pool     = "internal"
      priority = 117
      public   = false
    }
  }""", block)
        self.assertIn("listener_rule_priority = 120", block)

    def test_shorthands_protect_the_whole_site(self):
        for login in ("external", {"pool": "external"}, [{"pool": "external", "paths": ["/*"]}]):
            block = routing_block(generate(self.with_login(login))["main.tf"])
            self.assertIn('paths    = ["/*"]', block)
            self.assertIn('pool     = "external"', block)
            self.assertIn("priority = 119", block)

    def test_apps_without_login_are_unchanged(self):
        block = routing_block(generate(FROSTING)["main.tf"])
        self.assertNotIn("login_rules", block)
        self.assertNotIn("project", block)
        for login in (None, [], False):
            self.assertEqual(generate({**FROSTING, "routing": {**FROSTING["routing"], "login": login}}),
                             generate(FROSTING))

    def test_partial_protection_warns_about_case_sensitive_paths(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            generate(self.with_login([{"paths": ["/admin/*"], "groups": ["styret"]}]))
        self.assertIn("::warning file=app.yaml::routing.login protects only some paths", out.getvalue())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            generate(WIKI)
        self.assertEqual(out.getvalue(), "")

    def test_login_needs_a_host(self):
        with self.assertRaises(gen.AppYamlError) as cm:
            generate({**WIKI, "routing": {"priority": 120, "login": "internal"}})
        self.assertIn("routing.host is required", str(cm.exception))

    def test_invalid_rules_are_rejected(self):
        self.assertRejected("both", "must be 'internal', 'external'")
        self.assertRejected(42, "expected 'internal', 'external', a mapping or a list")
        self.assertRejected([{"paths": [f"/p{i}"]} for i in range(11)], "at most 10 rules")
        self.assertRejected({"paths": ["/a", "/b", "/c", "/d", "/e"]}, "1-4 paths")
        self.assertRejected({"paths": []}, "1-4 paths")
        self.assertRejected({"paths": ["admin"]}, "must start with /")
        self.assertRejected({"paths": ["/a b"]}, "ALB path characters")
        self.assertRejected([{"paths": ["/a"]}, {"paths": ["/a"], "public": True}], "listed more than once")
        self.assertRejected([{"paths": ["/*"]}, {"paths": ["/admin/*"]}], "unreachable")
        self.assertRejected({"paths": ["/x"], "public": True, "groups": ["styret"]}, "cannot be combined")
        self.assertRejected({"public": True, "pool": "internal"}, "cannot be combined")
        self.assertRejected({"public": "yes"}, "expected true or false")
        self.assertRejected({"pool": "both"}, "pool: must be 'internal' or 'external'")
        self.assertRejected({"groups": "styret"}, "list of group names")
        self.assertRejected({"groups": ["bad group"]}, "list of group names")
        self.assertRejected({"path": ["/x"]}, "routing.login[0]: unknown field(s) path")
        self.assertRejected([{"paths": ["/a"]}, {"paths": ["/b"]}], "use at least 3", priority=2)


@unittest.skipUnless(shutil.which("terraform"), "terraform not installed")
class FmtTest(unittest.TestCase):
    """tf-plan runs `terraform fmt -check`; generated files must pass it."""

    def test_generated_files_are_fmt_clean(self):
        public = {**FROSTING, "auth": {"pool": "internal", "client": "public", "groups": ["helter"],
                                       "env": {"COGNITO_CLIENT_ID": "NUXT_PUBLIC_OIDC_CLIENT_ID"}}}
        for app in (CUPCAKE, FROSTING, FULL, public, WIKI, {"name": "worker", "team": "core"}):
            with tempfile.TemporaryDirectory() as d:
                for name, content in generate(app).items():
                    with open(os.path.join(d, name), "w") as f:
                        f.write(content)
                r = subprocess.run(["terraform", "fmt", "-check", "-diff", d], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{app['name']}:\n{r.stdout}{r.stderr}")


if __name__ == "__main__":
    unittest.main()
