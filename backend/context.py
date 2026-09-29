"""Shared view context: which event we are running, and the JSON shapes.

Both the HTML views (``pages.py``) and the JSON API (``api.py``) build their
responses from these helpers, so a field that is hidden for a visitor is
hidden the same way in both.

The rule that matters: an invite code is a capability. It is only ever
serialised for a member of that team or for staff, never into a public
gallery payload.
"""

from flask_login import current_user

import db


def current_event():
    """The event the portal is currently running, or None on an empty database."""
    return db.get_primary_event()


def can_see_team_code(team, user=None):
    user = user or current_user
    if not team or not getattr(user, "is_authenticated", False):
        return False
    if user.has_role("admin", "organizer"):
        return True
    return any(member["id"] == int(user.id) for member in team.get("members", []))


def event_payload(event, with_stats=False):
    if event is None:
        return None
    payload = dict(event)
    payload["tracks"] = db.list_tracks(event["id"])
    payload["prizes"] = db.list_prizes(event["id"])
    payload["window"] = db.event_window(event)
    payload["team_count"] = db.count_teams(event["id"])
    payload["project_count"] = db.count_projects(event["id"], status="submitted")
    if with_stats:
        payload["stats"] = db.dashboard_stats(event["id"])
    return payload


def project_payload(project):
    """Public shape of a project. Scores and invite codes are never included."""
    if project is None:
        return None
    return {
        "id": project["id"],
        "external_id": project.get("external_id"),
        "title": project["title"],
        "summary": project.get("summary", ""),
        "description": project.get("description", ""),
        "repo_url": project.get("repo_url", ""),
        "demo_url": project.get("demo_url", ""),
        "status": project.get("status"),
        "track": project.get("track_name"),
        "track_id": project.get("track_id"),
        "team": project.get("team_name"),
        "team_id": project.get("team_id"),
        "team_size": project.get("team_member_count"),
        "review_count": project.get("review_count", 0),
        "submitted_at": project.get("submitted_at"),
        "updated_at": project.get("updated_at"),
        "duplicate_of": project.get("duplicate_of"),
    }


def team_payload(team, viewer=None):
    if team is None:
        return None
    payload = {
        "id": team["id"],
        "external_id": team.get("external_id"),
        "name": team["name"],
        "event_id": team.get("event_id"),
        "members": [
            {
                "id": member["id"],
                "email": member["email"],
                "name": member.get("name"),
                "is_lead": bool(member.get("is_lead")),
            }
            for member in team.get("members", [])
        ],
        "member_count": team.get(
            "member_count", len(team.get("members", []))
        ),
        "project_count": team.get("project_count"),
    }
    if can_see_team_code(team, viewer):
        payload["invite_code"] = team["invite_code"]
    return payload


def score_payload(score):
    return {
        "id": score["id"],
        "project_id": score["project_id"],
        "project": score.get("project_title"),
        "project_external_id": score.get("project_external_id"),
        "team": score.get("team_name"),
        "track": score.get("track_name"),
        "judge_id": score["judge_id"],
        "judge": score.get("judge_name") or score.get("judge_email"),
        "judge_external_id": score.get("judge_external_id"),
        "criteria": score.get("criteria", {}),
        "total": score.get("total"),
        "average": score.get("average"),
        "comment": score.get("comment", ""),
        "updated_at": score.get("updated_at"),
    }


def resolve_judge(identifier):
    """Look a judge up by internal id, fixture id ("jdg_01") or email."""
    if identifier in (None, ""):
        return None
    text = str(identifier).strip()
    if text.isdigit():
        detail = db.get_user_detail(int(text))
        if detail and detail["role"] == "judge":
            return detail
    row = db.find_user(email=text, external_id=text)
    if row and row["role"] == "judge":
        return db.get_user_detail(row["id"])
    return None


def submission_deadline_error(event):
    """The refusal every write path returns once the window has closed."""
    window = db.event_window(event)
    return {
        "message": "Submissions are closed for this event",
        "detail": window["message"],
        "submissions_close": window["closes_at"],
    }
