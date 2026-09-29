"""Import fixtures.json into the portal database and print what to log in with.

    python3 backend/seed.py                 # idempotent import
    python3 backend/seed.py --write-config  # ...and regenerate .dogfood.toml
    python3 backend/seed.py --stats         # print row counts only

The importer is keyed on the fixture ids (``external_id``), so running it
twice is a no-op rather than a second copy of the event. Nothing is deleted:
a project a participant created through the UI survives a reseed.

fixtures.json is input, not the data model. Two things it contains are
deliberately awkward and both are handled here rather than hidden:

* ``prj_41`` is a duplicate of ``prj_07`` (same team, same repo). Both rows
  are imported; the later one is flagged ``duplicate_of`` the earlier one and
  drops out of the public gallery.
* score entries are missing for most judge/project pairs, and some projects
  have two reviews while others have five. Nothing is backfilled and no
  average is invented: the review count is simply what is in the file.
"""

import argparse
import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from werkzeug.security import generate_password_hash  # noqa: E402

import db  # noqa: E402
from auth import User, create_static_token  # noqa: E402

FIXTURES_PATH = os.environ.get("DOGFOOD_FIXTURES") or os.path.join(
    db.REPO_ROOT, "fixtures.json"
)
CONFIG_PATH = os.environ.get("DOGFOOD_CONFIG") or os.path.join(
    db.REPO_ROOT, ".dogfood.toml"
)
BASE_URL = os.environ.get("DOGFOOD_BASE_URL", "http://localhost:8080")
SEED_PASSWORD = os.environ.get("DOGFOOD_SEED_PASSWORD", "change-me")

PITCH = (
    "Self-hosted hackathon portal: fixture-seeded public gallery, invite-link "
    "teams, deadline-enforced submissions and backend-enforced role isolation."
)

# Identities handed to the acceptance checker. judge_a/judge_b are fixture
# judges with different score sets, so the peer-score refusal is a real test.
ACCEPTANCE_IDENTITIES = [
    ("organizer", {"email": "organizer@example.local"}),
    ("judge_a", {"external_id": "jdg_01"}),
    ("judge_b", {"external_id": "jdg_02"}),
    ("participant", {"email": "priya1@example.org"}),
]

# The fixture file has no organizer and no admin, so the portal would have
# nobody able to create an event or read an export. These four are demo
# accounts, not fixture data, and the login page says so.
DEMO_ACCOUNTS = [
    ("admin@example.local", "admin", "Demo Admin"),
    ("organizer@example.local", "organizer", "Demo Organizer"),
    ("judge@example.local", "judge", "Demo Judge"),
    ("participant@example.local", "participant", "Demo Participant"),
]

EVENT_DESCRIPTION = (
    "Seeded from the shared DOGFOOD fixtures (fixtures.json). "
    "The submission window in the fixture file has passed, so this event is "
    "closed for new and edited submissions."
)


def load_fixtures(path=FIXTURES_PATH):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def seed_event(data, counts):
    existing = db.get_event_by_external_id(data["id"])
    if existing:
        counts["events"] = 0
        return existing

    event_id = db.create_event(
        event_name=data["name"],
        event_date=data.get("event_date") or data.get("submissions_close") or "",
        event_location=data.get("location", ""),
        description=EVENT_DESCRIPTION,
        submissions_open=data.get("submissions_open"),
        submissions_close=data.get("submissions_close"),
        external_id=data["id"],
    )
    db.set_primary_event(event_id)
    db.log_action(
        "seed.event",
        f"imported {data['id']} '{data['name']}' from fixtures.json",
        event_id=event_id,
    )
    counts["events"] = 1
    return db.get_event(event_id)


def seed_tracks(event_id, rows, counts):
    track_ids = {}
    for row in rows:
        existing = db.get_track_by_external_id(event_id, row["id"])
        if existing:
            track_ids[row["id"]] = existing["id"]
            continue
        track_ids[row["id"]] = db.add_track(
            event_id, row["name"], row.get("description", ""), external_id=row["id"]
        )
        counts["tracks"] += 1
    return track_ids


def seed_judges(rows, track_ids, password_hash, counts):
    judge_ids = {}
    for row in rows:
        is_new = db.get_user_by_email(row["email"]) is None
        user_id = db.upsert_user(
            row["email"],
            "judge",
            name=row.get("name"),
            external_id=row["id"],
            password_hash=password_hash,
        )
        judge_ids[row["id"]] = user_id
        if is_new:
            counts["judges"] += 1
        db.set_judge_tracks(
            user_id, [track_ids[t] for t in row.get("tracks", []) if t in track_ids]
        )
    return judge_ids


def seed_teams(event_id, rows, password_hash, counts):
    team_ids = {}
    for row in rows:
        team = db.get_team_by_external_id(event_id, row["id"])
        if team is None:
            team_id = db.create_team(event_id, row["name"], external_id=row["id"])
            counts["teams"] += 1
        else:
            team_id = team["id"]
        team_ids[row["id"]] = team_id

        for position, email in enumerate(row.get("members", [])):
            user_id = db.upsert_user(
                email, "participant", password_hash=password_hash
            )
            if db.add_team_member(team_id, user_id, is_lead=(position == 0)):
                counts["members"] += 1
    return team_ids


def seed_projects(event_id, rows, team_ids, track_ids, event, counts):
    close = db.parse_ts(event.get("submissions_close"))
    project_ids = {}
    for row in rows:
        existing = db.get_project_by_external_id(event_id, row["id"])
        if existing:
            project_ids[row["id"]] = existing["id"]
            continue

        team_id = team_ids.get(row["team"])
        if team_id is None:
            counts["skipped"] += 1
            continue
        submitted_at = row.get("submitted_at")
        if close and db.parse_ts(submitted_at) and db.parse_ts(submitted_at) > close:
            db.log_action(
                "import.late_submission",
                f"{row['id']} '{row['title']}' is timestamped after the deadline; "
                "imported as-is and flagged in the audit trail",
                event_id=event_id,
            )
            counts["late"] += 1

        duplicate_of = None
        duplicate = db.find_duplicate_project(
            event_id, team_id, row["title"], row.get("repo_url", "")
        )
        if duplicate:
            duplicate_of = duplicate["id"]
            db.log_action(
                "import.duplicate_flagged",
                f"{row['id']} '{row['title']}' duplicates project "
                f"{duplicate['id']} ('{duplicate['title']}') from the same team; "
                "kept, flagged, hidden from the public gallery",
                event_id=event_id,
            )
            counts["duplicates"] += 1

        project_ids[row["id"]] = db.create_project(
            event_id=event_id,
            team_id=team_id,
            title=row["title"],
            summary=row.get("summary", ""),
            description=row.get("description", ""),
            repo_url=row.get("repo_url", ""),
            demo_url=row.get("demo_url", ""),
            track_id=track_ids.get(row.get("track")),
            status="submitted",
            submitted_at=submitted_at,
            external_id=row["id"],
            duplicate_of=duplicate_of,
            # An imported row was last touched when it was submitted, not when
            # the importer ran: "edited 5 minutes ago" on a February project
            # would be a lie the UI tells.
            created_at=submitted_at,
            updated_at=submitted_at,
        )
        counts["projects"] += 1
    return project_ids


def seed_scores(event_id, rows, judge_ids, project_ids, counts):
    for row in rows:
        judge_id = judge_ids.get(row["judge"])
        project_id = project_ids.get(row["project"])
        if judge_id is None or project_id is None:
            counts["skipped"] += 1
            continue
        inserted = db.upsert_score(
            event_id, project_id, judge_id, row.get("criteria", {}), row.get("comment", "")
        )
        if inserted:
            counts["scores"] += 1


def seed_demo_accounts(password_hash):
    ids = {}
    for email, role, name in DEMO_ACCOUNTS:
        ids[email] = db.upsert_user(
            email, role, name=name, password_hash=password_hash
        )
    return ids


def seed(fixtures=None, verbose=True):
    """Import everything. Returns the row counts it touched."""
    fixtures = fixtures or load_fixtures()
    db.init_db()

    # One hash for every seeded account: they all share SEED_PASSWORD, and
    # hashing it 121 times would make `docker compose up` crawl.
    password_hash = generate_password_hash(SEED_PASSWORD)
    counts = {
        "events": 0,
        "tracks": 0,
        "judges": 0,
        "teams": 0,
        "members": 0,
        "projects": 0,
        "duplicates": 0,
        "late": 0,
        "scores": 0,
        "skipped": 0,
    }

    event = seed_event(fixtures.get("event", {}), counts)
    event_id = event["id"]
    track_ids = seed_tracks(event_id, fixtures.get("tracks", []), counts)
    judge_ids = seed_judges(
        fixtures.get("judges", []), track_ids, password_hash, counts
    )
    team_ids = seed_teams(event_id, fixtures.get("teams", []), password_hash, counts)
    project_ids = seed_projects(
        event_id, fixtures.get("projects", []), team_ids, track_ids, event, counts
    )
    seed_scores(event_id, fixtures.get("scores", []), judge_ids, project_ids, counts)
    seed_demo_accounts(password_hash)

    db.log_action(
        "seed.finished",
        "fixtures.json import complete: "
        + (", ".join(f"{key}={value}" for key, value in counts.items() if value)
           or "nothing new, database already seeded"),
        event_id=event_id,
    )
    if verbose:
        created = {key: value for key, value in counts.items() if value}
        print(f"event: {event['event_name']} (id {event_id})")
        print(
            "  created: "
            + (", ".join(f"{key} {value}" for key, value in created.items())
               or "nothing (already imported)")
        )
        totals = db.dashboard_stats(event_id)
        print(
            "  database now holds: "
            + ", ".join(
                f"{key} {totals[key]}"
                for key in ("teams", "projects", "submitted", "drafts", "tracks",
                            "scores", "judges", "participants")
            )
        )
        if totals["duplicates_flagged"]:
            print(
                f"  {totals['duplicates_flagged']} duplicate submission flagged "
                "and hidden from the public gallery"
            )
    return event, counts


# --------------------------------------------------------------------------
# what to log in with
# --------------------------------------------------------------------------


def acceptance_identities():
    """Resolve the four checker identities to users."""
    identities = {}
    for label, lookup in ACCEPTANCE_IDENTITIES:
        row = db.find_user(**lookup)
        if row is None:
            raise SystemExit(
                f"seed.py: cannot build .dogfood.toml, no user for {label} "
                f"({lookup}). Run the import first."
            )
        detail = db.get_user_detail(row["id"])
        identities[label] = User(
            detail["id"], detail["email"], detail["role"], detail["is_active"],
            name=detail.get("name"),
        )
    return identities


def print_credentials():
    identities = acceptance_identities()
    print("\nAcceptance headers for .dogfood.toml (non-expiring, signed):")
    for label, user in identities.items():
        print(f"  {label:<12} Authorization: Bearer {create_static_token(user)}")

    print(f"\nDemo logins (password '{SEED_PASSWORD}' for every seeded user):")
    for label, user in identities.items():
        name = f" ({user.name})" if user.name else ""
        print(f"  {label:<12} {user.email}{name}  role={user.role.value}")
    for email, role, name in DEMO_ACCOUNTS:
        if email not in {user.email for user in identities.values()}:
            print(f"  {'demo':<12} {email} ({name})  role={role}")
    print(
        "\nEvery fixture judge and participant can log in with their "
        f"example.org address and the password '{SEED_PASSWORD}'."
    )


def render_config():
    identities = acceptance_identities()
    lines = [
        "# .dogfood.toml",
        "# Generated by `python3 backend/seed.py --write-config`.",
        "# Run the checker with:  python3 run.py .dogfood.toml",
        "",
        "[portal]",
        f'base_url = "{BASE_URL}"',
        "",
        "[tiers]",
        '# T1 is complete and verified. T2 is not claimed: there is no',
        '# organizer-configurable weighted rubric, no judge assignment strategy',
        '# and no cross-judge normalization yet. Two T2 endpoints exist',
        '# (judge score isolation and CSV export) and are listed below so the',
        '# checker reports them honestly instead of probing the wrong url.',
        'claimed = ["T1"]',
        f'pitch = "{PITCH}"',
        "",
        "[auth]",
        "# Signed bearer tokens for the seeded identities. They do not expire,",
        "# so this file stays valid after a restart. The login page and the",
        "# boot log print the same values.",
    ]
    for label, user in identities.items():
        lines.append(
            f'{label:<12}= "Authorization: Bearer {create_static_token(user)}"'
        )
    lines += [
        "",
        "[routes]",
        'gallery      = "/projects"',
        'submit       = "/projects/new"',
        'judge_scores = "/api/v1/judge/scores"',
        "# judge_a's scores, requested as judge_b: refused in the backend.",
        'peer_scores  = "/api/v1/judge/scores?judge=jdg_01"',
        'csv_export   = "/api/v1/export.csv"',
        "",
    ]
    return "\n".join(lines)


def write_config(path=CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_config())
    print(f"\nwrote {os.path.relpath(path, db.REPO_ROOT)}")


def main():
    parser = argparse.ArgumentParser(description="Seed the Dog-Food portal")
    parser.add_argument("--fixtures", default=FIXTURES_PATH, help="path to fixtures.json")
    parser.add_argument("--write-config", action="store_true", help="write .dogfood.toml")
    parser.add_argument("--quiet", action="store_true", help="only print credentials")
    parser.add_argument("--stats", action="store_true", help="print row counts and exit")
    args = parser.parse_args()

    if args.stats:
        event = db.get_primary_event()
        if event is None:
            print("database is empty, run: python3 backend/seed.py")
            return 0
        for key, value in db.dashboard_stats(event["id"]).items():
            if not isinstance(value, list):
                print(f"{key:<22} {value}")
        return 0

    seed(load_fixtures(args.fixtures), verbose=not args.quiet)
    print_credentials()
    if args.write_config:
        write_config()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
