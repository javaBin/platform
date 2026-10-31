"""Tests for generate-modules.py — run: python3 -m unittest discover scripts/tests"""

import contextlib
import importlib.util
import io
import os
import re
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

MORESLEEP = {  # javaBin/moresleep: existing RDS instance and hand-made DNS record
    "name": "moresleep",
    "team": "moresleep",
    "compute": {"cpu": 256, "memory": 512, "port": 5000, "desired_count": 2, "health_check": "/"},
    "routing": {"host": "sleepingpill.javazone.no", "priority": 120, "adopt_dns": True},
    "resources": {
        "databases": [{"name": "main", "engine": "postgres", "adopt": "dbmoresleep",
                       "host_env": "DBHOST", "port_env": "DBPORT"}],
        "secrets": [{"name": "db-password", "env": "DBPASSWORD"}],
    },
    "environment": {"DATABASE_TYPE": "POSTGRES", "ALL_OPEN_MODE": "false"},
}


def with_db(app, **fields):
    db = {**app["resources"]["databases"][0], **fields}
    return {**app, "resources": {**app["resources"], "databases": [db]}}


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


class ComputeTest(unittest.TestCase):
    def service_block(self, app):
        main = generate(app)["main.tf"]
        start = main.index('module "service"')
        return main[start:main.index("\n}", start)]

    def assertRejected(self, app, fragment):
        with self.assertRaises(gen.AppYamlError) as cm:
            generate(app)
        self.assertIn(fragment, str(cm.exception))

    def assertAttr(self, block, name, value):
        self.assertRegex(block, rf"\n  {name} += {re.escape(value)}\n")

    def with_compute(self, **fields):
        return {**CUPCAKE, "compute": {**CUPCAKE["compute"], **fields}}

    def test_default_user_is_non_root_and_compute_user_overrides_it(self):
        self.assertAttr(self.service_block(CUPCAKE), "container_user", '"1000"')
        for user, rendered in (("101", '"101"'), (101, '"101"'), ("nginx", '"nginx"'), ("1000:1000", '"1000:1000"')):
            self.assertAttr(self.service_block(self.with_compute(user=user)), "container_user", rendered)

    def test_root_user_is_rejected(self):
        for user in ("0", 0, "root", "0:0", "root:root"):
            self.assertRejected(self.with_compute(user=user), "may not run as root")

    def test_malformed_user_is_rejected(self):
        for user in ("", "a b", True, "1000:", ["1000"]):
            self.assertRejected(self.with_compute(user=user), "compute.user")

    def test_grace_period_defaults_to_120_and_is_configurable(self):
        self.assertAttr(self.service_block(CUPCAKE), "health_check_grace_period_seconds", "120")
        block = self.service_block(self.with_compute(health_check_grace_period=300))
        self.assertAttr(block, "health_check_grace_period_seconds", "300")

    def test_grace_period_is_only_set_with_routing(self):
        worker = {k: v for k, v in CUPCAKE.items() if k not in ("routing", "auth")}
        self.assertNotIn("health_check_grace_period_seconds", self.service_block(worker))

    def test_grace_period_must_be_whole_seconds_in_range(self):
        for value in (-1, 3601, 1.5, "120", True):
            self.assertRejected(self.with_compute(health_check_grace_period=value),
                                "compute.health_check_grace_period")

    def test_exec_is_off_by_default(self):
        main = generate(CUPCAKE)["main.tf"]
        self.assertNotIn("enable_execute_command", main)
        self.assertNotIn("ssmmessages", main)

    def test_exec_enables_execute_command_and_grants_ssm_channels(self):
        app = self.with_compute(exec=True, readonly_root_filesystem=False)
        main = generate(app)["main.tf"]
        self.assertAttr(self.service_block(app), "enable_execute_command", "true")
        task_role = main[main.index('module "task_role"'):main.index('module "service"')]
        self.assertIn("ecs-exec", task_role)
        for action in ("CreateControlChannel", "CreateDataChannel", "OpenControlChannel", "OpenDataChannel"):
            self.assertIn(f"ssmmessages:{action}", task_role)

    def test_exec_needs_a_writable_root_filesystem(self):
        self.assertRejected(self.with_compute(exec=True), "readonly_root_filesystem: false")
        self.assertRejected(self.with_compute(exec="yes", readonly_root_filesystem=False), "compute.exec")


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

    def test_internal_clients_get_the_account_chooser_param(self):
        app = {**FROSTING, "auth": {"client": "public",
                                    "env": {"COGNITO_EXTRA_AUTH_PARAMS": "NUXT_PUBLIC_OIDC_EXTRA_PARAMS"}}}
        main = generate(app)["main.tf"]
        self.assertRegex(main, r'COGNITO_EXTRA_AUTH_PARAMS\s+= "prompt=select_account"')
        self.assertRegex(main, r'NUXT_PUBLIC_OIDC_EXTRA_PARAMS\s+= "prompt=select_account"')
        self.assertIn("COGNITO_EXTRA_AUTH_PARAMS", generate(CUPCAKE)["main.tf"])  # confidential

    def test_external_and_validate_only_apps_get_no_extra_auth_params(self):
        self.assertNotIn("COGNITO_EXTRA_AUTH_PARAMS", generate(REAL_CUPCAKE)["main.tf"])
        external = {**FROSTING, "auth": {"pool": "external", "client": "public"}}
        self.assertNotIn("COGNITO_EXTRA_AUTH_PARAMS", generate(external)["main.tf"])

    def test_validate_only_app_gets_no_identity_provider(self):
        self.assertNotIn("COGNITO_IDENTITY_PROVIDER", generate(REAL_CUPCAKE)["main.tf"])

    def test_resolve_auth_matches_the_request(self):
        auth = gen.resolve_auth(REAL_FROSTING)
        self.assertEqual(auth["callback_urls"], REAL_FROSTING["auth"]["callback_urls"])
        self.assertEqual(auth["scopes"], ["openid", "email", "profile"])
        self.assertIsNone(gen.resolve_auth({**FROSTING, "auth": "none"}))


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
      paths    = ["/admin/*", "/admin"]
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

    def test_bare_prefix_is_protected_with_the_wildcard(self):
        rules = gen.normalize_login({"priority": 120, "login": [
            {"paths": ["/admin/*", "/ops/*"], "groups": ["styret"]},
            {"paths": ["/docs/*"], "public": True},
            {"paths": ["/api/*"]},
            {"paths": ["/api"], "public": True},
        ]})
        self.assertEqual(rules[0]["paths"], ["/admin/*", "/ops/*", "/admin", "/ops"])
        self.assertEqual(rules[1]["paths"], ["/docs/*"])  # public rules are left alone
        self.assertEqual(rules[2]["paths"], ["/api/*"])   # /api is listed explicitly
        self.assertRejected({"paths": ["/a/*", "/b/*", "/c/*"]}, "split it into several entries")

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


POSTGRES = {
    "name": "cake-redux",
    "team": "pkom",
    "resources": {"databases": [{"name": "main", "engine": "postgres", "env": "DATABASE_URL",
                                 "jdbc_env": "SPRING_DATASOURCE_URL", "user_env": "SPRING_DATASOURCE_USERNAME",
                                 "password_env": "SPRING_DATASOURCE_PASSWORD"}]},
}


class PerAppResourceTest(unittest.TestCase):
    def assertRejected(self, app, fragment):
        with self.assertRaises(gen.AppYamlError) as cm:
            generate(app)
        self.assertIn(fragment, str(cm.exception))

    def test_every_resource_module_gets_the_app_name(self):
        main = generate(FULL)["main.tf"]
        for mod in ("bucket_uploads", "database_sessions", "rds_main", "queue_notifications"):
            block = main.split(f'module "{mod}" {{', 1)[1].split("\n}", 1)[0]
            self.assertRegex(block, r'service\s+= "cake-redux"', mod)

    def test_postgres_urls_user_and_password_are_injected(self):
        main = generate(POSTGRES)["main.tf"]
        env = main.split("environment = {", 1)[1].split("}", 1)[0]
        secrets = main.split("secrets = {", 1)[1].split("}", 1)[0]
        self.assertRegex(env, r"DATABASE_URL\s+= module.rds_main.url")
        self.assertRegex(env, r"SPRING_DATASOURCE_URL\s+= module.rds_main.jdbc_url")
        self.assertRegex(env, r"SPRING_DATASOURCE_USERNAME\s+= module.rds_main.username")
        self.assertRegex(secrets, r"SPRING_DATASOURCE_PASSWORD\s+= module.rds_main.password_parameter_arn")
        self.assertNotIn("PASSWORD", env)

    def test_one_variable_for_one_database_value(self):
        self.assertRejected(with_db(POSTGRES, user_env="DATABASE_URL"), "DATABASE_URL is used for more than one")

    def test_db_variable_cannot_shadow_another_resource(self):
        app = {**POSTGRES, "resources": {**POSTGRES["resources"],
                                         "buckets": [{"name": "data", "env": "SPRING_DATASOURCE_USERNAME"}]}}
        self.assertRejected(app, "SPRING_DATASOURCE_USERNAME is already set by another resource")

    def test_rds_identifier_must_be_valid(self):
        self.assertRejected(with_db(POSTGRES, name="my_db"), "RDS identifier, here 'pkom-cake-redux-my_db'")
        self.assertRejected(with_db(POSTGRES, name="db-"), "single hyphens")
        self.assertRejected(with_db(POSTGRES, name="1db"), "must start with a letter")
        self.assertRejected(with_db(POSTGRES, name="x" * 48), "at most 63 characters")
        generate(with_db(POSTGRES, name="x" * 47))  # pkom-cake-redux-xxx... is exactly 63

    def test_bucket_name_must_be_valid(self):
        bucket = lambda n: {**POSTGRES, "resources": {"buckets": [{"name": n}]}}
        self.assertRejected(bucket("my_data"), "must be lowercase letters, digits and hyphens")
        self.assertRejected(bucket("x" * 35), "(it would be 64)")
        generate(bucket("x" * 34))  # pkom-cake-redux-xxx...-553637109631 is exactly 63

    def test_queue_name_length(self):
        queue = lambda n: {**POSTGRES, "resources": {"queues": [{"name": n}]}}
        self.assertRejected(queue("x" * 61), "at most 80 characters")
        generate(queue("x" * 60))  # pkom-cake-redux-xxx...-dlq is exactly 80


class AdoptTest(unittest.TestCase):
    def assertRejected(self, app, fragment):
        with self.assertRaises(gen.AppYamlError) as cm:
            generate(app)
        self.assertIn(fragment, str(cm.exception))

    def test_adopted_database_is_imported_not_created(self):
        main = generate(MORESLEEP)["main.tf"]
        self.assertIn('module "rds_main"', main)
        self.assertIn("//terraform/modules/service-rds-adopted?ref=main", main)
        self.assertIn('identifier          = "dbmoresleep"', main)
        self.assertIn('import {\n  to = module.rds_main.aws_db_instance.this\n  id = "dbmoresleep"\n}', main)
        self.assertNotIn("modules/service-rds?", main)

    def test_adopted_database_defaults_to_deletion_protection(self):
        self.assertRegex(generate(MORESLEEP)["main.tf"], r"deletion_protection\s+= true")
        main = generate(with_db(MORESLEEP, deletion_protection=False))["main.tf"]
        self.assertRegex(main, r"deletion_protection\s+= false")

    def test_backup_retention_is_only_managed_when_set(self):
        self.assertNotIn("backup_retention_period", generate(MORESLEEP)["main.tf"])
        main = generate(with_db(MORESLEEP, backup_retention_period=14))["main.tf"]
        self.assertRegex(main, r"backup_retention_period\s+= 14")

    def test_adopted_database_gets_no_task_role_policy(self):
        main = generate(MORESLEEP)["main.tf"]
        self.assertNotIn("module.rds_main.access_policy_json", main)

    def test_host_and_port_env_are_wired(self):
        main = generate(MORESLEEP)["main.tf"]
        self.assertRegex(main, r"DBHOST\s+= module.rds_main.address")
        self.assertRegex(main, r"DBPORT\s+= module.rds_main.port")

    def test_host_env_works_for_created_postgres_too(self):
        app = with_db(MORESLEEP, adopt=None, host_env="PGHOST", env="DATABASE_URL")
        app["resources"]["databases"][0].pop("adopt")
        main = generate(app)["main.tf"]
        self.assertIn("modules/service-rds?ref=main", main)
        self.assertRegex(main, r"PGHOST\s+= module.rds_main.address")
        self.assertRegex(main, r"DATABASE_URL\s+= module.rds_main.url")
        self.assertNotIn("import {", main)

    def test_adopted_database_keeps_its_own_credentials(self):
        for key in ("env", "jdbc_env", "user_env", "password_env"):
            self.assertRejected(with_db(MORESLEEP, **{key: "X"}), f"{key} cannot be set with adopt")

    def test_adopt_rejects_fields_mirrored_from_the_live_instance(self):
        for field, value in (("instance_class", "db.t4g.small"), ("allocated_storage", 50),
                             ("engine_version", "17"), ("multi_az", True)):
            self.assertRejected(with_db(MORESLEEP, **{field: value}), "cannot be set with adopt")

    def test_adopt_is_postgres_only(self):
        app = with_db(MORESLEEP, engine="dynamodb")
        for key in ("host_env", "port_env"):
            app["resources"]["databases"][0].pop(key)
        self.assertRejected(app, "adopt: only supported for engine: postgres")

    def test_db_env_names_are_postgres_only(self):
        app = with_db(MORESLEEP, engine="dynamodb", hash_key="id")
        for key in ("host_env", "port_env", "adopt"):
            app["resources"]["databases"][0].pop(key)
        for key in ("jdbc_env", "user_env", "password_env"):
            self.assertRejected(with_db(app, **{key: "X"}), f"{key}: only supported for engine: postgres")

    def test_adopt_needs_a_valid_identifier(self):
        self.assertRejected(with_db(MORESLEEP, adopt="db_more sleep"), "not an RDS DB instance identifier")

    def test_host_env_is_postgres_only_and_a_valid_name(self):
        self.assertRejected(with_db(MORESLEEP, engine="dynamodb", adopt=None), "only supported for engine: postgres")
        self.assertRejected(with_db(MORESLEEP, host_env="DB-HOST"), "not a valid variable name")

    def test_adopt_dns_takes_over_the_existing_record(self):
        main = generate(MORESLEEP)["main.tf"]
        self.assertRegex(main, r"allow_dns_overwrite\s+= true")
        self.assertIn('module.platform.route53_zone_ids["javazone.no"]', main)

    def test_without_adopt_dns_routing_output_is_unchanged(self):
        self.assertNotIn("allow_dns_overwrite", generate(CUPCAKE)["main.tf"])

    def test_adopting_a_database_needs_platform_approval(self):
        self.assertRejected(with_db(MORESLEEP, adopt="dbcakeredux"), "not approved to adopt 'dbcakeredux'")
        other_team = {**MORESLEEP, "team": "cupcake", "routing": {"host": "x.javazone.no", "priority": 120}}
        self.assertRejected(other_team, "team 'cupcake' is not approved to adopt 'dbmoresleep'")

    def test_adopting_a_dns_record_needs_platform_approval(self):
        app = {**MORESLEEP, "routing": {**MORESLEEP["routing"], "host": "submit.javazone.no"}}
        self.assertRejected(app, "routing.adopt_dns: team 'moresleep' is not approved to adopt 'submit.javazone.no'")

    def test_unapproved_host_is_fine_without_adopt_dns(self):
        routing = {"host": "submit.javazone.no", "priority": 120}
        self.assertNotIn("allow_dns_overwrite", generate({**MORESLEEP, "routing": routing})["main.tf"])

    def test_adopt_dns_must_be_a_bool(self):
        app = {**MORESLEEP, "routing": {**MORESLEEP["routing"], "adopt_dns": "yes"}}
        self.assertRejected(app, "routing.adopt_dns must be true or false")


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

    def test_secret_names_the_platform_writes_are_reserved(self):
        for name in ("network", "m2m-client-id", "m2m-client-secret", "peers-status",
                     "peer-moresleep-moresleep-url", "peer-moresleep-moresleep-scopes"):
            app = {**CUPCAKE, "resources": {"secrets": [{"name": name}]}}
            self.assertRejected(app, "is reserved")
        generate({**CUPCAKE, "resources": {"secrets": [{"name": "peer-review-token"}]}})

    def test_one_client_holds_at_most_50_scopes(self):
        many = [f"s{i}" for i in range(20)]
        calls = [{"app": f"a{i}", "team": "x", "scopes": many} for i in range(3)]
        self.assertRejected({**CALLER, "calls": calls}, "60 scopes requested in total")
        generate({**CALLER, "calls": [*calls[:2], {**calls[2], "scopes": many[:10]}]})

    def test_network_request_must_fit_a_standard_parameter(self):
        scopes = {f"scope.{i}": "d" * 256 for i in range(25)}
        self.assertRejected({**CALLEE, "provides": {"scopes": scopes}}, "4096-byte SSM limit")
        generate({**CALLEE, "provides": {"scopes": {f"scope.{i}": "d" * 100 for i in range(25)}}})

    def test_network_request_size_counts_jsonencode_escapes(self):
        app = {**CALLEE, "provides": {"scopes": {"a": "<&>"}}}
        calls, scopes, consumers = gen.parse_peers(app)
        self.assertIn('"a":"\\u003c\\u0026\\u003e"', gen.network_request(app, calls, scopes, consumers))


class PeersGenerationTest(unittest.TestCase):
    def test_every_app_gets_its_own_security_group_and_registry(self):
        for app in (CUPCAKE, FROSTING, FULL, {"name": "worker", "team": "core"}):
            main = generate(app)["main.tf"]
            self.assertIn('module "network"', main)
            self.assertRegex(main, r"security_group_ids += \[module\.network\.security_group_id\]")
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
        self.assertRegex(main, r'SP_BASE_SCOPES\s+= module\.network\.peer_scopes_arns\["moresleep/moresleep"\]')
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
        for app in (CUPCAKE, FROSTING, FULL, MORESLEEP, public, REAL_FROSTING, REAL_CUPCAKE, WIKI,
                    {"name": "worker", "team": "core"}, CALLER, CALLEE):
            with tempfile.TemporaryDirectory() as d:
                for name, content in generate(app).items():
                    with open(os.path.join(d, name), "w") as f:
                        f.write(content)
                r = subprocess.run(["terraform", "fmt", "-check", "-diff", d], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{app['name']}:\n{r.stdout}{r.stderr}")


if __name__ == "__main__":
    unittest.main()
