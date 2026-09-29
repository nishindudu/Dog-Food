from functools import wraps
from flask import Flask, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask_login import (
    LoginManager,
    UserMixin,
    AnonymousUserMixin,
    current_user,
    login_user,
    logout_user,
    login_required,
)
import db
from enum import StrEnum

app = Flask(__name__, template_folder="../frontend", static_folder="../frontend/static")

app.config["SECRET_KEY"] = "your_secret_key_here" 

login_manager = LoginManager()
login_manager.init_app(app)
TOKEN_MAX_AGE = 86400
token_serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

db.init_db()

class Role(StrEnum):
    ADMIN = "admin"
    ORGANIZER = "organizer"
    JUDGE = "judge"
    PARTICIPANT = "participant"
    VISITOR = "visitor"


class User(UserMixin):
    def __init__(self, user_id, email, role, is_active=True):
        self.id = user_id
        self.email = email
        self.role = Role(role)
        self._is_active = bool(is_active)

    @property
    def is_active(self):
        return self._is_active

    def has_role(self, *roles):
        return self.role in {Role(role) for role in roles}


class Visitor(AnonymousUserMixin):
    role = Role.VISITOR

    def has_role(self, *roles):
        return Role.VISITOR in {Role(role) for role in roles}


login_manager.anonymous_user = Visitor


@login_manager.user_loader
def load_user(user_id):
    row = db.get_user_by_id(user_id)
    if row is None:
        return None

    return User(row[0], row[1], row[3], row[4])


@login_manager.unauthorized_handler
def unauthorized():
    return {"message": "Authentication required"}, 401


def create_bearer_token(user):
    return token_serializer.dumps({"user_id": str(user.id), "role": user.role.value})


def authenticate_bearer_token():
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return False

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = token_serializer.loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False

    user = load_user(payload.get("user_id", ""))
    if user is None or user.role.value != payload.get("role"):
        return False

    login_user(user)
    return True


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            authenticate_bearer_token()
            if not current_user.is_authenticated:
                return unauthorized()
            if not current_user.has_role(*roles):
                return {"message": "Insufficient permissions"}, 403
            return view(*args, **kwargs)

        return wrapped_view

    return decorator


@app.route('/')
def home():
    return render_template("index.html")

@app.route("/api/v1/create_event",methods=["POST"])
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

@app.route("/api/v1/get_events",methods=["GET"])
def get_events():
    events = db.get_events()
    return {"events": events}, 200


@app.route("/api/v1/delete_event/<int:event_id>", methods=["DELETE"])
@roles_required("admin", "organizer") # Check if same organizer created the event
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

    login_user(User(user[0], user[1], user[3], user[4]))

    return {
        "message": "Logged in successfully",
        "user": {
            "id": user[0],
            "email": user[1],
            "role": user[3],
        },
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


def print_development_tokens():
    print("Development bearer tokens (valid for 24 hours):")
    for role in (Role.ADMIN, Role.ORGANIZER, Role.JUDGE, Role.PARTICIPANT):
        email = f"{role.value}@example.local"
        row = db.get_or_create_user(email, "change-me", role.value)
        user = User(row[0], row[1], row[3], row[4])
        print(f"{role.value}: Bearer {create_bearer_token(user)}")
    print("visitor: anonymous (no bearer token)")


if __name__ == "__main__":
    print_development_tokens()
    app.run(debug=True, use_reloader=False)