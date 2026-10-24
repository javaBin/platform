"""Tests for generate-modules.py — run: python3 -m unittest discover scripts/tests"""

import importlib.util
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


CALLER = {  # cupcake-backend calling moresleep privately instead of over the internet
    "name": "cupcake-backend",
    "team": "cupcake",
    "compute": {"port": 8080},
    "routing": {"host": "cupcake-backend.javazone.no", "priority": 101},
    "calls": [{"app": "moresleep", "team": "moresleep", "scopes": ["talks.read"], "env": "SP_BASE_URL"},
              {"app": "cupcake"}],
}

CALLEE = {  # private-only app (no routing) offering scopes
    "name": "moresleep",
    "team": "moresleep",
    "compute": {"port": 8082},
    "resources": {"databases": [{"name": "main", "engine": "postgres", "env": "DATABASE_URL"}]},
    "provides": {
        "scopes": {"talks.read": "Read talks", "talks.write": "Write talks"},
        "consumers": [{"app": "cupcake-backend", "team": "cupcake", "scopes": ["talks.read"]}],
    },
}


class PeersValidationTest(unittest.TestCase):
    def assertRejected(self, app, fragment):
        with self.assertRaises(gen.AppYamlError) as cm:
            generate(app)
        self.assertIn(fragment, str(cm.exception))

    def test_self_reference_is_rejected(self):
        self.assertRejected({**CALLER, "calls": [{"app": "cupcake-backend"}]}, "cannot call itself")
        self.assertRejected({**CALLEE, "provides": {"consumers": [{"app": "moresleep"}]}}, "cannot consume itself")

    def test_duplicates_are_rejected(self):
        self.assertRejected({**CALLER, "calls": [{"app": "x"}, {"app": "x", "team": "cupcake"}]}, "listed twice")
        self.assertRejected({**CALLEE, "provides": {"consumers": [{"app": "a", "team": "b"}, {"app": "a", "team": "b"}]}},
                            "listed twice")

    def test_bad_names_and_fields_are_rejected(self):
        self.assertRejected({**CALLER, "calls": [{"app": "Moresleep"}]}, "calls[0].app")
        self.assertRejected({**CALLER, "calls": [{"app": "x", "team": "more-sleep"}]}, "calls[0].team")
        self.assertRejected({**CALLER, "calls": [{"app": "x", "scope": ["a"]}]}, "unknown field(s) scope")
        self.assertRejected({**CALLER, "calls": [{"app": "x", "scopes": ["has space"]}]}, "not a valid scope name")
        self.assertRejected({**CALLER, "calls": [{"app": "x", "env": "1BAD"}]}, "calls[0].env")
        self.assertRejected({**CALLER, "calls": {"app": "x"}}, "calls: expected a list")
        self.assertRejected({**CALLEE, "provides": {"scopes": {"a": ""}}}, "description")

    def test_consumer_scopes_must_be_provided(self):
        app = {**CALLEE, "provides": {"scopes": ["talks.read"],
                                      "consumers": [{"app": "x", "scopes": ["talks.write"]}]}}
        self.assertRejected(app, "not listed in provides.scopes")

    def test_injected_names_must_not_collide(self):
        # cupcake today sets SP_BASE_URL in environment:; moving to calls means removing it there
        self.assertRejected({**CUPCAKE, "calls": [{"app": "moresleep", "team": "moresleep", "env": "SP_BASE_URL"}]},
                            "SP_BASE_URL is injected")
        self.assertRejected({**CALLER, "calls": [{"app": "moresleep", "team": "a"}, {"app": "moresleep", "team": "b"}]},
                            "MORESLEEP_URL used twice")
        self.assertRejected({**CALLER, "calls": [{"app": "x", "env": "PEER_AUTH_CLIENT_ID"}, {"app": "y", "scopes": ["a"]}]},
                            "PEER_AUTH_CLIENT_ID used twice")

    def test_limits(self):
        self.assertRejected({**CALLER, "calls": [{"app": f"a{i}"} for i in range(21)]}, "at most 20")


class PeersGenerationTest(unittest.TestCase):
    def test_every_app_gets_its_own_security_group_and_registry(self):
        for app in (CUPCAKE, FROSTING, FULL, {"name": "worker", "team": "core"}):
            main = generate(app)["main.tf"]
            self.assertIn('module "network"', main)
            self.assertIn("security_group_ids       = [module.network.security_group_id]", main)
            self.assertIn("[module.network.registry_arn]", main)
            self.assertNotIn("ecs_tasks_security_group_id", main)

    def test_rds_accepts_only_the_apps_own_group(self):
        main = generate(FULL)["main.tf"]
        self.assertIn("allowed_security_group_ids = [module.network.security_group_id]", main)

    def test_alb_ingress_follows_routing(self):
        self.assertIn("alb_ingress = true", generate(CALLER)["main.tf"])
        self.assertIn("alb_ingress = false", generate(CALLEE)["main.tf"])

    def test_caller_gets_url_scopes_and_m2m_credentials(self):
        main = generate(CALLER)["main.tf"]
        self.assertIn('"moresleep/moresleep" = ["talks.read"]', main)
        self.assertIn('"cupcake/cupcake"     = []', main)
        self.assertRegex(main, r'SP_BASE_URL\s+= module.network.peer_url_arns\["moresleep/moresleep"\]')
        self.assertRegex(main, r'CUPCAKE_URL\s+= module.network.peer_url_arns\["cupcake/cupcake"\]')
        self.assertRegex(main, r'SP_BASE_SCOPES\s+= "moresleep-moresleep/talks.read"')
        self.assertNotIn("CUPCAKE_SCOPES", main)
        self.assertRegex(main, r"PEER_AUTH_TOKEN_URL\s+= module.network.token_url")
        self.assertRegex(main, r"PEER_AUTH_CLIENT_SECRET\s+= module.network.m2m_client_secret_arn")
        self.assertNotIn("PEER_AUTH_ISSUER", main)

    def test_network_only_caller_gets_no_m2m_credentials(self):
        main = generate({**CALLER, "calls": [{"app": "cupcake"}]})["main.tf"]
        self.assertIn("CUPCAKE_URL", main)
        self.assertNotIn("PEER_AUTH", main)

    def test_callee_gets_verification_settings(self):
        main = generate(CALLEE)["main.tf"]
        self.assertIn('"talks.read"  = "Read talks"', main)
        self.assertIn('"cupcake/cupcake-backend" = ["talks.read"]', main)
        self.assertRegex(main, r"PEER_AUTH_ISSUER\s+= module.network.issuer_url")
        self.assertRegex(main, r'PEER_AUTH_SCOPE_PREFIX\s+= "moresleep-moresleep/"')
        self.assertNotIn("target_group_arn", main)

    def test_scope_list_shorthand(self):
        main = generate({**CALLEE, "provides": {"scopes": ["talks.read"]}})["main.tf"]
        self.assertIn('"talks.read" = "talks.read"', main)


@unittest.skipUnless(shutil.which("terraform"), "terraform not installed")
class FmtTest(unittest.TestCase):
    """tf-plan runs `terraform fmt -check`; generated files must pass it."""

    def test_generated_files_are_fmt_clean(self):
        public = {**FROSTING, "auth": {"pool": "internal", "client": "public", "groups": ["helter"],
                                       "env": {"COGNITO_CLIENT_ID": "NUXT_PUBLIC_OIDC_CLIENT_ID"}}}
        for app in (CUPCAKE, FROSTING, FULL, public, {"name": "worker", "team": "core"}, CALLER, CALLEE):
            with tempfile.TemporaryDirectory() as d:
                for name, content in generate(app).items():
                    with open(os.path.join(d, name), "w") as f:
                        f.write(content)
                r = subprocess.run(["terraform", "fmt", "-check", "-diff", d], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{app['name']}:\n{r.stdout}{r.stderr}")


if __name__ == "__main__":
    unittest.main()
