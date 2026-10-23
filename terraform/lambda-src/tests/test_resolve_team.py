"""Tests for shared.github.resolve_team — run: python3 -m unittest discover terraform/lambda-src/tests"""

import io
import json
import os
import sys
import types
import unittest
from unittest import mock

# shared.github creates an SSM client at import; no AWS access is needed here.
sys.modules.setdefault("boto3", types.SimpleNamespace(client=lambda *a, **k: mock.MagicMock()))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared import github  # noqa: E402


def teams_response(*slugs):
    return mock.MagicMock(__enter__=lambda s: io.BytesIO(json.dumps([{"slug": t} for t in slugs]).encode()),
                          __exit__=lambda *a: False)


class ResolveTeamTest(unittest.TestCase):
    def resolve(self, slugs, requested=None):
        with mock.patch.object(github, "github_app_token", return_value="t"), \
             mock.patch.object(github.urllib.request, "urlopen", return_value=teams_response(*slugs)):
            return github.resolve_team("cupcake", requested)

    def test_requested_team_wins_over_first_team(self):
        # javaBin/cupcake is in both `cake` and `cupcake`; app.yaml says cupcake
        self.assertEqual(self.resolve(["cake", "cupcake"], "cupcake"), "cupcake")

    def test_requested_team_the_repo_is_not_in_is_refused(self):
        self.assertIsNone(self.resolve(["cake"], "cupcake"))

    def test_without_request_first_non_platform_team(self):
        self.assertEqual(self.resolve(["platform", "cake", "cupcake"]), "cake")

    def test_platform_team_is_never_returned(self):
        self.assertIsNone(self.resolve(["platform"], "platform"))


if __name__ == "__main__":
    unittest.main()
