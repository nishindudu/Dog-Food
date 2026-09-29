"""Dog-Food portal: application entry point.

Run it:

    python3 backend/seed.py     # optional, the app seeds itself on boot
    python3 backend/main.py     # http://localhost:8080

What was here before is still here: the login/logout/session routes and the
three event routes keep their urls, their payloads and their decorators. The
role model, tokens and ``roles_required`` moved to ``auth.py`` unchanged so
``pages.py`` and ``api.py`` can share them; new HTML views live in
``pages.py`` and new JSON endpoints in ``api.py``.
"""

import os
import sys

from flask import Flask, jsonify, render_template, request
from flask_login import current_user, login_required, login_user, logout_user

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import db  # noqa: E402
import seed  # noqa: E402
from api import api  # noqa: E402
from auth import (  # noqa: E402
    LOGIN_ENDPOINT,
    SECRET_KEY,
    Role,
    User,
    authenticate_bearer_token,
    create_bearer_token,
    login_manager,
    roles_required,
    wants_json,
)
from pages import pages  # noqa: E402

app = Flask(
    __name__, template_folder="../frontend", static_folder="../frontend/static"
)

app.config["SECRET_KEY"] = SECRET_KEY

login_manager.init_app(app)
login_manager.login_view = LOGIN_ENDPOINT

app.register_blueprint(pages)
app.register_blueprint(api)

db.init_db()


@app.context_processor
def template_helpers():
    """Things templates need and should not compute themselves."""
    return {
        "utc_now": lambda: db.format_ts(db.utcnow_iso()),
        "event_window": db.event_window,
    }


@app.template_filter("datetime")
def datetime_filter(value):
    """ISO 8601 in the database, '1 Mar 2026, 18:00 UTC' on the page."""
    return db.format_ts(value)


@app.before_request
def accept_bearer_header():
    """A bearer token authenticates any request, not just decorated ones.

    The acceptance checker attaches one header and never logs in, and curl
    should behave the same way as a browser session. An explicit
    ``Authorization`` header wins over a session cookie: the identity you
    present is the identity you get, which is the only predictable rule when
    both are in play.
    """
    if request.headers.get("Authorization", "").startswith("Bearer "):
        authenticate_bearer_token()
    return None


# --------------------------------------------------------------------------
# routes that were here before the DOGFOOD build, urls and payloads unchanged
# --------------------------------------------------------------------------


@app.route("/api/v1/create_event", methods=["POST"])
@roles_required("admin", "organizer")
def create_event():
    data = request.get_json()
    event_name = data.get("event_name")
    event_date = data.get("event_date")
    event_location = data.get("event_location")

    if not event_name or not event_date or not event_location:
        return {"message": "Missing required fields"}, 400
    # Call the database function to create the event
    db.create_event(event_name, event_date, event_location)

    return {"message": "Event created successfully"}, 201


@app.route("/api/v1/get_events", methods=["GET"])
def get_events():
    events = db.get_events()
    return {"events": events}, 200


@app.route("/api/v1/delete_event/<int:event_id>", methods=["DELETE"])
@roles_required("admin", "organizer")  # Check if same organizer created the event
def delete_event(event_id):
    # Call the database function to delete the event
    try:
        db.delete_event(event_id)
    except ValueError as e:
        return {"message": str(e)}, 404

    return {"message": "Event deleted successfully"}, 200


@app.post("/api/v1/login")
def login():
    data = request.get_json() or {}
    user = db.get_user_by_email(data.get("email", ""))

    if user is None or not db.check_password(
        data.get("password", ""),
        user[2],
    ):
        return {"message": "Invalid credentials"}, 401

    logged_in = User(user[0], user[1], user[3], user[4])
    login_user(logged_in)
    db.log_action(
        "auth.login", f"{user[1]} signed in", actor=logged_in
    )

    return {
        "message": "Logged in successfully",
        "user": {
            "id": user[0],
            "email": user[1],
            "role": user[3],
        },
        # Bearer form of the same session, for curl and for the checker.
        "token": create_bearer_token(logged_in),
    }, 200


@app.post("/api/v1/logout")
@login_required
def logout():
    logout_user()
    return {"message": "Logged out successfully"}, 200


@app.get("/api/v1/session")
def session():
    if not current_user.is_authenticated:
        return {"authenticated": False}, 200

    return {
        "authenticated": True,
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "role": current_user.role,
        },
    }, 200


ROLE_PERMISSIONS = {
    Role.ADMIN: {"manage_users", "manage_events", "manage_system"},
    Role.ORGANIZER: {"create_events", "edit_own_events", "delete_own_events"},
    Role.JUDGE: {"submit_scores", "view_assigned_projects"},
    Role.PARTICIPANT: {"create_projects", "edit_own_projects"},
    Role.VISITOR: {"view_public_events"},
}


# --------------------------------------------------------------------------
# errors: JSON for the API, a real page for a human
# --------------------------------------------------------------------------


def error_response(status, message):
    if wants_json():
        return jsonify({"message": message}), status
    return (
        render_template("error.html", status=status, message=message),
        status,
    )


@app.errorhandler(400)
def bad_request(error):
    return error_response(400, getattr(error, "description", "Bad request"))


@app.errorhandler(403)
def forbidden(error):
    return error_response(403, getattr(error, "description", "Forbidden"))


@app.errorhandler(404)
def not_found(error):
    return error_response(404, getattr(error, "description", "Page not found"))


@app.errorhandler(405)
def method_not_allowed(error):
    return error_response(405, getattr(error, "description", "Method not allowed"))


@app.errorhandler(500)
def server_error(error):
    return error_response(500, "Internal server error")


# --------------------------------------------------------------------------
# boot
# --------------------------------------------------------------------------


def ensure_seeded():
    """Import fixtures.json on first boot so the portal is never empty.

    Idempotent: with an event already in the database this does nothing, so a
    restart never overwrites what a participant typed.
    """
    if db.get_primary_event() is not None:
        return False
    if not os.path.exists(seed.FIXTURES_PATH):
        print(
            f"boot: no event in the database and no fixtures at "
            f"{seed.FIXTURES_PATH}; create one at /events",
            file=sys.stderr,
        )
        return False
    print(f"boot: empty database, importing {seed.FIXTURES_PATH}")
    seed.seed(verbose=True)
    return True


def print_development_tokens():
    """Print what to log in with, and the headers for .dogfood.toml."""
    print("-" * 72)
    try:
        seed.print_credentials()
    except SystemExit as error:  # no seeded identities yet
        print(f"boot: {error}")
    print("-" * 72)


def serve(host, port, debug):
    """waitress when it is installed, the Flask development server when you
    ask for it (or when waitress is missing)."""
    if debug:
        print(f"Flask development server on http://{host}:{port} (debug=1)")
        app.run(host=host, port=port, debug=True, use_reloader=True)
        return

    try:
        from waitress import serve as waitress_serve
    except ImportError:
        print(
            "waitress is not installed, falling back to the Flask development "
            "server (pip install -r requirements.txt)"
        )
        app.run(host=host, port=port, debug=False, use_reloader=False)
        return

    print(f"waitress serving http://{host}:{port}")
    waitress_serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    # The boot log carries the acceptance headers, so it must not sit in a
    # buffer when stdout is a pipe (`docker compose logs`, a CI capture).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    ensure_seeded()
    print_development_tokens()
    serve(
        host=os.environ.get("DOGFOOD_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT") or os.environ.get("DOGFOOD_PORT") or 8080),
        debug=os.environ.get("DOGFOOD_DEBUG", "0") == "1",
    )
