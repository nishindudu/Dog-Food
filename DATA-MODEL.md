# Data model

One SQLite file (`data/events.db`, `DOGFOOD_DB` to move it). Nine tables, six
indexes, no ORM. `fixtures.json` is input, not the schema: it is imported,
transformed and stored in the shape below.

```
events ─┬─< tracks ──< judge_tracks >── users(role='judge')
        ├─< prizes
        ├─< teams ──< team_members >── users(role='participant')
        └─< projects >── teams
                  │  └── tracks
                  └─< scores >── users(role='judge')

users        (admin, organizer, judge, participant; visitor = not a row)
audit_log    append-only, references events and users loosely
```

## Conventions

* Internal primary keys are `INTEGER` rowids. Fixture ids (`evt_01`, `trk_04`,
  `jdg_01`, `tm_01`, `prj_07`) are kept in `external_id`, which is what makes
  the import re-runnable and the export reversible.
* Timestamps are ISO 8601 UTC strings (`2026-03-01T18:00:00Z`). `created_at`
  columns that predate this build keep SQLite's `CURRENT_TIMESTAMP` format;
  everything new is written by `db.utcnow_iso()`.
* Booleans are `INTEGER` 0/1 (SQLite has no boolean type).
* Cascades: deleting an event removes its tracks, prizes, teams, projects and
  scores. Deleting a user removes their team memberships and scores. `audit_log`
  keeps `actor_email` as text so a deleted user still reads correctly.

## Tables

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `email` | TEXT UNIQUE NOT NULL | login identifier |
| `password_hash` | TEXT NOT NULL | werkzeug scrypt |
| `role` | TEXT NOT NULL | `CHECK (role IN ('admin','organizer','judge','participant'))` |
| `is_active` | INTEGER NOT NULL DEFAULT 1 | `0` invalidates sessions and tokens |
| `created_at` | TEXT | |
| `name` | TEXT | added by migration; fixtures have names for judges only |
| `external_id` | TEXT | fixture id, unique where not null |

`visitor` is deliberately not a row: it is `auth.Visitor`, the anonymous user
class, so the five-role model exists without inventing accounts for people who
have not signed in.

### `events`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `event_name`, `event_date`, `event_location` | TEXT NOT NULL | the original three columns, names kept so `/api/v1/get_events` and `/api/v1/create_event` did not change |
| `external_id` | TEXT | `evt_01` for the seeded event |
| `description` | TEXT DEFAULT '' | |
| `submissions_open`, `submissions_close` | TEXT | the submission window; `NULL` close means open-ended |
| `is_primary` | INTEGER DEFAULT 0 | the event the portal is running |
| `created_at` | TEXT | |

`is_primary` exists so that creating an event does not silently move the public
gallery (and the acceptance checker) onto an empty one. An organizer switches
events explicitly: `POST /api/v1/events/<id>/primary` or the *make running*
button on `/events`. `event_location` is `NOT NULL` for legacy reasons and the
fixtures do not supply one, so the seeded event stores an empty string and the
UI prints "not set" rather than inventing a city.

### `tracks`

`id`, `event_id` → events, `external_id`, `name`, `description`, with
`UNIQUE (event_id, name)`. Tracks belong to an event, not to the portal, so two
events can both have "Security".

### `prizes`

`id`, `event_id` → events, `name`, `detail`, `amount` (free text: `800 USD`,
`a mechanical keyboard`), `position` (ordering, nullable). The fixtures contain
no prizes; the table and the create-event form do, so an organizer can
configure them.

### `teams`

`id`, `event_id`, `external_id`, `name`, `invite_code` UNIQUE, `created_at`,
`UNIQUE (event_id, external_id)`. Team names are **not** unique — the fixtures
contain `OpenSignal` twice, `StillTrail` three times and `AmberSwitch` twice,
and that is realistic rather than a mistake to normalize away.

### `team_members`

`team_id`, `user_id`, `is_lead`, `joined_at`, `PRIMARY KEY (team_id,
user_id)`. A join table rather than a `users.team_id` column, because membership
is per event and a person may be in two events. The first member of a seeded
team is the lead.

### `projects`

| Column | Notes |
| --- | --- |
| `id`, `event_id`, `team_id` | a submission always belongs to a team |
| `track_id` | nullable; `ON DELETE SET NULL` so removing a track does not delete submissions |
| `external_id` | `prj_07`, with `UNIQUE (event_id, external_id)` |
| `title`, `summary`, `description`, `repo_url`, `demo_url` | `description` is ours; the fixtures only ship a one-line `summary` |
| `status` | `CHECK (status IN ('draft','submitted'))` |
| `submitted_at` | set when the status first becomes `submitted`; kept on later edits |
| `duplicate_of` | self-reference, set by the importer and by the create path |
| `created_at`, `updated_at` | |

Drafts are invisible to the public: the gallery query filters
`status='submitted' AND duplicate_of IS NULL`, and a direct request for somebody
else's draft is a `404` rather than a `403`, because "exists but you may not see
it" leaks more than "no such project".

### `scores`

`id`, `event_id`, `project_id`, `judge_id` → users, `criteria_json`, `comment`,
`created_at`, `updated_at`, `UNIQUE (project_id, judge_id)`.

`criteria_json` stores `{"functionality": 4, "quality": 5, "innovation": 4}`
exactly as the fixture provides it. That is the honest choice for *importing* a
file whose rubric we do not control: keys are not known in advance, one judge's
missing criterion stays missing instead of becoming a zero, and no scale is
assumed. It is the wrong shape for a weighted, organizer-configured rubric —
see [JUDGING.md](JUDGING.md) for what replaces it when T2 is built.

One row per judge per project, enforced by the unique index, so a judge cannot
double-score a project and a re-import updates rather than duplicates. There is
no `total` column: totals are computed on read (`db._score_dict`) so a changed
rubric cannot leave stale sums behind.

### `judge_tracks`

`judge_id`, `track_id`, composite PK. The fixtures declare which tracks each
judge covers. Imported and exposed on `/api/v1/me`; **not used yet** — judge
assignment is T2 and is not claimed.

### `audit_log`

`id`, `event_id`, `actor_id`, `actor_email`, `action`, `detail`, `created_at`.
Append-only: no code path updates or deletes a row. `action` is a short dotted
verb (`submission.refused_create`, `access.peer_scores_refused`,
`import.duplicate_flagged`), `detail` is a sentence an organizer can read
without a decoder.

### Indexes

`idx_projects_event(event_id, status)`, `idx_projects_team(team_id)`,
`idx_scores_project(project_id)`, `idx_scores_judge(judge_id)`,
`idx_team_members_user(user_id)`, and a partial unique index on
`users(external_id) WHERE external_id IS NOT NULL`.

## Import: fixtures.json → tables

`backend/seed.py`, run at first boot and re-runnable at any time.

| Fixture | Becomes | Keyed on |
| --- | --- | --- |
| `event` | one `events` row, marked primary | `external_id` |
| `tracks[]` | `tracks` rows | `(event_id, name)` |
| `judges[]` | `users` rows with `role='judge'`, plus `judge_tracks` | `email`, `external_id` |
| `teams[]` | `teams` rows | `(event_id, external_id)` |
| `teams[].members[]` | `users` rows with `role='participant'` + `team_members` rows, first member is lead | `email` |
| `projects[]` | `projects` rows, `status='submitted'` | `(event_id, external_id)` |
| `scores[]` | `scores` rows | `(project_id, judge_id)` |

Rules the importer follows:

* **Idempotent.** Every upsert is keyed on a fixture id or a natural key, so a
  second run reports "created: nothing (already imported)".
* **Non-destructive.** Nothing is deleted. A project somebody created through
  the UI survives a re-import.
* **Passwords are never overwritten.** An existing account keeps its hash, so a
  reseed cannot lock anybody out. New seeded accounts share one scrypt hash of
  `DOGFOOD_SEED_PASSWORD`, computed once — hashing it 121 times would put first
  boot at tens of seconds instead of about one.
* **Missing data stays missing.** No score is invented for a judge who did not
  finish a batch, no average is backfilled, no name is guessed for a participant
  the fixtures only give an email for.

The awkward cases the fixtures contain on purpose:

| Case | Handling |
| --- | --- |
| `prj_41` duplicates `prj_07` (same team `tm_07`, same repo, same title) | both rows imported; the later one gets `duplicate_of = 7`, an `import.duplicate_flagged` audit line, and it is hidden from the public gallery. Staff see it with `/projects?duplicates=1`, and it is still in the CSV export |
| uneven review coverage (2 to 5 scores per project, 41 of 41 projects scored, most judge/project pairs empty) | stored as-is; `review_count` is computed per project; nothing pads the gaps |
| a judge who scored one project uniformly 2/2/2 (`jdg_01`) | stored as-is; no normalization is applied, because none is claimed |
| projects submitted minutes before the deadline | imported with their original `submitted_at`; the importer compares it against `submissions_close` and writes an `import.late_submission` audit line if one is after it (none in the shipped file) |
| duplicate team names | allowed; teams are identified by id and invite code, not by name |

Import counts for the shipped file: 1 event, 8 tracks, 30 judges, 40 teams, 91
team members, 41 projects (1 flagged duplicate), 126 score rows — plus the four
demo accounts the fixtures do not contain (no organizer, no admin).

## Export

**Out to CSV** — `GET /api/v1/export.csv`, staff only, long format: one row per
project × score, and one row with empty score columns for a project nobody
reviewed, so the gaps are visible instead of absent.

```
project_id, project_external_id, title, team, track, status, duplicate_of,
submitted_at, criterion_<name>…, score_total, score_average, review_count,
judge_id, judge_external_id, judge_email, judge_name, comment
```

`criterion_*` columns are derived from the union of criteria keys present in the
data, sorted — with the shipped fixtures that is `criterion_functionality`,
`criterion_innovation`, `criterion_quality`. `score_total` and `score_average`
are the plain sum and mean of one judge's own criteria: **not weighted, not
normalized across judges**, and labelled as such on `/admin/export`.

**Out to JSON** — `GET /api/v1/projects`, `/api/v1/teams`, `/api/v1/tracks`,
`/api/v1/portal`, `/api/v1/judge/scores`, `/api/v1/audit`, `/api/v1/me`. Because
every row keeps its `external_id`, a client can reconstruct the fixture shape
from the API without touching the database file.

**Migrating in and out.** In: drop a `fixtures.json`-shaped file at
`DOGFOOD_FIXTURES` and run `backend/seed.py`. Out: the CSV above, or
`sqlite3 data/events.db .dump` for a full copy, or the JSON endpoints. Moving to
another database means rewriting `db.py` — no other module contains SQL.

## Schema evolution

`db.init_db()` runs `CREATE TABLE IF NOT EXISTS` for everything, then walks
`db.MIGRATIONS` and issues `ALTER TABLE … ADD COLUMN` for any column an older
file lacks. That is how the pre-existing `users` and `events` tables from the
original build gained `name`, `external_id`, `description`, the submission
window and `is_primary` without a dump-and-restore, and it is the migration path
for anybody who forked the repo before this change. There is no down migration:
added columns are nullable or defaulted, so an older copy of the code still runs
against a newer file.
