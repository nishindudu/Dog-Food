"""Write operations, in one place.

The HTML forms and the JSON API both call these functions, so a rule only has
to be written once to hold everywhere. That matters most for the deadline:
there is exactly one place that decides whether a submission is allowed, and
it is server side, before any row is touched.

Every function returns ``(ok, status, payload)``. ``payload`` is JSON-shaped
and is also what the HTML views put into a flash message, so a refusal reads
the same in a browser and in curl.
"""

import os

import db
from context import submission_deadline_error

MAX_TEAM_SIZE = int(os.environ.get("DOGFOOD_MAX_TEAM_SIZE", "4"))
PROJECT_STATUSES = ("draft", "submitted")
TEXT_FIELDS = ("title", "summary", "description", "repo_url", "demo_url")


def _user_id(user):
    return int(user.id)


def submission_window_closed(event, user, action):
    """Refuse and record an attempt to write after the deadline."""
    db.log_action(
        f"submission.refused_{action}",
        f"{getattr(user, 'email', 'anonymous')} tried to {action} a submission; "
        f"{db.event_window(event)['message']}",
        actor=user if getattr(user, "is_authenticated", False) else None,
        event_id=(event or {}).get("id"),
    )
    return False, 403, submission_deadline_error(event)


def resolve_track(event_id, track):
    """Accept a track id, a fixture id ("trk_04") or a track name."""
    if track in (None, ""):
        return None, True
    text = str(track).strip()
    for candidate in db.list_tracks(event_id):
        if text in (
            str(candidate["id"]),
            candidate.get("external_id") or "",
            candidate["name"],
        ):
            return candidate["id"], True
    return None, False


def with_stored_defaults(data, project):
    """PATCH semantics: a field that is absent keeps the value in the database.

    Without this, editing one line of a summary would silently blank the
    title, the track and the repository url.
    """
    merged = dict(data)
    for field in TEXT_FIELDS:
        if merged.get(field) is None:
            merged[field] = project.get(field) or ""
    if merged.get("track") is None:
        merged["track"] = project.get("track_external_id") or project.get("track_id")
    if merged.get("status") is None:
        merged["status"] = project.get("status") or "draft"
    return merged


def submit_project(event, user, data, project=None):
    """Create or update a submission. Draft and edit are allowed until the
    deadline; after it, every write is refused."""
    if event is None:
        return False, 404, {"message": "No event is running"}

    if project is not None:
        data = with_stored_defaults(data, project)

    if not db.submissions_are_open(event):
        verb = "edit" if project else "create"
        return submission_window_closed(event, user, verb)

    if project is None:
        team = db.get_team_for_user(event["id"], _user_id(user))
        if team is None:
            return False, 409, {
                "message": "You are not on a team for this event",
                "detail": "Create a team or join one with an invite link first.",
            }
        team_id = team["id"]
    else:
        team_id = project["team_id"]
        if not user.has_role("admin", "organizer"):
            team = db.get_team_for_user(event["id"], _user_id(user))
            if team is None or team["id"] != team_id:
                return False, 403, {
                    "message": "That submission belongs to another team",
                    "detail": "Only members of the submitting team, an "
                    "organizer or an admin can edit it.",
                }

    title = (data.get("title") or "").strip()
    if not title:
        return False, 400, {"message": "A project title is required"}
    if len(title) > 120:
        return False, 400, {"message": "Title must be 120 characters or fewer"}

    track_id, known = resolve_track(event["id"], data.get("track"))
    if not known:
        return False, 400, {
            "message": "Unknown track",
            "detail": f"'{data.get('track')}' is not a track of this event.",
        }

    status = (data.get("status") or "draft").strip()
    if status not in PROJECT_STATUSES:
        return False, 400, {
            "message": "Unknown status",
            "detail": f"status must be one of {', '.join(PROJECT_STATUSES)}",
        }

    fields = {
        "title": title,
        "summary": (data.get("summary") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "repo_url": (data.get("repo_url") or "").strip(),
        "demo_url": (data.get("demo_url") or "").strip(),
        "track_id": track_id,
        "status": status,
    }

    if project is None:
        duplicate_of = None
        duplicate = db.find_duplicate_project(
            event["id"], team_id, title, fields["repo_url"]
        )
        if duplicate:
            duplicate_of = duplicate["id"]
        if status == "submitted":
            fields["submitted_at"] = db.utcnow_iso()
        project_id = db.create_project(
            event_id=event["id"], team_id=team_id, duplicate_of=duplicate_of, **fields
        )
        db.log_action(
            f"submission.{'created' if status == 'submitted' else 'drafted'}",
            f"project {project_id} '{title}' by {user.email}"
            + (
                f", flagged as a duplicate of project {duplicate_of}"
                if duplicate_of
                else ""
            ),
            actor=user,
            event_id=event["id"],
        )
        created = db.get_project(project_id)
        return True, 201, {
            "message": "Submission saved"
            if status == "submitted"
            else "Draft saved",
            "project": created,
        }

    if status == "submitted" and not project.get("submitted_at"):
        fields["submitted_at"] = db.utcnow_iso()
    db.update_project(project["id"], fields)
    db.log_action(
        "submission.updated",
        f"project {project['id']} '{title}' edited by {user.email} "
        f"(status {project.get('status')} -> {status})",
        actor=user,
        event_id=event["id"],
    )
    return True, 200, {
        "message": "Submission updated",
        "project": db.get_project(project["id"]),
    }


def create_team(event, user, name):
    if event is None:
        return False, 404, {"message": "No event is running"}
    name = (name or "").strip()
    if not name:
        return False, 400, {"message": "A team name is required"}
    if len(name) > 60:
        return False, 400, {"message": "Team name must be 60 characters or fewer"}
    if db.get_team_for_user(event["id"], _user_id(user)):
        return False, 409, {
            "message": "You are already on a team for this event",
            "detail": "Leave it before creating another one.",
        }

    team_id = db.create_team(event["id"], name)
    db.add_team_member(team_id, _user_id(user), is_lead=True)
    team = db.get_team(team_id)
    db.log_action(
        "team.created",
        f"team {team_id} '{name}' created by {user.email}",
        actor=user,
        event_id=event["id"],
    )
    return True, 201, {"message": f"Team '{name}' created", "team": team}


def join_team(event, user, invite_code):
    if event is None:
        return False, 404, {"message": "No event is running"}
    team = db.get_team_by_invite((invite_code or "").strip())
    if team is None or team["event_id"] != event["id"]:
        return False, 404, {
            "message": "That invite link does not match a team in this event"
        }
    current = db.get_team_for_user(event["id"], _user_id(user))
    if current:
        if current["id"] == team["id"]:
            return False, 409, {"message": "You are already on that team"}
        return False, 409, {
            "message": f"You are already on team '{current['name']}'",
            "detail": "Leave it before joining another one.",
        }
    if len(team["members"]) >= MAX_TEAM_SIZE:
        return False, 409, {
            "message": f"That team is full ({MAX_TEAM_SIZE} members)",
            "detail": "Ask another team, or create your own.",
        }

    db.add_team_member(team["id"], _user_id(user))
    db.log_action(
        "team.joined",
        f"{user.email} joined team {team['id']} '{team['name']}' by invite link",
        actor=user,
        event_id=event["id"],
    )
    return True, 200, {
        "message": f"You joined '{team['name']}'",
        "team": db.get_team(team["id"]),
    }


def create_event(data, user):
    """Create an event with its tracks and prizes in one call."""
    name = (data.get("event_name") or data.get("name") or "").strip()
    date = (data.get("event_date") or "").strip()
    if not name or not date:
        return False, 400, {"message": "event_name and event_date are required"}

    event_id = db.create_event(
        event_name=name,
        event_date=date,
        event_location=(data.get("event_location") or "").strip(),
        description=(data.get("description") or "").strip(),
        submissions_open=(data.get("submissions_open") or None),
        submissions_close=(data.get("submissions_close") or None),
    )
    for track in data.get("tracks") or []:
        track_name = (track.get("name") if isinstance(track, dict) else track) or ""
        if track_name.strip():
            db.add_track(
                event_id,
                track_name.strip(),
                (track.get("description", "") if isinstance(track, dict) else "")
                or "",
            )
    for position, prize in enumerate(data.get("prizes") or [], start=1):
        if not isinstance(prize, dict):
            prize = {"name": prize}
        if (prize.get("name") or "").strip():
            db.add_prize(
                event_id,
                prize["name"].strip(),
                prize.get("detail", ""),
                prize.get("amount", ""),
                position=position,
            )
    db.log_action(
        "event.created",
        f"event {event_id} '{name}' created by {user.email} with "
        f"{len(data.get('tracks') or [])} tracks and "
        f"{len(data.get('prizes') or [])} prizes",
        actor=user,
        event_id=event_id,
    )
    return True, 201, {"message": f"Event '{name}' created", "event_id": event_id}
