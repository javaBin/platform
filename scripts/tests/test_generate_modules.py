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


REAL_FROSTING = {  # javaBin/frosting app.yaml, verbatim
    **FROSTING,
    "auth": {
        "pool": "internal", "client": "public",
        "callback_urls": ["https://cupcake.javazone.no/", "https://cupcake.javazone.no/auth/callback",
                          "http://localhost:3000/"],
        "logout_urls": ["https://cupcake.javazone.no/", "http://localhost:3000/"],
        "groups": ["helter"],
        "env": {"COGNITO_CLIENT_ID": "NUXT_PUBLIC_OIDC_CLIENT_ID", "COGNITO_ISSUER_URL": "NUXT_PUBLIC_OIDC_AUTHORITY",
                "COGNITO_DOMAIN": "NUXT_PUBLIC_OIDC_LOGOUT_DOMAIN"},
    },
}
REAL_CUPCAKE = {**CUPCAKE, "auth": {"pool": "internal", "client": "none",
                                    "env": {"COGNITO_WELL_KNOWN_URL": "OIDC_WELL_KNOWN_URL"}}}


class AuthValidationTest(unittest.TestCase):
    def assertRejected(self, auth, fragment, app=FROSTING):
        with self.assertRaises(gen.AppYamlError) as cm:
            generate({**app, "auth": {"pool": "internal", "client": "public", **auth}})
        self.assertIn(fragment, str(cm.exception))

    def test_live_apps_still_generate(self):
        for app in (REAL_FROSTING, REAL_CUPCAKE):
            generate(app)

    def test_callback_on_another_host_is_rejected(self):
        self.assertRejected({"callback_urls": ["https://cupcake.javazone.no.evil.com/cb"]}, "not this app's routing.host")
        self.assertRejected({"logout_urls": ["https://other.javazone.no/"]}, "auth.extra_hosts")

    def test_extra_hosts_allow_other_hosts(self):
        auth = {"callback_urls": ["https://www.javazone.no/cb"], "extra_hosts": ["www.javazone.no"]}
        self.assertIn('"https://www.javazone.no/cb"', generate({**FROSTING, "auth": {"client": "public", **auth}})["main.tf"])
        self.assertRejected({"extra_hosts": ["https://www.javazone.no"]}, "list of hostnames")

    def test_without_routing_every_host_needs_extra_hosts(self):
        app = {"name": "spa", "team": "core"}
        self.assertRejected({"callback_urls": ["https://spa.javazone.no/"]}, "auth.extra_hosts", app=app)
        generate({**app, "auth": {"callback_urls": ["https://spa.javazone.no/"], "extra_hosts": ["spa.javazone.no"]}})

    def test_http_only_for_localhost_and_no_fragments(self):
        self.assertRejected({"callback_urls": ["http://cupcake.javazone.no/"]}, "must use https://")
        self.assertRejected({"callback_urls": ["https://cupcake.javazone.no/#/cb"]}, "#fragment")
        self.assertRejected({"callback_urls": ["cupcake.javazone.no/cb"]}, "absolute https:// URL")
        generate({**FROSTING, "auth": {"client": "public", "callback_urls": ["http://127.0.0.1:5173/cb",
                                                                           "https://localhost:8443/cb"]}})

    def test_client_none_rejects_settings_it_would_ignore(self):
        for key, value in (("callback_urls", ["https://cupcake.javazone.no/"]), ("scopes", ["openid"]),
                           ("logout_urls", ["https://cupcake.javazone.no/"]), ("extra_hosts", ["a.javazone.no"])):
            self.assertRejected({"client": "none", key: value}, f"would be ignored")

    def test_identity_provider_is_injected_and_aliasable(self):
        app = {**FROSTING, "auth": {"client": "public",
                                    "env": {"COGNITO_IDENTITY_PROVIDER": "NUXT_PUBLIC_OIDC_IDENTITY_PROVIDER"}}}
        main = generate(app)["main.tf"]
        self.assertRegex(main, r"COGNITO_IDENTITY_PROVIDER\s+= module.auth.identity_provider_arn")
        self.assertRegex(main, r"NUXT_PUBLIC_OIDC_IDENTITY_PROVIDER\s+= module.auth.identity_provider_arn")

    def test_validate_only_app_gets_no_identity_provider(self):
        self.assertNotIn("COGNITO_IDENTITY_PROVIDER", generate(REAL_CUPCAKE)["main.tf"])

    def test_resolve_auth_matches_the_request(self):
        auth = gen.resolve_auth(REAL_FROSTING)
        self.assertEqual(auth["callback_urls"], REAL_FROSTING["auth"]["callback_urls"])
        self.assertEqual(auth["scopes"], ["openid", "email", "profile"])
        self.assertIsNone(gen.resolve_auth({**FROSTING, "auth": "none"}))


@unittest.skipUnless(shutil.which("terraform"), "terraform not installed")
class FmtTest(unittest.TestCase):
    """tf-plan runs `terraform fmt -check`; generated files must pass it."""

    def test_generated_files_are_fmt_clean(self):
        public = {**FROSTING, "auth": {"pool": "internal", "client": "public", "groups": ["helter"],
                                       "env": {"COGNITO_CLIENT_ID": "NUXT_PUBLIC_OIDC_CLIENT_ID"}}}
        for app in (CUPCAKE, FROSTING, FULL, public, REAL_FROSTING, REAL_CUPCAKE, {"name": "worker", "team": "core"}):
            with tempfile.TemporaryDirectory() as d:
                for name, content in generate(app).items():
                    with open(os.path.join(d, name), "w") as f:
                        f.write(content)
                r = subprocess.run(["terraform", "fmt", "-check", "-diff", d], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{app['name']}:\n{r.stdout}{r.stderr}")


if __name__ == "__main__":
    unittest.main()
