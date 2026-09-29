"""JSON API.

The endpoints that were already in ``main.py`` stay there untouched. These are
the new ones the portal frontend, curl and the acceptance checker use.

Two of them are the ones the DOGFOOD checker probes, and both refuse in the
backend:

* ``GET /api/v1/judge/scores?judge=jdg_01`` as another judge -> 403
* ``GET /api/v1/judge/scores`` as a participant -> 403 (role decorator)
* ``GET /api/v1/export.csv`` as anyone but staff -> 403
"""

import csv
import io

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user

import actions
import context
import db
from auth import create_bearer_token, create_static_token, roles_required

api = Blueprint("api", __name__)

STAFF = ("admin", "organizer")


def _event_or_404():
    event = context.current_event()
    if event is None:
        return None, ({"message": "No event is running"}, 404)
    return event, None


def _form_or_json():
    """Accept a JSON body or an HTML form post with the same shape."""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    if request.form:
        merged = {key: request.form.get(key) for key in request.form}
        merged["tracks"] = request.form.getlist("tracks[]") or None
        return merged
    return {}


@api.get("/api/v1/portal")
def portal():
    """Everything the dashboard needs: event, window, tracks, prizes, counts."""
    event = context.current_event()
    if event is None:
        return {"event": None, "message": "No event has been created yet"}, 200
    return {
        "event": context.event_payload(event, with_stats=True),
        "viewer": {
            "authenticated": current_user.is_authenticated,
            "role": current_user.role.value,
            "email": getattr(current_user, "email", ""),
        },
    }, 200


@api.get("/api/v1/tracks")
def tracks():
    event, error = _event_or_404()
    if error:
        return error
    return {"tracks": db.list_tracks(event["id"])}, 200


@api.get("/api/v1/projects")
def projects():
    """Public project list. Drafts and flagged duplicates stay out unless a
    staff member asks for them."""
    event, error = _event_or_404()
    if error:
        return error
    staff = current_user.is_authenticated and current_user.has_role(*STAFF)
    track_id, known = actions.resolve_track(event["id"], request.args.get("track"))
    if request.args.get("track") and not known:
        return {"message": "Unknown track", "tracks": db.list_tracks(event["id"])}, 400

    try:
        limit = min(int(request.args.get("limit", 100)), 500)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return {"message": "limit and offset must be integers"}, 400

    rows = db.list_projects(
        event["id"],
        search=request.args.get("q"),
        track_id=track_id,
        include_duplicates=staff and request.args.get("duplicates") == "1",
        include_drafts=staff and request.args.get("drafts") == "1",
        sort=request.args.get("sort", "submitted_at"),
        limit=limit,
        offset=offset,
    )
    return {
        "event": {"id": event["id"], "name": event["event_name"]},
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "projects": [context.project_payload(row) for row in rows],
    }, 200


@api.get("/api/v1/projects/<int:project_id>")
def project_detail(project_id):
    project = db.get_project(project_id)
    if project is None:
        return {"message": "No such project"}, 404
    if project["status"] != "submitted" and not (
        current_user.is_authenticated and current_user.has_role(*STAFF)
    ):
        # A draft is visible to its own team and to staff, to nobody else.
        team = db.get_team_for_user(
            project["event_id"], int(current_user.id)
        ) if current_user.is_authenticated else None
        if team is None or team["id"] != project["team_id"]:
            return {"message": "That submission is still a draft"}, 404
    return {"project": context.project_payload(project)}, 200


@api.post("/api/v1/projects")
@roles_required("participant", "admin", "organizer")
def create_project():
    event, error = _event_or_404()
    if error:
        return error
    ok, status, payload = actions.submit_project(event, current_user, _form_or_json())
    if ok:
        payload = dict(payload)
        payload["project"] = context.project_payload(payload["project"])
    return jsonify(payload), status


@api.patch("/api/v1/projects/<int:project_id>")
@api.post("/api/v1/projects/<int:project_id>")
@roles_required("participant", "admin", "organizer")
def update_project(project_id):
    event, error = _event_or_404()
    if error:
        return error
    project = db.get_project(project_id)
    if project is None:
        return {"message": "No such project"}, 404
    ok, status, payload = actions.submit_project(
        event, current_user, _form_or_json(), project=project
    )
    if ok:
        payload = dict(payload)
        payload["project"] = context.project_payload(payload["project"])
    return jsonify(payload), status


@api.get("/api/v1/teams")
def teams():
    event, error = _event_or_404()
    if error:
        return error
    rows = db.list_teams(event["id"])
    return {
        "count": len(rows),
        "teams": [context.team_payload(row, current_user) for row in rows],
    }, 200


@api.post("/api/v1/teams")
@roles_required("participant", "admin", "organizer")
def create_team():
    event, error = _event_or_404()
    if error:
        return error
    data = _form_or_json()
    ok, status, payload = actions.create_team(event, current_user, data.get("name"))
    if ok:
        payload = dict(payload)
        payload["team"] = context.team_payload(payload["team"], current_user)
    return jsonify(payload), status


@api.post("/api/v1/teams/join")
@roles_required("participant", "admin", "organizer")
def join_team():
    event, error = _event_or_404()
    if error:
        return error
    data = _form_or_json()
    ok, status, payload = actions.join_team(
        event, current_user, data.get("invite_code") or data.get("code")
    )
    if ok:
        payload = dict(payload)
        payload["team"] = context.team_payload(payload["team"], current_user)
    return jsonify(payload), status


@api.get("/api/v1/me")
def me():
    """Who am I, what team am I on, what have I submitted."""
    if not current_user.is_authenticated:
        return {"authenticated": False, "role": "visitor"}, 200
    event = context.current_event()
    payload = {
        "authenticated": True,
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role.value,
        "token": create_bearer_token(current_user),
    }
    if event:
        payload["event_id"] = event["id"]
        team = db.get_team_for_user(event["id"], int(current_user.id))
        if team:
            full = db.get_team(team["id"])
            payload["team"] = context.team_payload(full, current_user)
        payload["projects"] = [
            context.project_payload(row)
            for row in db.projects_for_user(event["id"], int(current_user.id))
        ]
        if current_user.has_role("judge"):
            payload["scores"] = [
                context.score_payload(row)
                for row in db.scores_for_judge(int(current_user.id), event["id"])
            ]
    return payload, 200


@api.get("/api/v1/judge/scores")
@roles_required("judge", "organizer", "admin")
def judge_scores():
    """A judge reads their own scores. Nobody else's.

    ``?judge=`` exists so an organizer can audit one judge's batch. A judge
    asking for a colleague's batch is refused here, in the backend, and the
    refusal is written to the audit trail: hiding the link in a template
    would not stop a curl request.
    """
    event, error = _event_or_404()
    if error:
        return error

    requested = request.args.get("judge")
    if requested:
        target = context.resolve_judge(requested)
        if target is None:
            return {"message": f"No judge matches '{requested}'"}, 404
        if not current_user.has_role(*STAFF) and int(target["id"]) != int(
            current_user.id
        ):
            db.log_action(
                "access.peer_scores_refused",
                f"{current_user.email} ({current_user.role.value}) requested the "
                f"scores of judge {target['email']}; refused",
                actor=current_user,
                event_id=event["id"],
            )
            return (
                {
                    "message": "A judge cannot read another judge's scores",
                    "detail": "Role isolation is enforced here, in the backend. "
                    "Only an organizer or an admin can read a judge's batch.",
                    "requested_judge": target.get("external_id") or target["email"],
                },
                403,
            )
        judge = target
    else:
        judge = db.get_user_detail(int(current_user.id))

    scores = db.scores_for_judge(judge["id"], event["id"])
    return {
        "judge": {
            "id": judge["id"],
            "email": judge["email"],
            "name": judge.get("name"),
            "external_id": judge.get("external_id"),
        },
        "event": {"id": event["id"], "name": event["event_name"]},
        "count": len(scores),
        "scores": [context.score_payload(row) for row in scores],
    }, 200


@api.get("/api/v1/export.csv")
@roles_required(*STAFF)
def export_csv():
    """Long-format results export: one row per project x score.

    Projects nobody reviewed still appear once, with empty score columns, so
    an organizer can see the gaps instead of guessing at them. Criterion
    columns are derived from the data, because the rubric is not fixed.
    """
    event, error = _event_or_404()
    if error:
        return error

    projects = db.list_projects(
        event["id"], include_duplicates=True, include_drafts=True
    )
    scores = db.all_scores(event["id"])
    by_project = {}
    for score in scores:
        by_project.setdefault(score["project_id"], []).append(score)

    criteria_names = sorted(
        {name for score in scores for name in score.get("criteria", {})}
    )
    header = (
        [
            "project_id",
            "project_external_id",
            "title",
            "team",
            "track",
            "status",
            "duplicate_of",
            "submitted_at",
        ]
        + [f"criterion_{name}" for name in criteria_names]
        + [
            "score_total",
            "score_average",
            "review_count",
            "judge_id",
            "judge_external_id",
            "judge_email",
            "judge_name",
            "comment",
        ]
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for project in projects:
        project_scores = by_project.get(project["id"], [])
        base = [
            project["id"],
            project.get("external_id") or "",
            project["title"],
            project.get("team_name") or "",
            project.get("track_name") or "",
            project.get("status") or "",
            project.get("duplicate_of") or "",
            project.get("submitted_at") or "",
        ]
        if not project_scores:
            writer.writerow(
                base + [""] * len(criteria_names) + ["", "", 0, "", "", "", "", ""]
            )
            continue
        for score in project_scores:
            criteria = score.get("criteria", {})
            writer.writerow(
                base
                + [criteria.get(name, "") for name in criteria_names]
                + [
                    score.get("total", ""),
                    score.get("average", ""),
                    len(project_scores),
                    score["judge_id"],
                    score.get("judge_external_id") or "",
                    score.get("judge_email") or "",
                    score.get("judge_name") or "",
                    score.get("comment") or "",
                ]
            )

    db.log_action(
        "export.csv",
        f"{current_user.email} exported {len(projects)} projects and "
        f"{len(scores)} scores for event {event['id']}",
        actor=current_user,
        event_id=event["id"],
    )
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{event["event_name"].replace(" ", "-").lower()}-results.csv"'
        },
    )


@api.get("/api/v1/audit")
@roles_required(*STAFF)
def audit():
    event = context.current_event()
    limit = min(int(request.args.get("limit", 100)), 500)
    rows = db.recent_audit(limit, event_id=(event or {}).get("id"))
    return {"count": len(rows), "entries": rows}, 200


@api.post("/api/v1/events")
@roles_required(*STAFF)
def create_event():
    ok, status, payload = actions.create_event(_form_or_json(), current_user)
    return jsonify(payload), status


@api.post("/api/v1/events/<int:event_id>/primary")
@roles_required(*STAFF)
def make_event_primary(event_id):
    event = db.get_event(event_id)
    if event is None:
        return {"message": "No such event"}, 404
    db.set_primary_event(event_id)
    db.log_action(
        "event.set_primary",
        f"event {event_id} '{event['event_name']}' is now the running event "
        f"(changed by {current_user.email})",
        actor=current_user,
        event_id=event_id,
    )
    return {"message": f"'{event['event_name']}' is now the running event"}, 200


@api.post("/api/v1/events/<int:event_id>/tracks")
@roles_required(*STAFF)
def add_track(event_id):
    if db.get_event(event_id) is None:
        return {"message": "No such event"}, 404
    data = _form_or_json()
    name = (data.get("name") or "").strip()
    if not name:
        return {"message": "A track name is required"}, 400
    track_id = db.add_track(event_id, name, data.get("description", ""))
    db.log_action(
        "track.added",
        f"track {track_id} '{name}' added to event {event_id} by "
        f"{current_user.email}",
        actor=current_user,
        event_id=event_id,
    )
    return {"message": f"Track '{name}' added", "track_id": track_id}, 201


@api.post("/api/v1/events/<int:event_id>/prizes")
@roles_required(*STAFF)
def add_prize(event_id):
    if db.get_event(event_id) is None:
        return {"message": "No such event"}, 404
    data = _form_or_json()
    name = (data.get("name") or "").strip()
    if not name:
        return {"message": "A prize name is required"}, 400
    prize_id = db.add_prize(
        event_id, name, data.get("detail", ""), data.get("amount", ""),
        position=data.get("position"),
    )
    db.log_action(
        "prize.added",
        f"prize {prize_id} '{name}' added to event {event_id} by "
        f"{current_user.email}",
        actor=current_user,
        event_id=event_id,
    )
    return {"message": f"Prize '{name}' added", "prize_id": prize_id}, 201


@api.get("/api/v1/users")
@roles_required("admin")
def users():
    return {
        "users": db.list_users(
            role=request.args.get("role"), search=request.args.get("q")
        )
    }, 200


@api.post("/api/v1/users")
@roles_required("admin")
def create_user():
    data = _form_or_json()
    email = (data.get("email") or "").strip().lower()
    role = (data.get("role") or "").strip()
    password = data.get("password") or ""
    if not email or role not in db.ROLES:
        return {
            "message": "email and a valid role are required",
            "roles": sorted(db.ROLES),
        }, 400
    if len(password) < 8:
        return {"message": "Password must be at least 8 characters"}, 400
    if db.get_user_by_email(email):
        return {"message": "That email is already registered"}, 409
    db.create_user(email, password, role, name=(data.get("name") or "").strip() or None)
    db.log_action(
        "user.created", f"{email} created with role {role} by {current_user.email}",
        actor=current_user,
    )
    return {"message": f"{email} created as {role}"}, 201


@api.patch("/api/v1/users/<int:user_id>")
@roles_required("admin")
def update_user(user_id):
    detail = db.get_user_detail(user_id)
    if detail is None:
        return {"message": "No such user"}, 404
    data = _form_or_json()
    changed = []
    if data.get("role") and data["role"] != detail["role"]:
        if data["role"] not in db.ROLES:
            return {"message": f"Unknown role '{data['role']}'"}, 400
        db.set_user_role(user_id, data["role"])
        changed.append(f"role {detail['role']} -> {data['role']}")
    if "is_active" in data:
        active = bool(data["is_active"])
        if active != bool(detail["is_active"]):
            db.set_user_active(user_id, active)
            changed.append(f"is_active -> {active}")
    if changed:
        db.log_action(
            "user.updated",
            f"{detail['email']}: {', '.join(changed)} by {current_user.email}",
            actor=current_user,
        )
    return {"message": ", ".join(changed) or "Nothing to change"}, 200


@api.get("/api/v1/tokens")
@roles_required(*STAFF, "judge", "participant")
def tokens():
    """Hand the signed acceptance headers to whoever is logged in.

    The checker never logs in, so these are printed at boot and written into
    .dogfood.toml; this endpoint is the same thing for a human with a browser.
    """
    return {
        "session_token": f"Bearer {create_bearer_token(current_user)}",
        "static_token": f"Bearer {create_static_token(current_user)}",
        "note": "The session token expires after 24 hours. The static token "
        "does not; it exists for .dogfood.toml and for demo logins.",
    }, 200
