# Architecture

How the portal is put together, and why it is put together that way.

```
                        docker compose up
                                │
                                ▼
        ┌───────────────────────────────────────────────┐
        │ waitress / Flask dev server   :8080           │
        │                                               │
        │  backend/main.py     app, boot, legacy routes │
        │        │                                      │
        │        ├── before_request: bearer token ──────┼──► auth.py
        │        │                                      │      roles, tokens,
        │        ├── pages.py    HTML views (Jinja) ────┼──►   roles_required,
        │        │        │                             │      refusals
        │        │        ├── actions.py  write rules ──┼──► deadline, teams,
        │        │        │        │                    │      events
        │        ├── api.py      JSON views ────────────┤
        │        │        │                             │
        │        │        └── context.py  JSON shapes ──┤
        │        │                  │                   │
        │        ▼                  ▼                   │
        │  db.py  ── every query, schema, migrations    │
        └────────┬──────────────────────────────────────┘
                 ▼
        data/events.db  (SQLite, one file, no server)
                 ▲
                 │  first boot only, idempotent
        fixtures.json ── backend/seed.py
```

Five direct dependencies: Flask, Flask-Login, Werkzeug, itsdangerous, waitress.
No ORM, no build step, no CDN, no container other than the app.

## The four rules this design follows

**1. A rule is written once, or it is not a rule.**
Every write to a submission goes through `actions.submit_project`. The HTML
form and the JSON API are two thin callers of the same function, so the
deadline check, the team check, the track validation and the audit line cannot
drift apart. The same is true of `actions.create_team`, `actions.join_team` and
`actions.create_event`. When the brief says "deadline enforcement that actually
holds", holding means there is one place to look.

**2. The backend decides, the template only displays.**
`auth.roles_required` decorates the view; a template has no way to grant
access. A judge asking for a peer's scores is refused in `api.judge_scores`
before any query runs, and the refusal is written to `audit_log`. Public
serializers (`context.project_payload`, `context.team_payload`) do not include
scores or invite codes at all, so a future template cannot leak them by
accident — the field is not in the dict.

**3. Pages are server rendered.**
The acceptance checker fetches `GET /projects` with `urllib` and looks for a
fixture title in the body. A JSON-plus-JavaScript gallery would return an empty
shell and fail. Server rendering also means no build step, no node_modules, and
a portal that works with JavaScript disabled — which matters when the rule is
"it must run offline on a laptop".

**4. Refusals tell the truth.**
`pages.respond` is the single exit for a form post. A JSON client gets the
payload and the status. A browser gets a flash and a redirect on success, and
on failure gets the form again (or an error page) **with the real 4xx status**.
A late submission is a `403` in the address bar, not a redirect that hides it.

## Module boundaries

| Module | Owns | Does not |
| --- | --- | --- |
| `main.py` | app object, boot, seeding trigger, error handlers, the three original event routes and login/logout/session | new features |
| `auth.py` | `Role`, `User`, `Visitor`, token signing and verification, `roles_required`, content-negotiated refusals | queries |
| `db.py` | schema, migrations, every SQL statement, timestamp helpers | HTTP, policy |
| `actions.py` | write policy: deadline, team membership, team size, validation, audit lines for writes | rendering, SQL |
| `context.py` | which event is running, JSON shapes, what a viewer may see | policy |
| `api.py` | JSON endpoints | policy (delegates to `actions`) |
| `pages.py` | HTML endpoints | policy (delegates to `actions`) |
| `seed.py` | fixtures import, credential printing, `.dogfood.toml` generation | serving |

`main.py`, `pages.py` and `api.py` never contain a SQL string; `db.py` never
contains an `if` about a role.

The three routes that already existed (`/api/v1/create_event`,
`/api/v1/get_events`, `/api/v1/delete_event/<id>`) plus `login`, `logout` and
`session` were left in `main.py` with their urls, payloads and decorators
unchanged. `get_events()` returns the four original keys and now also carries
tracks, prizes and counts, which is additive for any existing caller.

## Authentication

Two credentials, one identity model:

* **Session cookie** — Flask-Login, signed with `SECRET_KEY`. What a browser
  gets from `/login` (a server-side POST, so it works without JavaScript) or
  from `POST /api/v1/login`.
* **Bearer token** — `itsdangerous` signed payloads. Two flavours:
  * *timed*, 24 h, minted at login, returned by `POST /api/v1/login` and
    `GET /api/v1/tokens`;
  * *static*, non-expiring, keyed by **email** rather than row id so it
    survives a reseed, minted only for seeded identities and written into
    `.dogfood.toml`. `DOGFOOD_STATIC_TOKENS=0` disables them.

A `before_request` hook accepts a bearer token on any route, and an explicit
`Authorization` header wins over a session cookie: the identity you present is
the identity you get. Tokens are verified by signature, then the user is loaded
from the database and the role in the token is compared with the role on the
row, so a token cannot promote anybody. `login_user()` refuses inactive users,
so deactivating an account kills its tokens too.

Five roles: `admin`, `organizer`, `judge`, `participant` are rows constrained
by a SQL `CHECK`; `visitor` is the anonymous user class (`auth.Visitor`), which
carries the same `has_role` interface so views never special-case "not signed
in".

## Deadline enforcement

`db.submissions_are_open(event)` compares `submissions_close` with `utcnow()`.
No deadline set means open. `actions.submit_project` calls it before anything
else, for create *and* edit, and `actions.submission_window_closed` records the
attempt:

```
submission.refused_create   priya1@example.org tried to create a submission;
                            Submissions closed 1 Mar 2026, 18:00 UTC.
```

The fixture event closed on 2026-03-01T18:00:00Z, so a seeded portal refuses
submissions immediately — which is exactly what the checker's third T1 probe
expects, with no clock manipulation on either side.

## Teams and invites

A team belongs to an event and has a random `invite_code` (`secrets`, 12
characters). The code is a capability: it is serialized only for members and
staff (`context.can_see_team_code`), never into a public payload — there is a
test that walks every public page and asserts no code appears. Joining is
refused with `409` when you are already on a team, when the team is full
(`DOGFOOD_MAX_TEAM_SIZE`, default 4) or when the code belongs to another event.

## Data

One SQLite file, `PRAGMA foreign_keys = ON`, one connection per call (opened
and closed inside each `db` function). At this scale — 41 projects, 126 score
rows, 125 users — connection churn costs nothing and buys the absence of any
transaction plumbing in the views.

Fixture ids live in `external_id` columns, so:

* the import is idempotent (re-run it after editing `fixtures.json`);
* an export can round trip back to the fixture shape;
* the API accepts `trk_04`, a track name or an internal id interchangeably
  (`actions.resolve_track`), which keeps urls readable and stable.

Timestamps are ISO 8601 UTC strings, parsed by one helper (`db.parse_ts`) that
tolerates `Z`, offsets and naive values. Schema evolution is `CREATE TABLE IF
NOT EXISTS` plus an explicit `MIGRATIONS` map that `ALTER TABLE ADD COLUMN`s
anything an older database file is missing — so a checkout from before this
build opens instead of crashing. Details in [DATA-MODEL.md](DATA-MODEL.md).

## Audit trail

`audit_log` is append-only from the application's point of view: nothing in the
codebase updates or deletes a row. It records the useful events (submission
created/drafted/updated, refusal with reason, team created/joined, event
created/deleted/switched, track and prize added, user created, role changed,
login, failed login, CSV export, peer-score refusal, import flags) with actor
email and event id, and it is readable at `/admin/audit` and
`GET /api/v1/audit`. It is the part of "judging integrity" that costs almost
nothing and is the hardest to retrofit.

## Deployment

`docker compose up --build` → one service, one port, one named volume for the
database. The image runs as a non-root user, has a standard-library
`HEALTHCHECK` against `/healthz`, and its `CMD` is `python backend/main.py`,
which seeds the empty database and prints the acceptance headers before it
listens. `waitress` serves it; `DOGFOOD_DEBUG=1` switches to the Flask dev
server with a reloader for development, and a missing waitress falls back
rather than failing to boot.

No network access is needed at runtime. The only network use is `pip install`
during the image build, which is why `requirements.txt` pins transitive
versions too.

## What we would change next

* **Judge assignment** is the missing centre of T2: `judge_tracks` already
  stores each judge's declared tracks and nothing reads it. An assignment pass
  (track-aware, load-balanced, conflict-free) plus an `assignments` table is
  the next schema change.
* **Rubric configuration** wants a `criteria` table (`event_id, name, weight,
  position`) and a `score_values` child of `scores` instead of `criteria_json`.
  The JSON column was the right call for importing fixtures; it is the wrong
  call for a weighted rubric an organizer edits.
* **Normalization** belongs in a `results` module with tests over the fixture
  data, not in a view. See [JUDGING.md](JUDGING.md) for the method we would
  defend.
* **Postgres** behind the same `db.py` interface if more than one event runs at
  a time; the query functions are the only thing that would change.
* **CSRF protection** on the cookie-session forms before this faces a network.
