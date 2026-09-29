# Dog-Food

**Self-hosted hackathon portal: fixture-seeded public gallery, invite-link teams,
deadline-enforced submissions and role isolation that is enforced in the backend.**

Built for [DOGFOOD 2026](https://dogfoodhack.com) — the 72 hour hackathon where
every team builds the platform that will judge them.

* **Tier claimed: T1** (core). Verified by the organisers' own checker: see
  [`acceptance-report.txt`](acceptance-report.txt).
* Flask + Flask-Login + one SQLite file. No cloud accounts, no hosted database,
  no external API, no JavaScript framework.
* Runs offline on a laptop: `docker compose up --build` and open
  <http://localhost:8080>.

---

## One command

```bash
docker compose up --build
```

That builds the image, creates `data/events.db`, imports `fixtures.json` into
it, prints the acceptance headers and serves the portal on
<http://localhost:8080>. Nothing else is required and nothing calls out to the
network at runtime.

Without Docker:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python backend/main.py           # seeds on first boot, listens on :8080
```

The database is seeded on first boot. To seed it by hand, or to re-import after
editing `fixtures.json`:

```bash
python3 backend/seed.py                    # idempotent, never deletes your rows
python3 backend/seed.py --write-config     # regenerate .dogfood.toml
python3 backend/seed.py --stats            # row counts
```

## Checking it

The organisers' checker is committed verbatim as [`run.py`](run.py):

```bash
python3 run.py .dogfood.toml > acceptance-report.txt
```

Current output — seven of seven checks pass:

```
T1  gallery is public ................. PASS
T1  project from fixtures shown ....... PASS
T1  closed event refuses submissions .. PASS
T2  judge sees own scores ............. PASS
T2  judge cannot see peer scores ...... PASS
T2  participant blocked ............... PASS
T2  csv export works .................. PASS

claimed T1, verified T1 T2
```

The checker reports `verified T1 T2` because both of the T2 probes it can run
pass. **We still claim T1 only.** T2 needs a configurable weighted rubric,
judge invitation and assignment, a live progress dashboard and documented
cross-judge normalization, and none of those exist here. See
[Honest gaps](#honest-gaps).

## Signing in

Every seeded account — 30 fixture judges, 91 fixture participants and 4 demo
accounts — uses the demo password `change-me` (`DOGFOOD_SEED_PASSWORD` to
change it). The login page lists them.

| Who | Email | Role |
| --- | --- | --- |
| organizer | `organizer@example.local` | organizer |
| admin | `admin@example.local` | admin |
| judge A (`jdg_01`) | `tomas.varga@example.org` | judge |
| judge B (`jdg_02`) | `wei.lindqvist@example.org` | judge |
| participant (`tm_01` lead) | `priya1@example.org` | participant |

Two ways to authenticate:

* **Browser** — a signed session cookie, from `/login` or `POST /api/v1/login`.
* **Bearer token** — `Authorization: Bearer <token>`, accepted on every route.
  The four non-expiring tokens the checker uses are printed at boot and written
  into `.dogfood.toml`. Regenerate them any time with
  `python3 backend/seed.py --write-config`.

```bash
curl -H "Authorization: Bearer <judge_b token>" \
     "http://localhost:8080/api/v1/judge/scores?judge=jdg_01"
# 403 {"message": "A judge cannot read another judge's scores", ...}
```

## What is built (T1)

| Requirement | Where |
| --- | --- |
| Authentication and sessions | `backend/auth.py` — Flask-Login session cookie + signed bearer tokens (timed for login, static for the seeded identities) |
| Real role model | `admin`, `organizer`, `judge`, `participant` rows with a SQL `CHECK`; `visitor` is the anonymous user class. Enforced by `auth.roles_required` |
| Event creation with dates, tracks and prizes | `/events` (form) and `POST /api/v1/events` (JSON); `events`, `tracks`, `prizes` tables |
| Team formation by invite link | `/teams`, `/teams/join/<code>`; random per-team code, shown only to members and staff |
| Project submission, draft and edit until the deadline | `/projects/new`, `/projects/<id>/edit`, `POST|PATCH /api/v1/projects[/<id>]`; `projects.status` is `draft` or `submitted` |
| Deadline enforcement that holds | `actions.submit_project` → `db.submissions_are_open()`. One code path for the form and the API; every refusal is written to the audit trail |
| Public gallery with search and filter | `/projects` — server rendered, `?q=`, `?track=`, `?sort=`, no sign in |

Also in the build, because the checker probes them and because they are the
cheap half of T2:

* `GET /api/v1/judge/scores` — a judge reads **their own** scores, and only
  their own. `?judge=<id|jdg_01|email>` is refused with `403` for any judge who
  is not that judge, and the attempt is audited. Organizers and admins may
  audit any batch.
* `GET /api/v1/export.csv` — staff-only long-format results export.
* An append-only `audit_log` with a readable page at `/admin/audit`.
* Duplicate detection on import: `prj_41` (same team, same repo as `prj_07`) is
  flagged `duplicate_of`, kept in the database, hidden from the public gallery
  and visible to staff with `/projects?duplicates=1`.

## Pages

| Route | Who | What |
| --- | --- | --- |
| `/` | everyone | Event overview: submission window, tracks with counts, prizes, latest submissions, role-specific panels |
| `/projects` | everyone | Public gallery, search + track filter + sort |
| `/projects/<id>` | everyone | Project detail, team members, repository. Drafts are `404` to anyone but their team and staff |
| `/projects/new`, `/projects/<id>/edit` | participant+ | Submission form; disabled and refused once the window has closed |
| `/teams`, `/teams/<id>` | everyone | Teams and members. Invite codes only for members and staff |
| `/teams/join/<code>` | participant+ | The invite link itself |
| `/events` | everyone (manage: staff) | All events; create with tracks and prizes; make one the running event |
| `/judge/scores` | judge, staff | Your own score batch. Organizers can pick a judge to audit |
| `/admin/export` | staff | What the CSV contains, and the curl to fetch it |
| `/admin/audit` | staff | The audit trail |
| `/admin/users` | admin | Accounts and roles |
| `/login`, `/logout` | everyone | Session login (server-side POST, works without JavaScript) |
| `/healthz` | everyone | Liveness plus what the database holds |

Every page is rendered by Flask from SQLite. There is no client-side state, no
mock data and no hardcoded list anywhere in `frontend/`: `static/app.js` only
does progressive enhancement (auto-submitting filters, copy-link buttons,
confirmation prompts, repeatable track/prize rows). Each form is a real POST
that works with JavaScript disabled.

## JSON API

All under `/api/v1`. JSON in, JSON out; `401` when you are not signed in, `403`
when your role does not allow it.

| Method and path | Role | Purpose |
| --- | --- | --- |
| `GET /portal` | public | Running event, window, tracks, prizes, counts |
| `GET /projects` | public | Gallery data: `?q=`, `?track=`, `?sort=`, `?limit=`, `?offset=`; staff add `?drafts=1&duplicates=1` |
| `GET /projects/<id>` | public | One project (`404` for somebody else's draft) |
| `POST /projects`, `PATCH /projects/<id>` | participant+ | Create/edit a submission. Deadline enforced |
| `GET /teams`, `POST /teams`, `POST /teams/join` | public / participant+ | Teams and invite-link joining |
| `GET /tracks` | public | Tracks with project counts |
| `GET /me` | signed in | Your identity, team, submissions, and your scores if you judge |
| `GET /judge/scores` | judge, staff | Own batch; `?judge=` is staff-only |
| `GET /export.csv` | staff | Long-format results CSV |
| `GET /audit` | staff | Audit trail JSON |
| `POST /events`, `POST /events/<id>/primary`, `POST /events/<id>/tracks`, `POST /events/<id>/prizes` | staff | Event configuration |
| `GET|POST /users`, `PATCH /users/<id>` | admin | Accounts and roles |
| `GET /tokens` | signed in | Your session token and static token |
| `POST /login`, `POST /logout`, `GET /session` | — | Unchanged from the original build |
| `POST /create_event`, `GET /get_events`, `DELETE /delete_event/<id>` | — | The three original event routes, urls and payloads untouched |

`PATCH /api/v1/projects/<id>` has PATCH semantics: a field you omit keeps its
stored value, so editing one line of a summary cannot blank the title.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` / `DOGFOOD_PORT` | `8080` | Listen port |
| `DOGFOOD_HOST` | `0.0.0.0` | Bind address |
| `DOGFOOD_DB` | `data/events.db` | SQLite file |
| `DOGFOOD_FIXTURES` | `fixtures.json` | What to import |
| `DOGFOOD_SECRET_KEY` | `your_secret_key_here` | Signs sessions and tokens |
| `DOGFOOD_SEED_PASSWORD` | `change-me` | Password for every seeded account |
| `DOGFOOD_STATIC_TOKENS` | `1` | Set `0` to disable the non-expiring tokens |
| `DOGFOOD_MAX_TEAM_SIZE` | `4` | Team size cap enforced on join |
| `DOGFOOD_DEBUG` | `0` | `1` for the Flask dev server with reloader |
| `DOGFOOD_BASE_URL` | `http://localhost:8080` | Used when generating `.dogfood.toml` |

## Tests

60 tests, standard library `unittest`, no server needed — they run the Flask
test client against a throwaway database:

```bash
.venv/bin/python -m unittest discover -s tests -t tests -v
```

`tests/test_acceptance_checks.py` loads the organisers' real `run.py`, replaces
only its `request()` with the test client, and asserts the seven checks pass.
It also asserts that `.dogfood.toml` is not lying: every route it names resolves
to a real Flask rule and every token it hands over authenticates as the role it
claims.

The other two modules cover the public portal (including that invite codes and
scores never leak onto a public page), the deadline (JSON, form, API, edit,
audit trail, and the same code path with an *open* window: draft → edit →
submit → close → refuse), role isolation, team formation, event creation with
tracks and prizes, and the endpoints that were already working before this
build.

## Demo script (the five minute video)

One full event lifecycle, in the order the brief asks for. Every step below is a
click or a curl against the seeded portal.

1. **Create** — sign in as `organizer@example.local` (`change-me`). `/events` →
   *Create an event*: name, event date, submission open and close, two tracks
   with *Add track*, two prizes with *Add prize*, submit. Then *make running* on
   the new row: the header bar, the gallery and the window all switch to it, and
   the gallery is now empty because nothing has been submitted to it yet.
2. **Submit** — sign out, sign in as `priya1@example.org`. `/teams` shows
   *NorthKiln* with a copyable invite link; open it in a second browser as
   `member1_1@example.org` and join. Then `/projects/new`: save a draft (it does
   **not** appear in the public gallery), edit it, submit it — it appears
   immediately with its submitted timestamp.
3. **Deadline** — as the organizer, edit the running event's close date into the
   past (or switch back to the seeded fixture event, which closed
   2026-03-01T18:00:00Z). The submission form shows a red banner and disabled
   fields, and `POST /projects/new` answers `403` — the refusal is in the audit
   trail with the reason.
4. **Judge** — sign in as `tomas.varga@example.org` (`jdg_01`). `/judge/scores`
   shows that judge's own batch from the fixtures. Then the isolation proof, on
   camera:
   `curl -H "Authorization: Bearer <jdg_02 token>" "…/api/v1/judge/scores?judge=jdg_01"`
   → `403`, and `/admin/audit` shows the `access.peer_scores_refused` line.
5. **Publish** — sign in as the organizer: `/projects?duplicates=1` shows the
   flagged duplicate the fixtures contain, `/admin/export` downloads the results
   CSV, `/admin/audit` reads as a timeline of the whole event.

## Data

* **Import** — `fixtures.json` → `backend/seed.py`. Keyed on the fixture ids
  stored in `external_id`, so a re-import updates rather than duplicates, and
  never deletes a row somebody typed into the portal.
* **Export** — `GET /api/v1/export.csv`: one row per project × score, criterion
  columns derived from the data, projects with no reviews still present with
  empty score columns so the gaps are visible.
* Full schema, mapping and rationale: [DATA-MODEL.md](DATA-MODEL.md).
* System design: [ARCHITECTURE.md](ARCHITECTURE.md).
* Judging, scoring and what is deliberately not claimed:
  [JUDGING.md](JUDGING.md).

## Honest gaps

What is **not** built, in the words of the tier ladder:

* **T2 — not claimed.** Scores are imported and readable by their own judge,
  and staff can export them, but there is no judge invitation or assignment
  (fixtures are imported as-is, `judge_tracks` records their declared tracks
  and nothing uses it yet), no organizer-configurable weighted rubric, no
  scoring form, no live progress dashboard and **no cross-judge
  normalization**. `score_total` and `score_average` in the CSV are a plain sum
  and mean of one judge's criteria — not comparable across judges.
* **T3 — not attempted.** No community voting, no comments, no randomized
  ballot order, no rate limiting beyond what Werkzeug does by default, no
  Sybil defence. The audit trail exists and would be the foundation.
* **T4 — not attempted.** No OpenAPI spec, no webhooks, no certificates, no
  signed participation records, no embeddable widget, no bulk import beyond
  `fixtures.json`.

Known limitations of what *is* built:

* **Default secret key.** `DOGFOOD_SECRET_KEY` defaults to a public string so
  that the committed `.dogfood.toml` tokens work out of the box. Set your own
  before this faces a network, then run `seed.py --write-config`.
* **Seeded accounts share a password**, and one scrypt hash is reused for all
  of them to keep first boot at about one second. Fine for fixtures, not fine
  for real people.
* **Static tokens never expire.** They exist for the checker and the demo.
  `DOGFOOD_STATIC_TOKENS=0` turns them off.
* **No CSRF tokens.** Session forms are same-origin POSTs with no CSRF
  protection; a bearer-token API would not need it, a cookie session does.
* **SQLite, one writer at a time.** Right for one event on one box; a
  multi-event deployment on shared storage wants Postgres.
* **The HTML gallery is unpaginated** (all matches on one page) so the
  checker's page-one assumption always holds. The JSON API paginates.
* **No password reset, no email.** Judge invitation by email needs an SMTP
  server, and the rules say no hosted dependencies.
* **Search is SQL `LIKE`**, not full-text; fine at hundreds of projects.
* **`/admin/users` has no pagination** — 125 seeded accounts render on one page.

## Layout

```
backend/
  main.py       app, the three original event routes, login/logout/session, boot
  auth.py       roles, User/Visitor, tokens, roles_required, refusals
  db.py         schema, migrations, every query
  actions.py    the write operations: deadline, teams, events, in one place
  context.py    shared view context and JSON shapes
  api.py        JSON endpoints
  pages.py      HTML views
  seed.py       fixtures.json import, credential printing, .dogfood.toml
frontend/       Jinja templates (base, macros, one per page)
frontend/static/style.css, app.js
tests/          60 unittest tests, including the real acceptance checker
fixtures.json   the shared DOGFOOD fixtures, unmodified
run.py          the organisers' acceptance checker, unmodified
.dogfood.toml   tiers claimed, routes, and the four acceptance headers
docs: README.md ARCHITECTURE.md DATA-MODEL.md JUDGING.md LICENSE
```

The brief's submission list says `src/`; this repo keeps the `backend/` and
`frontend/` split it started with, because repo layout is explicitly not scored
and renaming directories would obscure what changed. `.dogfood.toml` says where
every route ended up, which is the part the checker reads.

## License

MIT — see [LICENSE](LICENSE). You keep ownership; the organisers fork it.
