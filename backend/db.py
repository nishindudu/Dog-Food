"""Data access for the Dog-Food hackathon portal.

One SQLite file, standard library only. Every function opens and closes its
own connection: the portal is small, writes are rare, and this keeps the
call sites free of transaction plumbing.

Conventions
-----------
* Every id is an INTEGER rowid internally. Fixture ids ("evt_01", "prj_07")
  are kept in an ``external_id`` column so an import can be re-run and so an
  export can round trip back to fixtures.json.
* Every timestamp is an ISO 8601 string in UTC ("2026-03-01T18:00:00Z").
"""

import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

ROLES = {"admin", "organizer", "judge", "participant"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DOGFOOD_DB") or os.path.join(REPO_ROOT, "data", "events.db")

# --------------------------------------------------------------------------
# connections, timestamps
# --------------------------------------------------------------------------


def get_connection():
    directory = os.path.dirname(DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow_iso():
    return iso(utcnow())


def parse_ts(value):
    """Parse an ISO 8601 timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_ts(value):
    """Human readable UTC timestamp for templates: '1 Mar 2026, 18:00 UTC'."""
    dt = parse_ts(value)
    if dt is None:
        return ""
    return dt.strftime("%-d %b %Y, %H:%M UTC") if os.name != "nt" else dt.strftime(
        "%d %b %Y, %H:%M UTC"
    ).lstrip("0")


def _query(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _query_one(conn, sql, params=()):
    rows = _query(conn, sql, params)
    return rows[0] if rows else None


def _columns(conn, table):
    """Column names of a table, so migrations can add what is missing."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (
        role IN ('admin', 'organizer', 'judge', 'participant')
    ),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    name TEXT,
    external_id TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_location TEXT NOT NULL,
    external_id TEXT,
    description TEXT NOT NULL DEFAULT '',
    submissions_open TEXT,
    submissions_close TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    external_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    UNIQUE (event_id, name)
);

CREATE TABLE IF NOT EXISTS prizes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    amount TEXT NOT NULL DEFAULT '',
    position INTEGER
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    external_id TEXT,
    name TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (event_id, external_id)
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_lead INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    track_id INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    repo_url TEXT NOT NULL DEFAULT '',
    demo_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted')),
    submitted_at TEXT,
    duplicate_of INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (event_id, external_id)
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    judge_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    criteria_json TEXT NOT NULL DEFAULT '{}',
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, judge_id)
);

CREATE TABLE IF NOT EXISTS judge_tracks (
    judge_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    PRIMARY KEY (judge_id, track_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_email TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_id
    ON users(external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_projects_event ON projects(event_id, status);
CREATE INDEX IF NOT EXISTS idx_projects_team ON projects(team_id);
CREATE INDEX IF NOT EXISTS idx_scores_project ON scores(project_id);
CREATE INDEX IF NOT EXISTS idx_scores_judge ON scores(judge_id);
CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);
"""

# Columns added to tables that already existed in an older database file.
MIGRATIONS = {
    "users": [
        ("name", "TEXT"),
        ("external_id", "TEXT"),
    ],
    "events": [
        ("external_id", "TEXT"),
        ("description", "TEXT NOT NULL DEFAULT ''"),
        ("submissions_open", "TEXT"),
        ("submissions_close", "TEXT"),
        ("is_primary", "INTEGER NOT NULL DEFAULT 0"),
        ("created_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ],
}


def init_db():
    """Create the schema, then add any column an older database is missing."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            existing = _columns(conn, table)
            for name, declaration in columns:
                if name not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                    )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# audit trail
# --------------------------------------------------------------------------


def log_action(action, detail="", actor=None, event_id=None):
    """Append one line to the audit trail an organizer can read."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO audit_log
               (event_id, actor_id, actor_email, action, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                getattr(actor, "id", None),
                getattr(actor, "email", "") or "",
                action,
                detail,
                utcnow_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def recent_audit(limit=25, event_id=None):
    conn = get_connection()
    try:
        if event_id is None:
            return _query(
                conn,
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        return _query(
            conn,
            "SELECT * FROM audit_log WHERE event_id = ? ORDER BY id DESC LIMIT ?",
            (event_id, limit),
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


def get_user_by_id(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, email, password_hash, role, is_active FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def get_user_by_email(email):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, email, password_hash, role, is_active FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    conn.close()
    return row


def create_user(email, password, role, name=None, external_id=None):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, external_id)
               VALUES (?, ?, ?, ?, ?)""",
            (email, generate_password_hash(password), role, name, external_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_or_create_user(email, password, role, name=None, external_id=None):
    user = get_user_by_email(email)
    if user is None:
        create_user(email, password, role, name=name, external_id=external_id)
        user = get_user_by_email(email)
    return user


def upsert_user(
    email,
    role,
    password=None,
    name=None,
    external_id=None,
    password_hash=None,
):
    """Insert a user, or refresh the identity fields of one that exists.

    Used by the fixture importer. An existing password is never overwritten,
    so reseeding cannot lock anybody out. ``password_hash`` lets the importer
    hash the shared demo password once instead of once per seeded user
    (scrypt is deliberately slow, and there are 121 of them).
    """
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    conn = get_connection()
    try:
        row = _query_one(conn, "SELECT id FROM users WHERE email = ?", (email,))
        if row:
            conn.execute(
                """UPDATE users
                   SET role = ?, name = COALESCE(?, name),
                       external_id = COALESCE(?, external_id)
                   WHERE id = ?""",
                (role, name, external_id, row["id"]),
            )
            conn.commit()
            return row["id"]
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, external_id)
               VALUES (?, ?, ?, ?, ?)""",
            (
                email,
                password_hash or generate_password_hash(password or ""),
                role,
                name,
                external_id,
            ),
        )
        conn.commit()
        return _query_one(conn, "SELECT id FROM users WHERE email = ?", (email,))["id"]
    finally:
        conn.close()


def check_password(password, password_hash):
    return check_password_hash(password_hash, password)


def get_user_detail(user_id):
    conn = get_connection()
    try:
        return _query_one(
            conn,
            """SELECT id, email, role, name, external_id, is_active, created_at
               FROM users WHERE id = ?""",
            (user_id,),
        )
    finally:
        conn.close()


def find_user(email=None, external_id=None):
    conn = get_connection()
    try:
        if external_id:
            row = _query_one(
                conn,
                """SELECT id, email, role, name, external_id
                   FROM users WHERE external_id = ?""",
                (external_id,),
            )
            if row:
                return row
        if email:
            return _query_one(
                conn,
                """SELECT id, email, role, name, external_id
                   FROM users WHERE email = ?""",
                (email,),
            )
        return None
    finally:
        conn.close()


def list_users(role=None, search=None):
    sql = """SELECT id, email, role, name, external_id, is_active, created_at
             FROM users"""
    clauses, params = [], []
    if role:
        clauses.append("role = ?")
        params.append(role)
    if search:
        clauses.append("(email LIKE ? OR name LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY role, email"
    conn = get_connection()
    try:
        return _query(conn, sql, params)
    finally:
        conn.close()


def set_user_role(user_id, role):
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE users SET role = ? WHERE id = ?", (role, user_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def set_user_active(user_id, is_active):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def count_users(role=None):
    conn = get_connection()
    try:
        if role:
            return conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = ?", (role,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------


def create_event(
    event_name,
    event_date,
    event_location,
    description="",
    submissions_open=None,
    submissions_close=None,
    external_id=None,
):
    """Insert an event. Returns the new event id.

    The first three arguments are the original signature and the original
    column names; the rest are the configurable window the tier list asks
    for.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO events
               (event_name, event_date, event_location, description,
                submissions_open, submissions_close, external_id, is_primary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_name,
                event_date,
                event_location,
                description or "",
                submissions_open,
                submissions_close,
                external_id,
                1 if get_primary_event(conn=conn) is None else 0,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _event_row_to_dict(row):
    """Normalise an events row (older databases lack the newer columns)."""
    return {
        "id": row["id"],
        "event_name": row["event_name"],
        "event_date": row["event_date"],
        "event_location": row["event_location"],
        "description": row.get("description") or "",
        "submissions_open": row.get("submissions_open"),
        "submissions_close": row.get("submissions_close"),
        "external_id": row.get("external_id"),
        "is_primary": bool(row.get("is_primary")),
        "created_at": row.get("created_at"),
    }


def get_events():
    conn = get_connection()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM events ORDER BY is_primary DESC, id DESC"
        ).fetchall()
        events = []
        for row in rows:
            event = _event_row_to_dict(dict(row))
            event["tracks"] = _query(
                conn,
                "SELECT * FROM tracks WHERE event_id = ? ORDER BY name",
                (event["id"],),
            )
            event["prizes"] = _query(
                conn,
                """SELECT * FROM prizes WHERE event_id = ?
                   ORDER BY position IS NULL, position, id""",
                (event["id"],),
            )
            event["project_count"] = conn.execute(
                "SELECT COUNT(*) FROM projects WHERE event_id = ?", (event["id"],)
            ).fetchone()[0]
            event["team_count"] = conn.execute(
                "SELECT COUNT(*) FROM teams WHERE event_id = ?", (event["id"],)
            ).fetchone()[0]
            events.append(event)
        return events
    finally:
        conn.close()


def get_event(event_id):
    conn = get_connection()
    try:
        row = _query_one(conn, "SELECT * FROM events WHERE id = ?", (event_id,))
        return _event_row_to_dict(row) if row else None
    finally:
        conn.close()


def get_event_by_external_id(external_id):
    conn = get_connection()
    try:
        row = _query_one(
            conn, "SELECT * FROM events WHERE external_id = ?", (external_id,)
        )
        return _event_row_to_dict(row) if row else None
    finally:
        conn.close()


def get_primary_event(conn=None):
    """The event the portal is currently running.

    Seeding marks the fixture event primary. Creating another event does not
    steal the spotlight, because the public gallery (and the acceptance
    checker) must keep showing the seeded event until an organizer says
    otherwise.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        row = _query_one(
            conn, "SELECT * FROM events WHERE is_primary = 1 ORDER BY id LIMIT 1"
        )
        if row is None:
            row = _query_one(conn, "SELECT * FROM events ORDER BY id DESC LIMIT 1")
        return _event_row_to_dict(row) if row else None
    finally:
        if own:
            conn.close()


def set_primary_event(event_id):
    conn = get_connection()
    try:
        conn.execute("UPDATE events SET is_primary = 0")
        cursor = conn.execute(
            "UPDATE events SET is_primary = 1 WHERE id = ?", (event_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_event(event_id):
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        if cursor.rowcount == 0:
            raise ValueError(f"No event found with ID {event_id}")
        conn.commit()
    finally:
        conn.close()


def submissions_are_open(event, now=None):
    """Deadline enforcement, in one place, used by every write path.

    No ``submissions_close`` means the organizer has not set a deadline, so
    the window is open. Otherwise the window closes at that instant, and a
    submission at or after it is refused.
    """
    if not event:
        return False
    close = parse_ts(event.get("submissions_close"))
    if close is None:
        return True
    return (now or utcnow()) < close


def event_window(event):
    """Describe the submission window for templates and API responses."""
    now = utcnow()
    opens = parse_ts((event or {}).get("submissions_open"))
    closes = parse_ts((event or {}).get("submissions_close"))
    if closes is None:
        state = "open"
        message = "No submission deadline is set for this event."
    elif now >= closes:
        state = "closed"
        message = f"Submissions closed {format_ts(closes)}."
    elif opens is not None and now < opens:
        state = "upcoming"
        message = f"Submissions open {format_ts(opens)}."
    else:
        state = "open"
        message = f"Submissions close {format_ts(closes)}."
    return {
        "state": state,
        "message": message,
        "open": state == "open",
        "opens_at": (event or {}).get("submissions_open"),
        "closes_at": (event or {}).get("submissions_close"),
    }


# --------------------------------------------------------------------------
# tracks and prizes
# --------------------------------------------------------------------------


def list_tracks(event_id):
    conn = get_connection()
    try:
        return _query(
            conn,
            """SELECT t.*, (SELECT COUNT(*) FROM projects p
                             WHERE p.track_id = t.id
                               AND p.status = 'submitted'
                               AND p.duplicate_of IS NULL) AS project_count
               FROM tracks t WHERE t.event_id = ?
               ORDER BY t.name""",
            (event_id,),
        )
    finally:
        conn.close()


def get_track(track_id):
    conn = get_connection()
    try:
        return _query_one(conn, "SELECT * FROM tracks WHERE id = ?", (track_id,))
    finally:
        conn.close()


def add_track(event_id, name, description="", external_id=None):
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO tracks (event_id, external_id, name, description)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(event_id, name) DO UPDATE SET
                 description = excluded.description,
                 external_id = COALESCE(excluded.external_id, tracks.external_id)""",
            (event_id, external_id, name, description or ""),
        )
        conn.commit()
        row = _query_one(
            conn,
            "SELECT * FROM tracks WHERE event_id = ? AND name = ?",
            (event_id, name),
        )
        return row["id"] if row else cursor.lastrowid
    finally:
        conn.close()


def list_prizes(event_id):
    conn = get_connection()
    try:
        return _query(
            conn,
            """SELECT * FROM prizes WHERE event_id = ?
               ORDER BY position IS NULL, position, id""",
            (event_id,),
        )
    finally:
        conn.close()


def add_prize(event_id, name, detail="", amount="", position=None):
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO prizes (event_id, name, detail, amount, position)
               VALUES (?, ?, ?, ?, ?)""",
            (event_id, name, detail or "", amount or "", position),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


# --------------------------------------------------------------------------
# teams
# --------------------------------------------------------------------------


def new_invite_code():
    return secrets.token_urlsafe(9)


def create_team(event_id, name, external_id=None, invite_code=None):
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO teams (event_id, external_id, name, invite_code)
               VALUES (?, ?, ?, ?)""",
            (event_id, external_id, name, invite_code or new_invite_code()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def list_teams(event_id):
    conn = get_connection()
    try:
        teams = _query(
            conn,
            """SELECT t.*,
                      (SELECT COUNT(*) FROM team_members m
                        WHERE m.team_id = t.id) AS member_count,
                      (SELECT COUNT(*) FROM projects p
                        WHERE p.team_id = t.id) AS project_count
               FROM teams t WHERE t.event_id = ?
               ORDER BY t.name, t.id""",
            (event_id,),
        )
        for team in teams:
            team["members"] = team_members(team["id"])
        return teams
    finally:
        conn.close()


def get_team(team_id):
    conn = get_connection()
    try:
        team = _query_one(conn, "SELECT * FROM teams WHERE id = ?", (team_id,))
        if team:
            team["members"] = team_members(team_id)
        return team
    finally:
        conn.close()


def get_team_by_invite(invite_code):
    conn = get_connection()
    try:
        team = _query_one(
            conn, "SELECT * FROM teams WHERE invite_code = ?", (invite_code,)
        )
        if team:
            team["members"] = team_members(team["id"])
        return team
    finally:
        conn.close()


def get_team_by_external_id(event_id, external_id):
    """Same shape as get_team(), so callers can use either interchangeably."""
    conn = get_connection()
    try:
        team = _query_one(
            conn,
            "SELECT * FROM teams WHERE event_id = ? AND external_id = ?",
            (event_id, external_id),
        )
        if team:
            team["members"] = team_members(team["id"])
        return team
    finally:
        conn.close()


def get_track_by_external_id(event_id, external_id):
    conn = get_connection()
    try:
        return _query_one(
            conn,
            "SELECT * FROM tracks WHERE event_id = ? AND external_id = ?",
            (event_id, external_id),
        )
    finally:
        conn.close()


def team_members(team_id):
    conn = get_connection()
    try:
        return _query(
            conn,
            """SELECT u.id, u.email, u.name, u.role, m.is_lead, m.joined_at
               FROM team_members m JOIN users u ON u.id = m.user_id
               WHERE m.team_id = ?
               ORDER BY m.is_lead DESC, m.joined_at, u.email""",
            (team_id,),
        )
    finally:
        conn.close()


def add_team_member(team_id, user_id, is_lead=False):
    """Add a member. Returns True when the row is new."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO team_members (team_id, user_id, is_lead, joined_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(team_id, user_id) DO NOTHING""",
            (team_id, user_id, 1 if is_lead else 0, utcnow_iso()),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_team_for_user(event_id, user_id):
    conn = get_connection()
    try:
        return _query_one(
            conn,
            """SELECT t.*, m.is_lead FROM teams t
               JOIN team_members m ON m.team_id = t.id
               WHERE t.event_id = ? AND m.user_id = ?""",
            (event_id, user_id),
        )
    finally:
        conn.close()


def count_teams(event_id):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM teams WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------

PROJECT_SELECT = """
    SELECT p.*,
           t.name AS team_name,
           tr.name AS track_name,
           tr.external_id AS track_external_id,
           (SELECT COUNT(*) FROM team_members m WHERE m.team_id = p.team_id)
             AS team_member_count,
           (SELECT COUNT(*) FROM scores s WHERE s.project_id = p.id)
             AS review_count
    FROM projects p
    JOIN teams t ON t.id = p.team_id
    LEFT JOIN tracks tr ON tr.id = p.track_id
"""


def _project_dict(row):
    project = dict(row)
    project["submitted"] = project.get("status") == "submitted"
    project["submitted_at_text"] = format_ts(project.get("submitted_at"))
    project["updated_at_text"] = format_ts(project.get("updated_at"))
    return project


def list_projects(
    event_id,
    search=None,
    track_id=None,
    status=None,
    team_id=None,
    include_duplicates=False,
    include_drafts=False,
    sort="submitted_at",
    limit=None,
    offset=0,
):
    """Gallery query. The public gallery shows submitted, unflagged projects."""
    clauses = ["p.event_id = ?"]
    params = [event_id]
    if not include_duplicates:
        clauses.append("p.duplicate_of IS NULL")
    if not include_drafts:
        clauses.append("p.status = 'submitted'")
    elif status:
        clauses.append("p.status = ?")
        params.append(status)
    if track_id:
        clauses.append("p.track_id = ?")
        params.append(track_id)
    if team_id:
        clauses.append("p.team_id = ?")
        params.append(team_id)
    if search:
        clauses.append("(p.title LIKE ? OR p.summary LIKE ? OR t.name LIKE ?)")
        like = f"%{search}%"
        params += [like, like, like]

    order = {
        "submitted_at": "p.submitted_at DESC, p.id DESC",
        "title": "p.title COLLATE NOCASE, p.id",
        "team": "t.name COLLATE NOCASE, p.id",
        "track": "tr.name COLLATE NOCASE, p.title COLLATE NOCASE",
        "reviews": "review_count DESC, p.title COLLATE NOCASE",
    }.get(sort, "p.submitted_at DESC, p.id DESC")

    sql = PROJECT_SELECT + " WHERE " + " AND ".join(clauses) + f" ORDER BY {order}"
    if limit:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]

    conn = get_connection()
    try:
        return [_project_dict(row) for row in _query(conn, sql, params)]
    finally:
        conn.close()


def get_project(project_id):
    conn = get_connection()
    try:
        row = _query_one(
            conn, PROJECT_SELECT + " WHERE p.id = ?", (project_id,)
        )
        if row is None:
            return None
        project = _project_dict(row)
        project["team_members"] = team_members(project["team_id"])
        project["duplicate"] = None
        if project.get("duplicate_of"):
            project["duplicate"] = _query_one(
                conn,
                "SELECT id, title, external_id FROM projects WHERE id = ?",
                (project["duplicate_of"],),
            )
        return project
    finally:
        conn.close()


def get_project_by_external_id(event_id, external_id):
    conn = get_connection()
    try:
        row = _query_one(
            conn,
            PROJECT_SELECT + " WHERE p.event_id = ? AND p.external_id = ?",
            (event_id, external_id),
        )
        return _project_dict(row) if row else None
    finally:
        conn.close()


def find_duplicate_project(event_id, team_id, title, repo_url, exclude_id=None):
    """Same team, same repo or same title: the fixture file contains one.

    Returns the earlier project row so the newer one can be flagged instead of
    silently doubling up in the gallery.
    """
    clauses = ["event_id = ?", "team_id = ?", "duplicate_of IS NULL"]
    params = [event_id, team_id]
    if repo_url:
        clauses.append("repo_url = ?")
        params.append(repo_url)
    else:
        clauses.append("title = ?")
        params.append(title)
    if exclude_id:
        clauses.append("id != ?")
        params.append(exclude_id)
    conn = get_connection()
    try:
        return _query_one(
            conn,
            "SELECT id, title, external_id FROM projects WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id LIMIT 1",
            params,
        )
    finally:
        conn.close()


def create_project(
    event_id,
    team_id,
    title,
    summary="",
    description="",
    repo_url="",
    demo_url="",
    track_id=None,
    status="draft",
    submitted_at=None,
    external_id=None,
    duplicate_of=None,
    created_at=None,
    updated_at=None,
):
    conn = get_connection()
    try:
        if external_id:
            existing = _query_one(
                conn,
                "SELECT id FROM projects WHERE event_id = ? AND external_id = ?",
                (event_id, external_id),
            )
            if existing:
                return existing["id"]
        now = utcnow_iso()
        cursor = conn.execute(
            """INSERT INTO projects
               (event_id, team_id, track_id, external_id, title, summary,
                description, repo_url, demo_url, status, submitted_at,
                duplicate_of, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                team_id,
                track_id,
                external_id,
                title,
                summary or "",
                description or "",
                repo_url or "",
                demo_url or "",
                status,
                submitted_at,
                duplicate_of,
                created_at or now,
                updated_at or created_at or now,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_project(project_id, fields):
    """Update a whitelisted set of columns. Returns True when a row changed."""
    allowed = {
        "title",
        "summary",
        "description",
        "repo_url",
        "demo_url",
        "track_id",
        "status",
        "submitted_at",
        "team_id",
        "duplicate_of",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return False
    updates["updated_at"] = utcnow_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn = get_connection()
    try:
        cursor = conn.execute(
            f"UPDATE projects SET {assignments} WHERE id = ?",
            (*updates.values(), project_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def projects_for_user(event_id, user_id):
    """Drafts and submissions belonging to any team the user is on."""
    conn = get_connection()
    try:
        rows = _query(
            conn,
            PROJECT_SELECT
            + """ WHERE p.event_id = ? AND p.team_id IN
                  (SELECT team_id FROM team_members WHERE user_id = ?)
                ORDER BY p.status, p.updated_at DESC""",
            (event_id, user_id),
        )
        return [_project_dict(row) for row in rows]
    finally:
        conn.close()


def count_projects(event_id, status=None):
    conn = get_connection()
    try:
        if status:
            return conn.execute(
                """SELECT COUNT(*) FROM projects
                   WHERE event_id = ? AND status = ? AND duplicate_of IS NULL""",
                (event_id, status),
            ).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM projects WHERE event_id = ? AND duplicate_of IS NULL",
            (event_id,),
        ).fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# scores
# --------------------------------------------------------------------------


def _score_dict(row):
    score = dict(row)
    try:
        score["criteria"] = json.loads(score.pop("criteria_json") or "{}")
    except json.JSONDecodeError:
        score["criteria"] = {}
    values = [v for v in score["criteria"].values() if isinstance(v, (int, float))]
    score["total"] = sum(values)
    score["average"] = round(sum(values) / len(values), 2) if values else None
    return score


SCORE_SELECT = """
    SELECT s.*, u.name AS judge_name, u.email AS judge_email,
           u.external_id AS judge_external_id,
           p.title AS project_title, p.external_id AS project_external_id,
           t.name AS team_name, tr.name AS track_name
    FROM scores s
    JOIN users u ON u.id = s.judge_id
    JOIN projects p ON p.id = s.project_id
    JOIN teams t ON t.id = p.team_id
    LEFT JOIN tracks tr ON tr.id = p.track_id
"""


def upsert_score(event_id, project_id, judge_id, criteria, comment=""):
    """Store one judge's score for one project. Returns True when new."""
    conn = get_connection()
    try:
        now = utcnow_iso()
        existed = (
            conn.execute(
                "SELECT 1 FROM scores WHERE project_id = ? AND judge_id = ?",
                (project_id, judge_id),
            ).fetchone()
            is not None
        )
        conn.execute(
            """INSERT INTO scores
               (event_id, project_id, judge_id, criteria_json, comment,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, judge_id) DO UPDATE SET
                 criteria_json = excluded.criteria_json,
                 comment = excluded.comment,
                 updated_at = excluded.updated_at""",
            (
                event_id,
                project_id,
                judge_id,
                json.dumps(criteria or {}, sort_keys=True),
                comment or "",
                now,
                now,
            ),
        )
        conn.commit()
        return not existed
    finally:
        conn.close()


def scores_for_judge(judge_id, event_id=None):
    """One judge's own scores. The only score read a judge is allowed."""
    sql = SCORE_SELECT + " WHERE s.judge_id = ?"
    params = [judge_id]
    if event_id:
        sql += " AND s.event_id = ?"
        params.append(event_id)
    sql += " ORDER BY p.title COLLATE NOCASE"
    conn = get_connection()
    try:
        return [_score_dict(row) for row in _query(conn, sql, params)]
    finally:
        conn.close()


def all_scores(event_id=None):
    sql = SCORE_SELECT
    params = []
    if event_id:
        sql += " WHERE s.event_id = ?"
        params.append(event_id)
    sql += " ORDER BY p.id, s.judge_id"
    conn = get_connection()
    try:
        return [_score_dict(row) for row in _query(conn, sql, params)]
    finally:
        conn.close()


def score_count(event_id=None):
    conn = get_connection()
    try:
        if event_id:
            return conn.execute(
                "SELECT COUNT(*) FROM scores WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
    finally:
        conn.close()


def judge_track_ids(judge_id):
    conn = get_connection()
    try:
        return [
            row["track_id"]
            for row in _query(
                conn, "SELECT track_id FROM judge_tracks WHERE judge_id = ?", (judge_id,)
            )
        ]
    finally:
        conn.close()


def set_judge_tracks(judge_id, track_ids):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM judge_tracks WHERE judge_id = ?", (judge_id,))
        for track_id in track_ids:
            conn.execute(
                "INSERT OR IGNORE INTO judge_tracks (judge_id, track_id) VALUES (?, ?)",
                (judge_id, track_id),
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------


def dashboard_stats(event_id):
    conn = get_connection()
    try:
        stats = {
            "teams": conn.execute(
                "SELECT COUNT(*) FROM teams WHERE event_id = ?", (event_id,)
            ).fetchone()[0],
            "projects": conn.execute(
                """SELECT COUNT(*) FROM projects
                   WHERE event_id = ? AND duplicate_of IS NULL""",
                (event_id,),
            ).fetchone()[0],
            "submitted": conn.execute(
                """SELECT COUNT(*) FROM projects
                   WHERE event_id = ? AND status = 'submitted'
                     AND duplicate_of IS NULL""",
                (event_id,),
            ).fetchone()[0],
            "drafts": conn.execute(
                """SELECT COUNT(*) FROM projects
                   WHERE event_id = ? AND status = 'draft'
                     AND duplicate_of IS NULL""",
                (event_id,),
            ).fetchone()[0],
            "duplicates_flagged": conn.execute(
                """SELECT COUNT(*) FROM projects
                   WHERE event_id = ? AND duplicate_of IS NOT NULL""",
                (event_id,),
            ).fetchone()[0],
            "tracks": conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE event_id = ?", (event_id,)
            ).fetchone()[0],
            "prizes": conn.execute(
                "SELECT COUNT(*) FROM prizes WHERE event_id = ?", (event_id,)
            ).fetchone()[0],
            "scores": conn.execute(
                "SELECT COUNT(*) FROM scores WHERE event_id = ?", (event_id,)
            ).fetchone()[0],
            "participants": conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'participant'"
            ).fetchone()[0],
            "judges": conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'judge'"
            ).fetchone()[0],
            "judges_with_scores": conn.execute(
                """SELECT COUNT(DISTINCT judge_id) FROM scores WHERE event_id = ?""",
                (event_id,),
            ).fetchone()[0],
        }
        stats["projects_per_track"] = _query(
            conn,
            """SELECT tr.name AS track, COUNT(p.id) AS projects
               FROM tracks tr
               LEFT JOIN projects p ON p.track_id = tr.id
                    AND p.duplicate_of IS NULL AND p.status = 'submitted'
               WHERE tr.event_id = ?
               GROUP BY tr.id ORDER BY projects DESC, tr.name""",
            (event_id,),
        )
        return stats
    finally:
        conn.close()
