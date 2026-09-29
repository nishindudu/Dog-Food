"""Authentication, sessions and the role model.

Lifted out of ``main.py`` unchanged in behaviour, with two additions the
DOGFOOD checker needs:

* static bearer tokens. Login tokens are timed (24 h) which is right for a
  browser session and wrong for a config file that a grader reads next week.
  Seeded demo identities therefore also get a non-expiring signed token,
  keyed by email so it survives a reseed. ``DOGFOOD_STATIC_TOKENS=0`` turns
  them off.
* content negotiation on refusals. ``/api/...`` gets JSON 401/403, a page
  gets a redirect to ``/login`` or an HTML 403.

Role isolation lives here and in the view functions, never in a template:
a template can only hide a link, it cannot refuse a curl request.
"""

import os
from enum import StrEnum
from functools import wraps

from flask import redirect, render_template, request, url_for
from flask_login import (
    AnonymousUserMixin,
    LoginManager,
    UserMixin,
    current_user,
    login_user,
)
from itsdangerous import (
    BadSignature,
    SignatureExpired,
    URLSafeSerializer,
    URLSafeTimedSerializer,
)

import db

SECRET_KEY = os.environ.get("DOGFOOD_SECRET_KEY", "your_secret_key_here")
TOKEN_MAX_AGE = 86400
STATIC_TOKENS_ENABLED = os.environ.get("DOGFOOD_STATIC_TOKENS", "1") != "0"

login_manager = LoginManager()
#: Endpoint of the HTML login page (main.py already uses "login" for the JSON
#: route /api/v1/login, so the page needs its own name).
LOGIN_ENDPOINT = "pages.login_page"
token_serializer = URLSafeTimedSerializer(SECRET_KEY)
static_serializer = URLSafeSerializer(SECRET_KEY, salt="dogfood-acceptance")


class Role(StrEnum):
    ADMIN = "admin"
    ORGANIZER = "organizer"
    JUDGE = "judge"
    PARTICIPANT = "participant"
    VISITOR = "visitor"


#: Roles that run an event rather than take part in one.
STAFF_ROLES = (Role.ADMIN, Role.ORGANIZER)


class User(UserMixin):
    def __init__(self, user_id, email, role, is_active=True, name=None):
        self.id = user_id
        self.email = email
        self.role = Role(role)
        self.name = name or ""
        self._is_active = bool(is_active)

    @property
    def is_active(self):
        return self._is_active

    @property
    def display_name(self):
        return self.name or self.email

    def has_role(self, *roles):
        return self.role in {Role(role) for role in roles}

    def is_staff(self):
        return self.has_role(*STAFF_ROLES)


class Visitor(AnonymousUserMixin):
    role = Role.VISITOR
    email = ""
    name = ""

    def has_role(self, *roles):
        return Role.VISITOR in {Role(role) for role in roles}

    @property
    def display_name(self):
        return "Visitor"

    def is_staff(self):
        return False


login_manager.anonymous_user = Visitor


@login_manager.user_loader
def load_user(user_id):
    row = db.get_user_by_id(user_id)
    if row is None:
        return None

    detail = db.get_user_detail(row[0]) or {}
    return User(row[0], row[1], row[3], row[4], name=detail.get("name"))


@login_manager.unauthorized_handler
def unauthorized():
    return refuse(401, "Authentication required")


def refuse(status, message):
    """401/403 for an API call, a usable page for a human."""
    if wants_json():
        return {"message": message}, status
    if status == 401:
        return redirect(url_for(LOGIN_ENDPOINT, next=request.full_path.rstrip("?")))
    return render_template("error.html", status=status, message=message), status


def wants_json():
    """Answer in JSON to anything that is speaking JSON.

    The acceptance checker posts a JSON body to an HTML route
    (``POST /projects/new``) and only reads the status, but a curl client
    deserves a JSON body back rather than a page of HTML.
    """
    if request.path.startswith("/api/"):
        return True
    if request.is_json:
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def create_bearer_token(user):
    """Timed token, handed to a browser or curl client after login."""
    return token_serializer.dumps({"user_id": str(user.id), "role": user.role.value})


def create_static_token(user):
    """Non-expiring token for seeded demo identities (.dogfood.toml)."""
    return static_serializer.dumps({"email": user.email, "role": user.role.value})


def user_from_token(token):
    """Resolve a bearer token to a User, or None. Static first, then timed."""
    if STATIC_TOKENS_ENABLED:
        try:
            payload = static_serializer.loads(token)
        except BadSignature:
            payload = None
        if payload and payload.get("email"):
            row = db.get_user_by_email(payload["email"])
            if row is None or row[3] != payload.get("role") or not row[4]:
                return None
            detail = db.get_user_detail(row[0]) or {}
            return User(row[0], row[1], row[3], row[4], name=detail.get("name"))

    try:
        payload = token_serializer.loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

    user = load_user(payload.get("user_id", ""))
    if user is None or user.role.value != payload.get("role"):
        return None
    return user


def authenticate_bearer_token():
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return False

    user = user_from_token(authorization.removeprefix("Bearer ").strip())
    if user is None:
        return False

    # login_user() refuses inactive users, so is_active is enforced here too.
    return login_user(user)


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not current_user.is_authenticated:
                authenticate_bearer_token()
            if not current_user.is_authenticated:
                return unauthorized()
            if not current_user.has_role(*roles):
                return refuse(403, "Insufficient permissions")
            return view(*args, **kwargs)

        return wrapped_view

    return decorator
