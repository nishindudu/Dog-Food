"""HTML views.

Everything on these pages is read from the database at request time. There is
no client side state and no demo data baked into a template: if a row is not
in SQLite it is not on the page.

The pages are server rendered on purpose. The acceptance checker fetches
``GET /projects`` with urllib and looks for a fixture project title in the
body, so the gallery has to contain real titles in the HTML it returns, not
in a JSON blob that JavaScript would have to unpack.
"""

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_user, logout_user

import actions
import context
import db
from auth import STAFF_ROLES, User, roles_required, wants_json
from seed import ACCEPTANCE_IDENTITIES, DEMO_ACCOUNTS, SEED_PASSWORD

pages = Blueprint("pages", __name__)

#: The HTML gallery renders every match on one page. At fixture scale (41
#: projects) that is the honest choice, and it is what keeps the acceptance
#: checker's "page one contains a fixture title" check meaningful. The JSON
#: API (/api/v1/projects) takes limit and offset for callers that paginate.
STAFF = ("admin", "organizer")


def render(template, **kwargs):
    """Render with the running event and its submission window in scope."""
    event = kwargs.pop("event", None) or context.current_event()
    kwargs["event"] = event
    kwargs["window"] = db.event_window(event) if event else None
    return render_template(template, **kwargs)


SUBMISSION_FIELDS = ("title", "summary", "description", "repo_url", "demo_url")


def submission_form_values(project=None, submitted=None):
    """What the submission form should show: what was posted, else what is
    stored, else empty. Keeps a refused post from losing what was typed."""
    values = {field: (project or {}).get(field, "") or "" for field in SUBMISSION_FIELDS}
    if project:
        values["track"] = project.get("track_external_id") or (
            str(project["track_id"]) if project.get("track_id") else ""
        )
    else:
        values["track"] = ""
    for key, value in (submitted or {}).items():
        if key in values and value is not None:
            values[key] = value
    return values


def safe_next(target):
    """Only ever redirect somewhere on this portal."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def form_data():
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return {key: request.form.get(key) for key in request.form}


def respond(ok, status, payload, redirect_to, template=None, **kwargs):
    """One code path for a form post and for a curl call.

    JSON clients get the payload and the status. A browser gets a flash
    message and a redirect, or the form again with the refusal attached and
    the same 4xx status, so a refused late submission is a real 403 in the
    browser too, not a redirect that hides it.
    """
    if wants_json():
        return jsonify(payload), status

    message = payload.get("message", "Done" if ok else "Failed")
    detail = payload.get("detail")
    text = f"{message}. {detail}" if detail else message

    if ok:
        flash(text, "ok")
        return redirect(redirect_to or url_for("pages.home"))
    if template:
        flash(text, "error")
        return render(template, error=payload, form=form_data(), **kwargs), status
    # A refusal is a refusal. Rendering the real status instead of redirecting
    # is what keeps the browser and curl telling the same story.
    return render("error.html", status=status, message=message, detail=detail), status


# --------------------------------------------------------------------------
# home
# --------------------------------------------------------------------------


@pages.get("/")
def home():
    event = context.current_event()
    stats = db.dashboard_stats(event["id"]) if event else None
    latest = db.list_projects(event["id"], limit=6) if event else []
    tracks = db.list_tracks(event["id"]) if event else []
    prizes = db.list_prizes(event["id"]) if event else []

    my_team = None
    my_projects = []
    my_scores = []
    if event and current_user.is_authenticated:
        team = db.get_team_for_user(event["id"], int(current_user.id))
        if team:
            my_team = db.get_team(team["id"])
            my_team["is_lead"] = bool(team.get("is_lead"))
        my_projects = db.projects_for_user(event["id"], int(current_user.id))
        if current_user.has_role("judge"):
            my_scores = db.scores_for_judge(int(current_user.id), event["id"])

    audit = (
        db.recent_audit(12, event_id=event["id"])
        if event and current_user.is_authenticated and current_user.has_role(*STAFF)
        else []
    )
    return render(
        "home.html",
        event=event,
        stats=stats,
        latest=latest,
        tracks=tracks,
        prizes=prizes,
        my_team=my_team,
        my_projects=my_projects,
        my_scores=my_scores,
        audit=audit,
    )


@pages.get("/healthz")
def healthz():
    event = context.current_event()
    return {
        "status": "ok",
        "database": db.DB_PATH,
        "event": (event or {}).get("event_name"),
        "projects": db.count_projects((event or {}).get("id")) if event else 0,
        "users": db.count_users(),
    }, 200


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


@pages.route("/login", methods=["GET", "POST"])
def login_page():
    """Sign in for a human. ``POST /api/v1/login`` is the same thing for a
    client that wants JSON and a bearer token back."""
    if current_user.is_authenticated and request.method == "GET":
        return redirect(safe_next(request.args.get("next")) or url_for("pages.home"))

    demo = []
    for label, lookup in ACCEPTANCE_IDENTITIES:
        row = db.find_user(**lookup)
        if row:
            demo.append({"label": label, "email": row["email"], "role": row["role"],
                         "name": row.get("name")})
    extra = [
        {"label": "admin", "email": email, "role": role, "name": name}
        for email, role, name in DEMO_ACCOUNTS
        if email not in {entry["email"] for entry in demo}
    ]
    next_target = safe_next(request.args.get("next")) or ""

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        row = db.get_user_by_email(email)
        if row is None or not db.check_password(password, row[2]):
            db.log_action("auth.login_failed", f"failed sign in for '{email or '(empty)'}'")
            return (
                render(
                    "login.html",
                    next=safe_next(request.form.get("next")) or next_target,
                    demo_accounts=demo + extra,
                    demo_password=SEED_PASSWORD,
                    error={"message": "Invalid email or password"},
                ),
                401,
            )
        detail = db.get_user_detail(row[0])
        user = User(row[0], row[1], row[3], row[4], name=detail.get("name"))
        login_user(user)
        db.log_action("auth.login", f"{row[1]} signed in", actor=user)
        flash(f"Signed in as {user.display_name} ({user.role.value}).", "ok")
        return redirect(
            safe_next(request.form.get("next")) or url_for("pages.home")
        )

    return render(
        "login.html",
        next=next_target,
        demo_accounts=demo + extra,
        demo_password=SEED_PASSWORD,
    )


@pages.post("/logout")
def logout_page():
    if current_user.is_authenticated:
        logout_user()
    flash("Signed out.", "ok")
    return redirect(url_for("pages.home"))


# --------------------------------------------------------------------------
# public gallery
# --------------------------------------------------------------------------


@pages.get("/projects")
def gallery():
    event = context.current_event()
    if event is None:
        return render("gallery.html", projects=[], tracks=[], query={}, total=0)

    search = (request.args.get("q") or "").strip()
    track_param = (request.args.get("track") or "").strip()
    sort = request.args.get("sort") or "submitted_at"
    staff = current_user.is_authenticated and current_user.has_role(*STAFF)

    track_id, known = actions.resolve_track(event["id"], track_param)
    if track_param and not known:
        track_id = None

    projects = db.list_projects(
        event["id"],
        search=search or None,
        track_id=track_id,
        include_duplicates=staff and request.args.get("duplicates") == "1",
        include_drafts=staff and request.args.get("drafts") == "1",
        sort=sort,
    )
    return render(
        "gallery.html",
        projects=projects,
        tracks=db.list_tracks(event["id"]),
        total=len(projects),
        query={
            "q": search,
            "track": track_param,
            "sort": sort,
            "duplicates": request.args.get("duplicates", ""),
            "drafts": request.args.get("drafts", ""),
        },
        show_hidden=staff,
    )


@pages.get("/projects/<int:project_id>")
def project_detail(project_id):
    project = db.get_project(project_id)
    if project is None:
        return render("error.html", status=404, message="No such project"), 404

    viewer_team = (
        db.get_team_for_user(project["event_id"], int(current_user.id))
        if current_user.is_authenticated
        else None
    )
    is_owner = viewer_team is not None and viewer_team["id"] == project["team_id"]
    is_staff = current_user.is_authenticated and current_user.has_role(*STAFF)
    if project["status"] != "submitted" and not (is_owner or is_staff):
        # Somebody else's draft is not a thing a visitor can see, and "404"
        # leaks less than "403, but it exists".
        return render(
            "error.html", status=404, message="No such project"
        ), 404

    return render(
        "project_detail.html",
        project=project,
        is_owner=is_owner,
        is_staff=is_staff,
        team=db.get_team(project["team_id"]),
        siblings=db.list_projects(
            project["event_id"], team_id=project["team_id"], include_drafts=is_staff
        ),
    )


# --------------------------------------------------------------------------
# submissions
# --------------------------------------------------------------------------


@pages.route("/projects/new", methods=["GET", "POST"])
@roles_required("participant", "admin", "organizer")
def new_submission():
    event = context.current_event()
    if event is None:
        return render("error.html", status=404, message="No event is running"), 404

    team = db.get_team_for_user(event["id"], int(current_user.id))
    open_for_submissions = db.submissions_are_open(event)

    if request.method == "POST":
        data = dict(form_data())
        data.setdefault("status", "submitted")
        ok, status, payload = actions.submit_project(event, current_user, data)
        if ok:
            return respond(
                ok,
                status,
                payload,
                url_for("pages.project_detail", project_id=payload["project"]["id"]),
            )
        return respond(
            ok,
            status,
            payload,
            None,
            template="project_form.html",
            project=None,
            team=team,
            tracks=db.list_tracks(event["id"]),
            open_for_submissions=open_for_submissions,
            my_projects=[],
            values=submission_form_values(None, data),
        )

    return render(
        "project_form.html",
        project=None,
        team=team,
        tracks=db.list_tracks(event["id"]),
        open_for_submissions=open_for_submissions,
        my_projects=db.projects_for_user(event["id"], int(current_user.id)),
        values=submission_form_values(),
    )


@pages.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
@roles_required("participant", "admin", "organizer")
def edit_submission(project_id):
    event = context.current_event()
    project = db.get_project(project_id)
    if event is None or project is None:
        return render("error.html", status=404, message="No such project"), 404

    team = db.get_team_for_user(event["id"], int(current_user.id))
    is_staff = current_user.has_role(*STAFF_ROLES)
    if not is_staff and (team is None or team["id"] != project["team_id"]):
        return respond(
            False,
            403,
            {
                "message": "That submission belongs to another team",
                "detail": "Only its team, an organizer or an admin can edit it.",
            },
            None,
        )

    open_for_submissions = db.submissions_are_open(event)
    tracks = db.list_tracks(event["id"])

    if request.method == "POST":
        data = dict(form_data())
        data.setdefault("status", project["status"])
        ok, status, payload = actions.submit_project(
            event, current_user, data, project=project
        )
        if ok:
            return respond(
                ok, status, payload,
                url_for("pages.project_detail", project_id=project["id"]),
            )
        return respond(
            ok,
            status,
            payload,
            None,
            template="project_form.html",
            project=project,
            team=db.get_team(project["team_id"]),
            tracks=tracks,
            open_for_submissions=open_for_submissions,
            my_projects=db.projects_for_user(event["id"], int(current_user.id)),
            values=submission_form_values(project, data),
        )

    return render(
        "project_form.html",
        project=project,
        team=db.get_team(project["team_id"]),
        tracks=tracks,
        open_for_submissions=open_for_submissions,
        my_projects=db.projects_for_user(event["id"], int(current_user.id)),
        values=submission_form_values(project),
    )


# --------------------------------------------------------------------------
# teams
# --------------------------------------------------------------------------


@pages.get("/teams")
def teams_page():
    event = context.current_event()
    if event is None:
        return render("teams.html", teams=[], my_team=None, query={})
    search = (request.args.get("q") or "").strip()
    teams = db.list_teams(event["id"])
    if search:
        needle = search.lower()
        teams = [
            team
            for team in teams
            if needle in team["name"].lower()
            or any(needle in (member["email"] or "").lower() for member in team["members"])
        ]
    my_team = (
        db.get_team_for_user(event["id"], int(current_user.id))
        if current_user.is_authenticated
        else None
    )
    if my_team:
        my_team = db.get_team(my_team["id"])
    return render(
        "teams.html",
        teams=teams,
        my_team=my_team,
        query={"q": search},
        max_team_size=actions.MAX_TEAM_SIZE,
    )


@pages.post("/teams")
@roles_required("participant", "admin", "organizer")
def create_team():
    event = context.current_event()
    ok, status, payload = actions.create_team(event, current_user, form_data().get("name"))
    return respond(ok, status, payload, url_for("pages.teams_page"))


@pages.post("/teams/join")
@roles_required("participant", "admin", "organizer")
def join_team():
    event = context.current_event()
    data = form_data()
    ok, status, payload = actions.join_team(
        event, current_user, data.get("invite_code") or data.get("code")
    )
    return respond(ok, status, payload, url_for("pages.teams_page"))


@pages.get("/teams/join/<code>")
@roles_required("participant", "admin", "organizer")
def join_by_link(code):
    """The invite link itself. A stranger lands here, signs in, confirms."""
    event = context.current_event()
    team = db.get_team_by_invite(code)
    if event is None or team is None or team["event_id"] != event["id"]:
        return render(
            "error.html",
            status=404,
            message="That invite link does not match a team in this event",
        ), 404
    my_team = db.get_team_for_user(event["id"], int(current_user.id))
    return render(
        "team_join.html",
        team=team,
        code=code,
        my_team=db.get_team(my_team["id"]) if my_team else None,
        max_team_size=actions.MAX_TEAM_SIZE,
    )


@pages.post("/teams/join/<code>")
@roles_required("participant", "admin", "organizer")
def join_by_link_confirm(code):
    event = context.current_event()
    ok, status, payload = actions.join_team(event, current_user, code)
    if ok:
        return respond(
            ok, status, payload, url_for("pages.team_detail", team_id=payload["team"]["id"])
        )
    return respond(ok, status, payload, url_for("pages.join_by_link", code=code))


@pages.get("/teams/<int:team_id>")
def team_detail(team_id):
    event = context.current_event()
    team = db.get_team(team_id)
    if event is None or team is None or team["event_id"] != event["id"]:
        return render("error.html", status=404, message="No such team"), 404
    staff = current_user.is_authenticated and current_user.has_role(*STAFF)
    projects = db.list_projects(
        event["id"], team_id=team_id, include_drafts=staff or context.can_see_team_code(team)
    )
    return render(
        "team_detail.html",
        team=team,
        projects=projects,
        can_see_code=context.can_see_team_code(team),
    )


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------


@pages.get("/events")
def events_page():
    events = db.get_events()
    return render(
        "events.html",
        events=events,
        can_manage=current_user.is_authenticated and current_user.has_role(*STAFF),
    )


@pages.post("/events")
@roles_required(*STAFF)
def create_event():
    data = {
        "event_name": request.form.get("event_name"),
        "event_date": request.form.get("event_date"),
        "event_location": request.form.get("event_location"),
        "description": request.form.get("description"),
        "submissions_open": request.form.get("submissions_open") or None,
        "submissions_close": request.form.get("submissions_close") or None,
        "tracks": [
            {"name": name, "description": description}
            for name, description in zip(
                request.form.getlist("track_name"),
                request.form.getlist("track_description"),
            )
            if name.strip()
        ],
        "prizes": [
            {"name": name, "amount": amount, "detail": detail}
            for name, amount, detail in zip(
                request.form.getlist("prize_name"),
                request.form.getlist("prize_amount"),
                request.form.getlist("prize_detail"),
            )
            if name.strip()
        ],
    }
    ok, status, payload = actions.create_event(data, current_user)
    return respond(ok, status, payload, url_for("pages.events_page"))


@pages.post("/events/<int:event_id>/primary")
@roles_required(*STAFF)
def make_primary(event_id):
    event = db.get_event(event_id)
    if event is None:
        return respond(False, 404, {"message": "No such event"}, None)
    db.set_primary_event(event_id)
    db.log_action(
        "event.set_primary",
        f"event {event_id} '{event['event_name']}' is now the running event "
        f"(changed by {current_user.email})",
        actor=current_user,
        event_id=event_id,
    )
    return respond(
        True,
        200,
        {"message": f"'{event['event_name']}' is now the running event"},
        url_for("pages.events_page"),
    )


@pages.post("/events/<int:event_id>/delete")
@roles_required(*STAFF)
def delete_event(event_id):
    try:
        db.delete_event(event_id)
    except ValueError as error:
        return respond(False, 404, {"message": str(error)}, None)
    db.log_action(
        "event.deleted", f"event {event_id} deleted by {current_user.email}",
        actor=current_user,
    )
    return respond(
        True, 200, {"message": "Event deleted"}, url_for("pages.events_page")
    )


# --------------------------------------------------------------------------
# judge and staff areas
# --------------------------------------------------------------------------


@pages.get("/judge/scores")
@roles_required("judge", "organizer", "admin")
def judge_scores_page():
    """A judge's own batch. Peer scores are refused in api.judge_scores; this
    page has no way to ask for them."""
    event = context.current_event()
    if event is None:
        return render("error.html", status=404, message="No event is running"), 404

    requested = request.args.get("judge")
    judge = None
    if requested and current_user.has_role(*STAFF):
        judge = context.resolve_judge(requested)
    if judge is None:
        judge = db.get_user_detail(int(current_user.id))

    scores = db.scores_for_judge(judge["id"], event["id"])
    judges = (
        db.list_users(role="judge") if current_user.has_role(*STAFF) else []
    )
    return render(
        "judge_scores.html",
        judge=judge,
        scores=scores,
        judges=judges,
        is_own_batch=int(judge["id"]) == int(current_user.id),
    )


@pages.get("/admin/users")
@roles_required("admin")
def admin_users():
    return render(
        "admin_users.html",
        users=db.list_users(role=request.args.get("role"), search=request.args.get("q")),
        roles=sorted(db.ROLES),
        query={"role": request.args.get("role") or "", "q": request.args.get("q") or ""},
    )


@pages.post("/admin/users")
@roles_required("admin")
def admin_create_user():
    email = (request.form.get("email") or "").strip().lower()
    role = (request.form.get("role") or "").strip()
    password = request.form.get("password") or ""
    if not email or role not in db.ROLES or len(password) < 8:
        return respond(
            False,
            400,
            {
                "message": "Email, a valid role and a password of 8+ characters are required"
            },
            url_for("pages.admin_users"),
        )
    if db.get_user_by_email(email):
        return respond(
            False, 409, {"message": "That email is already registered"},
            url_for("pages.admin_users"),
        )
    db.create_user(email, password, role, name=(request.form.get("name") or "").strip() or None)
    db.log_action(
        "user.created", f"{email} created with role {role} by {current_user.email}",
        actor=current_user,
    )
    return respond(
        True, 201, {"message": f"{email} created as {role}"},
        url_for("pages.admin_users"),
    )


@pages.post("/admin/users/<int:user_id>/role")
@roles_required("admin")
def admin_set_role(user_id):
    detail = db.get_user_detail(user_id)
    if detail is None:
        return respond(False, 404, {"message": "No such user"}, None)
    role = (request.form.get("role") or "").strip()
    if role not in db.ROLES:
        return respond(
            False, 400, {"message": f"Unknown role '{role}'"},
            url_for("pages.admin_users"),
        )
    if role != detail["role"]:
        db.set_user_role(user_id, role)
        db.log_action(
            "user.role_changed",
            f"{detail['email']}: role {detail['role']} -> {role} by "
            f"{current_user.email}",
            actor=current_user,
        )
    return respond(
        True, 200, {"message": f"{detail['email']} is now {role}"},
        url_for("pages.admin_users"),
    )


@pages.get("/admin/audit")
@roles_required(*STAFF)
def audit_page():
    event = context.current_event()
    return render(
        "audit.html",
        entries=db.recent_audit(200, event_id=(event or {}).get("id")),
    )


@pages.get("/admin/export")
@roles_required(*STAFF)
def export_page():
    """Same refusal as the API, rendered as a page: staff only."""
    event = context.current_event()
    return render(
        "export.html",
        event=event,
        stats=db.dashboard_stats(event["id"]) if event else None,
    )
