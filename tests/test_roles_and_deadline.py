"""Role isolation and deadline enforcement, from the backend side.

These are the two rules the brief says are most often painted onto a
frontend: a judge must not be able to read a peer's scores with curl, and a
submission after the deadline must be refused by the server, not by a
disabled button.
"""

import unittest

import support


def setUpModule():
    support.fresh_database()


class DeadlineEnforcementTest(unittest.TestCase):
    """The fixture event closed on 2026-03-01T18:00:00Z."""

    def setUp(self):
        self.client = support.client()
        self.participant = support.header_for("participant")

    def test_json_submission_is_refused_after_the_deadline(self):
        response = self.client.post(
            "/projects/new",
            json={"title": "dogfood-late-submission-probe", "summary": "probe"},
            headers=self.participant,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["message"], "Submissions are closed for this event"
        )
        self.assertEqual(response.get_json()["submissions_close"],
                         "2026-03-01T18:00:00Z")

    def test_html_form_submission_is_refused_too(self):
        response = self.client.post(
            "/projects/new",
            data={"title": "late", "summary": "late", "status": "submitted"},
            headers=self.participant,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Submissions are closed", response.get_data(as_text=True))

    def test_the_json_api_refuses_the_same_post(self):
        response = self.client.post(
            "/api/v1/projects",
            json={"title": "late", "status": "submitted"},
            headers=self.participant,
        )
        self.assertEqual(response.status_code, 403)

    def test_editing_an_existing_submission_is_refused(self):
        response = self.client.post(
            "/projects/1/edit", json={"title": "renamed after the deadline"},
            headers=self.participant,
        )
        self.assertEqual(response.status_code, 403)
        project = support.db.get_project(1)
        self.assertEqual(project["title"], "Glass Signal")  # untouched

        response = self.client.patch(
            "/api/v1/projects/1", json={"title": "renamed"}, headers=self.participant
        )
        self.assertEqual(response.status_code, 403)

    def test_the_refusal_is_written_to_the_audit_trail(self):
        self.client.post(
            "/projects/new", json={"title": "probe"}, headers=self.participant
        )
        actions = [row["action"] for row in support.db.recent_audit(10)]
        self.assertIn("submission.refused_create", actions)
        entry = next(
            row for row in support.db.recent_audit(10)
            if row["action"] == "submission.refused_create"
        )
        self.assertIn("priya1@example.org", entry["detail"])
        self.assertIn("Submissions closed", entry["detail"])

    def test_nobody_sees_the_form_as_open(self):
        body = self.client.get(
            "/projects/new", headers=self.participant
        ).get_data(as_text=True)
        self.assertIn("Submissions closed", body)
        self.assertIn("disabled", body)


class OpenWindowTest(unittest.TestCase):
    """The same code path with the deadline in the future: drafts, edits and
    submissions must all work, then stop working the instant it closes."""

    def setUp(self):
        support.fresh_database()
        self.client = support.client()
        self.participant = support.header_for_email("elsa25@example.org")
        self.event_id = support.db.create_event(
            event_name="Open Window Test Event",
            event_date="2027-01-01T09:00:00Z",
            event_location="Online",
            description="Created by the test suite.",
            submissions_open="2026-12-01T09:00:00Z",
            submissions_close="2027-01-15T18:00:00Z",
        )
        support.db.set_primary_event(self.event_id)
        support.db.add_track(self.event_id, "Developer tools", external_id="trk_01")

    def tearDown(self):
        fixture_event = support.db.get_event_by_external_id("evt_01")
        support.db.set_primary_event(fixture_event["id"])

    def test_a_participant_without_a_team_is_told_so(self):
        response = self.client.post(
            "/api/v1/projects", json={"title": "No team yet"}, headers=self.participant
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("not on a team", response.get_json()["message"])

    def test_draft_edit_submit_lifecycle(self):
        created = self.client.post(
            "/api/v1/teams", json={"name": "Test Team"}, headers=self.participant
        )
        self.assertEqual(created.status_code, 201)
        invite_code = created.get_json()["team"]["invite_code"]
        self.assertTrue(invite_code)

        draft = self.client.post(
            "/api/v1/projects",
            json={"title": "Quiet Hours", "summary": "A draft.", "status": "draft",
                  "track": "trk_01"},
            headers=self.participant,
        )
        self.assertEqual(draft.status_code, 201)
        project_id = draft.get_json()["project"]["id"]
        self.assertEqual(draft.get_json()["project"]["status"], "draft")
        self.assertIsNone(draft.get_json()["project"]["submitted_at"])

        # a draft is invisible to the public
        self.assertNotIn(
            "Quiet Hours", self.client.get("/projects").get_data(as_text=True)
        )
        anonymous = support.client().get(f"/api/v1/projects/{project_id}")
        self.assertEqual(anonymous.status_code, 404)

        # the owner can read and edit it
        owner = self.client.get(f"/api/v1/projects/{project_id}", headers=self.participant)
        self.assertEqual(owner.status_code, 200)

        edited = self.client.patch(
            f"/api/v1/projects/{project_id}",
            json={"summary": "A better draft."},
            headers=self.participant,
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.get_json()["project"]["summary"], "A better draft.")

        submitted = self.client.patch(
            f"/api/v1/projects/{project_id}",
            json={"status": "submitted"},
            headers=self.participant,
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertIsNotNone(submitted.get_json()["project"]["submitted_at"])
        self.assertIn(
            "Quiet Hours", self.client.get("/projects").get_data(as_text=True)
        )

        # another participant cannot edit it
        other = support.header_for_email("teo26@example.org")
        forbidden = self.client.patch(
            f"/api/v1/projects/{project_id}", json={"title": "stolen"}, headers=other
        )
        self.assertEqual(forbidden.status_code, 403)

        # ...and once the window closes, neither can the owner
        connection = support.db.get_connection()
        try:
            connection.execute(
                "UPDATE events SET submissions_close = '2020-01-01T00:00:00Z' "
                "WHERE id = ?",
                (self.event_id,),
            )
            connection.commit()
        finally:
            connection.close()
        closed = self.client.patch(
            f"/api/v1/projects/{project_id}", json={"title": "too late"},
            headers=self.participant,
        )
        self.assertEqual(closed.status_code, 403)

    def test_validation_errors_are_4xx(self):
        self.client.post("/api/v1/teams", json={"name": "Team Two"},
                         headers=self.participant)
        for payload, expected in (
            ({"summary": "no title"}, 400),
            ({"title": "Bad track", "track": "trk_99"}, 400),
            ({"title": "Bad status", "status": "published"}, 400),
        ):
            response = self.client.post("/api/v1/projects", json=payload,
                                        headers=self.participant)
            self.assertEqual(response.status_code, expected, payload)


class TeamFormationTest(unittest.TestCase):
    def setUp(self):
        support.fresh_database()
        self.client = support.client()

    def test_invite_link_joins_a_team(self):
        support.db.upsert_user("free-agent@example.org", "participant")
        teammate = support.header_for_email("free-agent@example.org")

        team = support.db.get_team_by_external_id(support.event()["id"], "tm_25")
        self.assertEqual(len(team["members"]), 1)
        response = self.client.post(
            f"/teams/join/{team['invite_code']}", headers=teammate
        )
        self.assertEqual(response.status_code, 302)
        members = support.db.team_members(team["id"])
        self.assertEqual(len(members), 2)

        # joining twice is refused, with the real status, in a browser too
        again = self.client.post(f"/teams/join/{team['invite_code']}", headers=teammate)
        self.assertEqual(again.status_code, 409)
        self.assertIn("already on", again.get_data(as_text=True))
        api_again = self.client.post(
            "/api/v1/teams/join", json={"invite_code": team["invite_code"]},
            headers=teammate,
        )
        self.assertEqual(api_again.status_code, 409)

        # a second team membership is refused, on the page and on the API
        other = support.db.get_team_by_external_id(support.event()["id"], "tm_26")
        third = self.client.post(f"/teams/join/{other['invite_code']}", headers=teammate)
        self.assertEqual(third.status_code, 409)
        self.assertIn("already on team", third.get_data(as_text=True))

        fourth = self.client.post(
            "/api/v1/teams/join", json={"invite_code": other["invite_code"]},
            headers=teammate,
        )
        self.assertEqual(fourth.status_code, 409)
        self.assertIn("already on team", fourth.get_json()["message"])

    def test_a_full_team_refuses_another_member(self):
        team = support.db.get_team_by_external_id(support.event()["id"], "tm_05")
        self.assertEqual(len(team["members"]), 4)
        support.db.upsert_user("fifth@example.org", "participant")
        response = self.client.post(
            "/api/v1/teams/join",
            json={"invite_code": team["invite_code"]},
            headers=support.header_for_email("fifth@example.org"),
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("full", response.get_json()["message"])

    def test_an_unknown_code_is_a_404(self):
        support.db.upsert_user("free-agent@example.org", "participant")
        response = self.client.post(
            "/api/v1/teams/join",
            json={"invite_code": "not-a-code"},
            headers=support.header_for_email("free-agent@example.org"),
        )
        self.assertEqual(response.status_code, 404)

    def test_a_visitor_cannot_create_a_team(self):
        self.assertEqual(self.client.post("/api/v1/teams", json={"name": "x"}).status_code, 401)


class RoleIsolationTest(unittest.TestCase):
    def setUp(self):
        support.fresh_database()
        self.client = support.client()
        self.judge_a = support.header_for("judge_a")   # jdg_01
        self.judge_b = support.header_for("judge_b")   # jdg_02
        self.participant = support.header_for("participant")
        self.organizer = support.header_for("organizer")
        self.admin = support.admin_header()

    def test_a_judge_reads_their_own_scores(self):
        response = self.client.get("/api/v1/judge/scores", headers=self.judge_a)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["judge"]["external_id"], "jdg_01")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["scores"][0]["project"], "Dry Harbour")

    def test_a_judge_cannot_read_a_peers_scores(self):
        for probe in ("jdg_01", "1", "tomas.varga@example.org"):
            response = self.client.get(
                "/api/v1/judge/scores", query_string={"judge": probe},
                headers=self.judge_b,
            )
            self.assertEqual(response.status_code, 403, probe)
            self.assertIn("another judge", response.get_json()["message"])

    def test_the_refusal_is_audited(self):
        self.client.get("/api/v1/judge/scores?judge=jdg_01", headers=self.judge_b)
        actions = [row["action"] for row in support.db.recent_audit(5)]
        self.assertIn("access.peer_scores_refused", actions)

    def test_a_judge_may_read_their_own_batch_by_id(self):
        response = self.client.get(
            "/api/v1/judge/scores", query_string={"judge": "jdg_01"}, headers=self.judge_a
        )
        self.assertEqual(response.status_code, 200)

    def test_an_organizer_may_audit_any_judge(self):
        response = self.client.get(
            "/api/v1/judge/scores", query_string={"judge": "jdg_02"},
            headers=self.organizer,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["judge"]["external_id"], "jdg_02")

    def test_a_participant_is_not_a_judge(self):
        response = self.client.get("/api/v1/judge/scores", headers=self.participant)
        self.assertEqual(response.status_code, 403)

        page = self.client.get("/judge/scores", headers=self.participant)
        self.assertEqual(page.status_code, 403)
        self.assertIn("403", page.get_data(as_text=True))

    def test_a_visitor_gets_401_json_from_the_api_and_a_redirect_on_a_page(self):
        response = self.client.get("/api/v1/judge/scores")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Authentication required", response.get_json()["message"])

        page = self.client.get("/judge/scores")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/login", page.headers["Location"])

    def test_an_unknown_judge_identifier_is_a_404_not_a_leak(self):
        response = self.client.get(
            "/api/v1/judge/scores", query_string={"judge": "jdg_99"},
            headers=self.organizer,
        )
        self.assertEqual(response.status_code, 404)

    def test_csv_export_is_staff_only(self):
        response = self.client.get("/api/v1/export.csv", headers=self.organizer)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(",", body.splitlines()[0])
        self.assertIn("project_external_id", body.splitlines()[0])
        self.assertEqual(response.mimetype, "text/csv")
        self.assertGreater(len(body.splitlines()), 120)

        self.assertEqual(
            self.client.get("/api/v1/export.csv", headers=self.admin).status_code, 200
        )
        for header in (self.judge_a, self.participant):
            self.assertEqual(
                support.client().get("/api/v1/export.csv", headers=header).status_code,
                403,
            )
        self.assertEqual(support.client().get("/api/v1/export.csv").status_code, 401)

    def test_the_export_contains_every_project_and_every_score_row(self):
        body = self.client.get(
            "/api/v1/export.csv", headers=self.organizer
        ).get_data(as_text=True)
        rows = body.splitlines()[1:]
        self.assertEqual(len(rows), 126)  # one row per score
        self.assertIn("prj_01", body)
        self.assertIn("prj_41", body)  # the flagged duplicate is exported too
        self.assertIn("criterion_functionality", body.splitlines()[0])

    def test_user_management_is_admin_only(self):
        self.assertEqual(
            self.client.get("/admin/users", headers=self.admin).status_code, 200
        )
        self.assertEqual(
            self.client.get("/admin/users", headers=self.organizer).status_code, 403
        )
        self.assertEqual(
            self.client.get("/api/v1/users", headers=self.organizer).status_code, 403
        )
        self.assertEqual(
            self.client.get("/api/v1/users", headers=self.admin).status_code, 200
        )

    def test_an_admin_can_create_a_user_and_change_a_role(self):
        created = self.client.post(
            "/api/v1/users",
            json={"email": "new.judge@example.org", "role": "judge",
                  "password": "long-enough", "name": "New Judge"},
            headers=self.admin,
        )
        self.assertEqual(created.status_code, 201)
        duplicate = self.client.post(
            "/api/v1/users",
            json={"email": "new.judge@example.org", "role": "judge",
                  "password": "long-enough"},
            headers=self.admin,
        )
        self.assertEqual(duplicate.status_code, 409)
        weak = self.client.post(
            "/api/v1/users",
            json={"email": "x@example.org", "role": "judge", "password": "short"},
            headers=self.admin,
        )
        self.assertEqual(weak.status_code, 400)

        row = support.db.get_user_by_email("new.judge@example.org")
        changed = self.client.patch(
            f"/api/v1/users/{row[0]}", json={"role": "participant"}, headers=self.admin
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(support.db.get_user_by_email("new.judge@example.org")[3],
                         "participant")
        bogus = self.client.patch(
            f"/api/v1/users/{row[0]}", json={"role": "wizard"}, headers=self.admin
        )
        self.assertEqual(bogus.status_code, 400)

    def test_inactive_users_cannot_use_their_token(self):
        support.db.set_user_active(support.identities()["judge_a"].id, False)
        response = self.client.get("/api/v1/judge/scores", headers=self.judge_a)
        self.assertEqual(response.status_code, 401)


class EventManagementTest(unittest.TestCase):
    def setUp(self):
        support.fresh_database()
        self.client = support.client()
        self.organizer = support.header_for("organizer")
        self.participant = support.header_for("participant")

    def test_an_organizer_creates_an_event_with_tracks_and_prizes(self):
        response = self.client.post(
            "/api/v1/events",
            json={
                "event_name": "Winter Build 2027",
                "event_date": "2027-01-10T09:00:00Z",
                "event_location": "Online",
                "description": "Two day build.",
                "submissions_open": "2027-01-10T09:00:00Z",
                "submissions_close": "2027-01-11T18:00:00Z",
                "tracks": [{"name": "Games"}, {"name": "Tooling", "description": "CLI"}],
                "prizes": [{"name": "Grand prize", "amount": "800 USD"},
                           {"name": "Best judge", "amount": "100 USD"}],
            },
            headers=self.organizer,
        )
        self.assertEqual(response.status_code, 201)
        event_id = response.get_json()["event_id"]
        event = support.db.get_event(event_id)
        self.assertEqual(event["event_name"], "Winter Build 2027")
        self.assertEqual(event["submissions_close"], "2027-01-11T18:00:00Z")
        self.assertEqual([t["name"] for t in support.db.list_tracks(event_id)],
                         ["Games", "Tooling"])
        self.assertEqual(len(support.db.list_prizes(event_id)), 2)
        # the seeded event is still the running one
        self.assertEqual(support.db.get_primary_event()["external_id"], "evt_01")

    def test_a_new_event_can_be_made_the_running_one(self):
        created = self.client.post(
            "/api/v1/events",
            json={"event_name": "Second Event", "event_date": "2027-02-01T09:00:00Z",
                  "event_location": "Online", "submissions_close": "2027-02-02T18:00:00Z"},
            headers=self.organizer,
        )
        event_id = created.get_json()["event_id"]
        self.assertEqual(
            self.client.post(f"/api/v1/events/{event_id}/primary",
                             headers=self.organizer).status_code, 200
        )
        self.assertEqual(support.db.get_primary_event()["id"], event_id)
        # the gallery now belongs to the new, empty event
        self.assertEqual(self.client.get("/api/v1/projects").get_json()["count"], 0)
        self.assertEqual(
            self.client.post(f"/api/v1/events/{event_id}/primary",
                             headers=self.participant).status_code, 403
        )
        support.db.set_primary_event(support.db.get_event_by_external_id("evt_01")["id"])

    def test_the_html_form_creates_the_same_event(self):
        response = self.client.post(
            "/events",
            data={
                "event_name": "Form Event",
                "event_date": "2027-03-01T09:00",
                "event_location": "Lisbon",
                "description": "Created without JavaScript.",
                "submissions_close": "2027-03-02T18:00",
                "track_name": ["Climate", "Health"],
                "track_description": ["", "Anything medical"],
                "prize_name": ["First", "Second"],
                "prize_amount": ["500 EUR", "250 EUR"],
                "prize_detail": ["", "Runner up"],
            },
            headers=self.organizer,
        )
        self.assertEqual(response.status_code, 302)
        events = [row for row in support.db.get_events() if row["event_name"] == "Form Event"]
        self.assertEqual(len(events), 1)
        self.assertEqual([t["name"] for t in events[0]["tracks"]], ["Climate", "Health"])
        self.assertEqual([p["name"] for p in events[0]["prizes"]], ["First", "Second"])

    def test_a_participant_cannot_create_or_delete_an_event(self):
        self.assertEqual(
            self.client.post("/api/v1/events", json={"event_name": "x"},
                             headers=self.participant).status_code, 403
        )
        self.assertEqual(
            self.client.delete("/api/v1/delete_event/1", headers=self.participant).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete("/api/v1/delete_event/1", headers=self.organizer).status_code,
            200,
        )
        self.assertEqual(
            self.client.delete("/api/v1/delete_event/999", headers=self.organizer).status_code,
            404,
        )


class LegacyEndpointTest(unittest.TestCase):
    """The urls that were already working keep their urls and payloads."""

    def setUp(self):
        support.fresh_database()
        self.client = support.client()

    def test_login_logout_session(self):
        response = self.client.post(
            "/api/v1/login",
            json={"email": "organizer@example.local", "password": "change-me"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["user"]["role"], "organizer")
        self.assertTrue(payload["token"])

        self.assertEqual(self.client.get("/api/v1/session").get_json()["authenticated"],
                         True)
        self.assertEqual(self.client.post("/api/v1/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/session").get_json()["authenticated"],
                         False)

        bad = support.client().post(
            "/api/v1/login",
            json={"email": "organizer@example.local", "password": "wrong"},
        )
        self.assertEqual(bad.status_code, 401)

    def test_a_browser_login_redirects_instead_of_returning_json(self):
        response = self.client.post(
            "/login",
            data={"email": "tomas.varga@example.org", "password": "change-me",
                  "next": "/judge/scores"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/judge/scores")
        page = self.client.get("/judge/scores")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Tomas Varga", page.get_data(as_text=True))

    def test_the_next_parameter_cannot_leave_the_portal(self):
        response = self.client.post(
            "/login",
            data={"email": "organizer@example.local", "password": "change-me",
                  "next": "https://evil.example.org"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_get_events_keeps_its_shape(self):
        payload = self.client.get("/api/v1/get_events").get_json()
        event = payload["events"][0]
        for key in ("id", "event_name", "event_date", "event_location"):
            self.assertIn(key, event)
        self.assertEqual(event["event_name"], "Sample Hack 2026")
        self.assertEqual(len(event["tracks"]), 8)

    def test_create_event_requires_the_three_original_fields(self):
        response = self.client.post(
            "/api/v1/create_event",
            json={"event_name": "Only a name"},
            headers=support.header_for("organizer"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["message"], "Missing required fields")

        response = self.client.post(
            "/api/v1/create_event",
            json={"event_name": "Legacy", "event_date": "2027-05-01T09:00:00Z",
                  "event_location": "Online"},
            headers=support.header_for("organizer"),
        )
        self.assertEqual(response.status_code, 201)

        # a fresh client: the one above now carries the organizer's session
        self.assertEqual(
            support.client().post("/api/v1/create_event", json={}).status_code, 401
        )

    def test_a_bearer_token_authenticates_every_route(self):
        response = self.client.get(
            "/api/v1/session", headers=support.header_for("judge_b")
        )
        payload = response.get_json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["user"]["email"], "wei.lindqvist@example.org")

    def test_a_forged_token_is_refused(self):
        forged = {"Authorization": "Bearer not.a.token"}
        self.assertEqual(self.client.get("/api/v1/judge/scores", headers=forged).status_code,
                         401)
        tampered = {"Authorization": "Bearer " + support.header_for("judge_a")[
            "Authorization"].split(" ")[1][:-3] + "aaa"}
        self.assertEqual(
            self.client.get("/api/v1/judge/scores", headers=tampered).status_code, 401
        )


if __name__ == "__main__":
    unittest.main()
