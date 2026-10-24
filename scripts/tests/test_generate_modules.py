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
        self.assertRegex(main, r"DATABASE_URL\s+= module.rds_main.endpoint")
        self.assertNotIn("import {", main)

    def test_adopt_rejects_fields_mirrored_from_the_live_instance(self):
        for field, value in (("instance_class", "db.t4g.small"), ("allocated_storage", 50),
                             ("engine_version", "17"), ("multi_az", True)):
            self.assertRejected(with_db(MORESLEEP, **{field: value}), "cannot be set with adopt")

    def test_adopt_is_postgres_only(self):
        app = with_db(MORESLEEP, engine="dynamodb")
        for key in ("host_env", "port_env"):
            app["resources"]["databases"][0].pop(key)
        self.assertRejected(app, "adopt: only supported for engine: postgres")

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


@unittest.skipUnless(shutil.which("terraform"), "terraform not installed")
class FmtTest(unittest.TestCase):
    """tf-plan runs `terraform fmt -check`; generated files must pass it."""

    def test_generated_files_are_fmt_clean(self):
        public = {**FROSTING, "auth": {"pool": "internal", "client": "public", "groups": ["helter"],
                                       "env": {"COGNITO_CLIENT_ID": "NUXT_PUBLIC_OIDC_CLIENT_ID"}}}
        for app in (CUPCAKE, FROSTING, FULL, MORESLEEP, public, {"name": "worker", "team": "core"}):
            with tempfile.TemporaryDirectory() as d:
                for name, content in generate(app).items():
                    with open(os.path.join(d, name), "w") as f:
                        f.write(content)
                r = subprocess.run(["terraform", "fmt", "-check", "-diff", d], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{app['name']}:\n{r.stdout}{r.stderr}")


if __name__ == "__main__":
    unittest.main()
