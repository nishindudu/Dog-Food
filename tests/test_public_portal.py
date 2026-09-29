"""The public portal: what a stranger sees, and what must never leak."""

import json
import re
import unittest

import support


def setUpModule():
    support.fresh_database()


FIXTURES = json.load(open(support.FIXTURES, encoding="utf-8"))
FIRST_TITLES = [project["title"] for project in FIXTURES["projects"][:3]]


class PublicGalleryTest(unittest.TestCase):
    def setUp(self):
        self.client = support.client()

    def test_home_page_renders_the_event_from_the_database(self):
        response = self.client.get("/")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(FIXTURES["event"]["name"], body)
        self.assertIn("Submissions closed", body)
        # real counts, read from SQLite when the page was rendered
        self.assertIn("40", body)  # submitted projects
        self.assertIn("Developer tools", body)  # a track from the fixtures

    def test_gallery_is_public_and_contains_fixture_titles(self):
        response = self.client.get("/projects")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        for title in FIRST_TITLES:
            self.assertIn(title, body, f"{title} missing from the public gallery")

    def test_gallery_lists_every_submission_but_not_the_flagged_duplicate(self):
        body = self.client.get("/projects").get_data(as_text=True)
        titles = re.findall(r'<h3><a href="/projects/\d+">([^<]+)</a></h3>', body)
        self.assertEqual(len(titles), 40)
        # prj_07 and prj_41 are the same team, same repo, same title.
        self.assertEqual(titles.count("Dry Harbour"), 1)

    def test_organizer_can_ask_for_the_flagged_duplicate(self):
        body = self.client.get(
            "/projects", headers=support.header_for("organizer"),
            query_string={"duplicates": "1"},
        ).get_data(as_text=True)
        titles = re.findall(r'<h3><a href="/projects/\d+">([^<]+)</a></h3>', body)
        self.assertEqual(len(titles), 41)
        self.assertEqual(titles.count("Dry Harbour"), 2)

    def test_gallery_search_and_track_filter(self):
        body = self.client.get(
            "/projects", query_string={"q": "Glass"}
        ).get_data(as_text=True)
        self.assertIn("Glass Signal", body)
        self.assertNotIn("Slow Loom", body)

        body = self.client.get(
            "/projects", query_string={"track": "trk_05"}
        ).get_data(as_text=True)
        self.assertIn("Quiet Anchor", body)  # trk_05, Climate
        self.assertNotIn("Glass Signal", body)  # trk_04, Security

        body = self.client.get(
            "/projects", query_string={"q": "nothing matches this at all"}
        ).get_data(as_text=True)
        self.assertIn("No projects match those filters", body)

    def test_project_detail_is_public(self):
        project = support.db.get_project_by_external_id(support.event()["id"], "prj_01")
        response = self.client.get(f"/projects/{project['id']}")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Glass Signal", body)
        self.assertIn("NorthKiln", body)  # the team, from the fixtures
        self.assertIn("priya1@example.org", body)  # a member, from the fixtures
        self.assertIn("https://example.org/repo/01", body)
        self.assertIn("Security", body)  # the track name, not the fixture id

    def test_unknown_project_is_a_404_page(self):
        response = self.client.get("/projects/99999")
        self.assertEqual(response.status_code, 404)
        self.assertIn("404", response.get_data(as_text=True))

    def test_public_pages_never_leak_an_invite_code_or_a_score(self):
        invite_codes = [
            team["invite_code"]
            for team in support.db.list_teams(support.event()["id"])
        ]
        for path in ("/", "/projects", "/projects/1", "/teams", "/teams/1", "/events"):
            body = self.client.get(path).get_data(as_text=True)
            for code in invite_codes:
                self.assertNotIn(code, body, f"invite code leaked on {path}")
            self.assertNotIn("functionality", body, f"criteria leaked on {path}")

    def test_json_api_matches_the_page(self):
        payload = self.client.get("/api/v1/projects").get_json()
        self.assertEqual(payload["count"], 40)
        self.assertIn("Glass Signal", [row["title"] for row in payload["projects"]])
        self.assertNotIn("invite_code", json.dumps(payload))

        one = self.client.get(f"/api/v1/projects/{payload['projects'][0]['id']}").get_json()
        self.assertNotIn("score", json.dumps(one).lower())


class PublicPagesTest(unittest.TestCase):
    def setUp(self):
        self.client = support.client()

    def test_teams_page_is_public(self):
        body = self.client.get("/teams").get_data(as_text=True)
        self.assertIn("NorthKiln", body)
        self.assertIn("CopperLedger", body)
        self.assertIn("40 teams", body)

    def test_team_detail_is_public(self):
        team = support.db.get_team_by_external_id(support.event()["id"], "tm_01")
        body = self.client.get(f"/teams/{team['id']}").get_data(as_text=True)
        self.assertIn(team["name"], body)
        self.assertIn("priya1@example.org", body)
        self.assertNotIn(team["invite_code"], body)

    def test_member_sees_the_invite_link(self):
        # priya1@example.org, the checker's participant, leads tm_01
        team = support.db.get_team_by_external_id(support.event()["id"], "tm_01")
        body = self.client.get(
            f"/teams/{team['id']}", headers=support.header_for("participant")
        ).get_data(as_text=True)
        self.assertIn(team["invite_code"], body)

    def test_invite_link_page_needs_a_participant(self):
        team = support.db.get_team_by_external_id(support.event()["id"], "tm_25")
        response = self.client.get(f"/teams/join/{team['invite_code']}")
        self.assertEqual(response.status_code, 302)  # redirected to /login
        self.assertIn("/login", response.headers["Location"])

        response = self.client.get(
            f"/teams/join/{team['invite_code']}",
            headers=support.header_for("participant"),
        )
        # priya1 is already on that team, so the page says so
        self.assertEqual(response.status_code, 200)
        self.assertIn("already on", response.get_data(as_text=True))

        response = self.client.get("/teams/join/not-a-real-code",
                                   headers=support.header_for("participant"))
        self.assertEqual(response.status_code, 404)

    def test_events_page_lists_the_seeded_event(self):
        body = self.client.get("/events").get_data(as_text=True)
        self.assertIn(FIXTURES["event"]["name"], body)
        self.assertIn("running", body)
        self.assertIn("evt_01", body)

    def test_login_page_lists_the_seeded_identities(self):
        body = self.client.get("/login").get_data(as_text=True)
        self.assertIn("organizer@example.local", body)
        self.assertIn("tomas.varga@example.org", body)
        self.assertIn("change-me", body)

    def test_healthz_reports_database_contents(self):
        payload = self.client.get("/healthz").get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["event"], FIXTURES["event"]["name"])
        self.assertEqual(payload["projects"], 40)
        self.assertGreater(payload["users"], 120)

    def test_api_404_is_json_and_page_404_is_html(self):
        response = self.client.get("/api/v1/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("message", response.get_json())

        response = self.client.get("/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("<!DOCTYPE html>", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
