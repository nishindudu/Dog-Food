"""Shared test plumbing.

One temporary SQLite file for the whole run, wiped and reseeded from
fixtures.json by each test module, so modules cannot see each other's
writes. The portal's real ``data/events.db`` is never touched: the database
path is chosen before ``backend.db`` is imported.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
FIXTURES = os.path.join(REPO_ROOT, "fixtures.json")

_DATABASE_PATH = os.path.join(tempfile.mkdtemp(prefix="dogfood-tests-"), "test.db")
os.environ["DOGFOOD_DB"] = _DATABASE_PATH
os.environ.setdefault("DOGFOOD_FIXTURES", FIXTURES)

for path in (BACKEND_DIR, os.path.dirname(os.path.abspath(__file__))):
    if path not in sys.path:
        sys.path.insert(0, path)

import db  # noqa: E402
import main  # noqa: E402
import seed  # noqa: E402
from auth import create_static_token  # noqa: E402


def fresh_database():
    """Empty the database, then import fixtures.json into it."""
    if os.path.exists(_DATABASE_PATH):
        os.remove(_DATABASE_PATH)
    db.init_db()
    seed.seed(verbose=False)
    return _DATABASE_PATH


def client():
    """A test client with no cookies: every request stands alone, exactly
    like the acceptance checker's urllib calls."""
    return main.app.test_client()


def identities():
    """The four identities .dogfood.toml hands to the checker."""
    return seed.acceptance_identities()


def header_for(label, identities=None):
    user = (identities or seed.acceptance_identities())[label]
    return {"Authorization": f"Bearer {create_static_token(user)}"}


def admin_header():
    row = db.get_user_by_email("admin@example.local")
    detail = db.get_user_detail(row[0])
    from auth import User

    user = User(detail["id"], detail["email"], detail["role"], detail["is_active"],
                name=detail.get("name"))
    return {"Authorization": f"Bearer {create_static_token(user)}"}


def header_for_email(email):
    """A header for any seeded account (every fixture user shares the seed
    password, so any of them can be used to test a role)."""
    row = db.get_user_by_email(email)
    if row is None:
        raise AssertionError(f"no seeded user for {email}")
    detail = db.get_user_detail(row[0])
    from auth import User

    user = User(detail["id"], detail["email"], detail["role"], detail["is_active"],
                name=detail.get("name"))
    return {"Authorization": f"Bearer {create_static_token(user)}"}


def event():
    return db.get_primary_event()
