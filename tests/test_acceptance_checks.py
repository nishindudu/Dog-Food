"""Run the real acceptance checker, offline, against the test client.

``run.py`` is the organisers' program, copied verbatim into this repo. Its
``request()`` is the only thing that touches the network, so this test swaps
that one function for the Flask test client and then runs the checker's own
``build_checks``. If this passes, ``python3 run.py .dogfood.toml`` passes
against a running portal, and ``acceptance-report.txt`` is reproducible in CI
without a server.

It also checks that .dogfood.toml is not lying: every route it names resolves
to a real Flask rule, and the tokens it hands over really authenticate.
"""

import importlib.util
import os
import unittest

import support


def setUpModule():
    support.fresh_database()


def load_checker():
    path = os.path.join(support.REPO_ROOT, "run.py")
    spec = importlib.util.spec_from_file_location("dogfood_checker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFIG_PATH = os.path.join(support.REPO_ROOT, ".dogfood.toml")


class AcceptanceReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()
        cls.config = cls.checker.load_config(CONFIG_PATH)
        cls.fixture, cls.fixture_path = cls.checker.load_fixture(None, CONFIG_PATH)
        cls.base = cls.config["portal"]["base_url"].rstrip("/")

        client = support.client()

        def offline_request(url, header=None, method="GET", body=None):
            """Same signature and return shape as run.py's request()."""
            assert url.startswith(cls.base), url
            path = url[len(cls.base):] or "/"
            headers = {}
            if header:
                name, _, value = header.partition(":")
                headers[name.strip()] = value.strip()
            response = client.open(path, method=method, headers=headers, json=body)
            return response.status_code, response.get_data(as_text=True)

        cls.checker.request = offline_request
        cls.checks = cls.checker.build_checks(cls.config, cls.fixture)

    def test_the_config_points_at_a_real_file_of_fixtures(self):
        self.assertIsNotNone(self.fixture, "fixtures.json was not found")
        self.assertEqual(len(self.fixture["projects"]), 41)

    def test_every_t1_check_passes(self):
        failures = [
            f"{check.label}: {'; '.join(check.detail)}"
            for check in self.checks
            if check.tier == "T1" and not check.ok
        ]
        self.assertEqual(failures, [])

    def test_the_role_isolation_checks_pass_too(self):
        """We claim T1, not T2: there is no rubric, no assignment and no
        normalization. The two isolation checks the checker runs do pass, and
        this test keeps them passing."""
        for label in ("judge sees own scores", "judge cannot see peer scores",
                      "participant blocked", "csv export works"):
            check = next(c for c in self.checks if c.label == label)
            self.assertTrue(check.ok, f"{label}: {'; '.join(check.detail)}")

    def test_the_claimed_tier_is_verified_by_the_checkers_own_rule(self):
        verified = [
            tier for tier in self.checker.TIERS
            if any(c.tier == tier for c in self.checks)
            and all(c.ok for c in self.checks if c.tier == tier)
        ]
        solid = []
        for tier in self.checker.TIERS:
            if tier in verified:
                solid.append(tier)
            else:
                break
        claimed = self.config["tiers"]["claimed"]
        self.assertEqual(claimed, ["T1"], "claim T1 only until T2 is finished")
        for tier in claimed:
            self.assertIn(tier, solid)

    def test_every_route_in_the_config_is_a_real_route(self):
        rules = {rule.rule for rule in support.main.app.url_map.iter_rules()}
        for key, route in self.config["routes"].items():
            path = route.split("?")[0]
            matched = any(
                path == rule or _matches(rule, path) for rule in rules
            )
            self.assertTrue(matched, f"routes.{key} = {route} matches no Flask rule")

    def test_the_tokens_in_the_config_authenticate_as_the_role_they_claim(self):
        for label, header in self.config["auth"].items():
            name, _, value = header.partition(":")
            response = support.client().get(
                "/api/v1/session", headers={name.strip(): value.strip()}
            )
            payload = response.get_json()
            self.assertTrue(payload["authenticated"], f"{label} token is stale")
            expected = {
                "organizer": "organizer",
                "judge_a": "judge",
                "judge_b": "judge",
                "participant": "participant",
            }[label]
            self.assertEqual(payload["user"]["role"], expected, label)


def _matches(rule, path):
    """Very small Flask rule matcher: /projects/<int:project_id> vs /projects/1."""
    rule_parts = rule.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(rule_parts) != len(path_parts):
        return False
    for rule_part, path_part in zip(rule_parts, path_parts):
        if rule_part.startswith("<") and rule_part.endswith(">"):
            continue
        if rule_part != path_part:
            return False
    return True


if __name__ == "__main__":
    unittest.main()
