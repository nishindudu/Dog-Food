# Judging

**Status: T2 is not claimed and not built.** This document says precisely what
exists, what does not, and the method we would defend when it does. Nothing here
should be read as a feature of the shipped portal unless it is in the "built"
column.

## What is built

| Piece | Status | Where |
| --- | --- | --- |
| Score storage: one row per judge per project, criteria as given | built | `scores` table, `db.upsert_score` |
| Import of all 126 fixture score rows | built | `backend/seed.py` |
| A judge reads **their own** batch | built | `GET /api/v1/judge/scores`, `/judge/scores` |
| A judge **cannot** read a peer's batch — refused in the backend with `403`, and audited | built | `api.judge_scores`, test `test_a_judge_cannot_read_a_peers_scores` |
| A participant is not a judge (`403`) | built | `auth.roles_required` |
| An organizer or admin may audit any one judge's batch | built | `?judge=<id\|jdg_01\|email>` |
| Staff-only CSV export of every score row | built | `GET /api/v1/export.csv` |
| Judge track declarations stored | built, unused | `judge_tracks` |
| Public gallery hides all scores | built | `context.project_payload` has no score field |

## What is not built

* **Judge invitation and assignment.** No invitations, no `assignments` table,
  no ballots. Judges exist because the fixtures name them. `judge_tracks` is
  imported and read by nobody.
* **A weighted, organizer-configurable rubric.** Criteria are whatever keys the
  data contains (`functionality`, `quality`, `innovation` in the fixtures) and
  every criterion is implicitly equal.
* **A scoring form.** Judges cannot enter or change a score through the portal;
  the rows came from the import.
* **A live progress dashboard.** `/` shows `scores` and `judges_with_scores`
  counts from the database, which is a total, not a per-batch progress view.
* **Cross-judge normalization.** None. Not documented as implemented, because it
  is not.
* **Results publication, pairwise mode, Bradley-Terry.** Not attempted.

The two numbers the portal does compute are `score_total` (sum of one judge's
criteria) and `score_average` (their mean), on read, for one judge's one review
of one project. They are shown to that judge and to staff, exported to CSV, and
labelled "not normalized, not weighted" wherever they appear. They are never
ranked across judges and never published on the gallery.

## Why the raw numbers are not comparable

The fixture data shows exactly the problem, which is why we did not ship a
ranking that pretends to solve it:

* Judge spread differs. `jdg_02` gave `5,5,5` to `prj_08` and `jdg_20` gave
  `2,2,2` to `prj_23`; a plain sum ranks their projects by who had the generous
  judge, not by which project was better.
* Coverage is uneven: 2 to 5 reviews per project. A project with two reviews has
  a noisier mean than one with five, and a straight average hides that.
* `jdg_01` scored `prj_07` a uniform 2/2/2. A judge with no variance carries no
  information about *relative* quality, and any per-judge scaling has to decide
  what to do with them instead of dividing by zero.

## The method we would implement, and defend

Written down now so the choice is reviewable, in the order it would be built.

**1. Assignment first, normalization second.** Normalization cannot fix a bad
assignment. Assign with a greedy balanced round: for each project, prefer judges
who (a) declared the project's track (`judge_tracks`), (b) have the fewest
assignments so far, (c) have no conflict (same team, same employer domain, or a
self-declared conflict), until each project has `k` reviewers (`k=3` default)
and each judge has `⌈3·projects/judges⌉ ± 1` projects. Store it in an
`assignments(project_id, judge_id, state, assigned_at)` table so a ballot is a
row, not a query, and progress is countable — that table is also what makes a
live dashboard honest.

**2. Per-judge standardization, computed on the criteria vector.** For judge
*j* with reviews *r* of projects *p*, and criterion *c*:

```
mean(j,c)   = Σ_p score(j,p,c) / n(j)
sd(j,c)     = sqrt( Σ_p (score(j,p,c) − mean(j,c))² / (n(j) − 1) )
z(j,p,c)    = (score(j,p,c) − mean(j,c)) / sd(j,c)
```

with two guards, both of which the fixture data forces:

* **Low variance.** If `sd(j,c) < 0.5` on a 1–5 scale, judge *j* did not
  discriminate on *c* (`jdg_01` is exactly this case). Fall back to mean
  centering only: `z = score − mean(j,c)`. Never divide by a near-zero sd.
* **Small n.** Require `n(j) ≥ 3` before standardizing a judge at all; below
  that, mean-center and mark the review `low_confidence` in the output. Do not
  silently treat a two-review judge as equivalent to a five-review judge.

**3. Weighted combination.** With organizer-set weights `w(c)`, `Σ w(c) = 1`:

```
project_score(p,c) = Σ_j z(j,p,c) / n(p)          # mean of standardized reviews
project_score(p)   = Σ_c w(c) · project_score(p,c)
```

Rescale to a readable 0–100 band for display only, and always publish the raw
z alongside it, so a ranking can be audited back to the individual scores.

**4. Shrinkage toward the event mean, so coverage is honest.** With `n(p)`
reviews and a prior strength `m` (default 2):

```
final(p) = ( n(p) · project_score(p) + m · 0 ) / ( n(p) + m )
```

A project with two enthusiastic reviews no longer outranks one with five solid
reviews purely by variance. This is the standard Bayesian-average move, and it
is the single most defensible answer to "some projects have 2 reviews and some
have 5".

**5. Report the uncertainty, not just the order.** Publish `n(p)`, the standard
error across reviewers, and the rank interval it implies. Two projects whose
intervals overlap are tied, and the export should say so instead of breaking
the tie by rowid.

**6. Track-normalize only when comparing within a track.** Prizes are usually
per track, so the final ranking is computed within a track cohort after
standardization, and the event-wide view is labelled as a mix of cohorts.

**7. What we would verify it against.** The fixtures are a ready-made test set:
`jdg_01`'s uniform batch (low-variance guard), `jdg_02`'s generous batch (mean
shift removed), projects with 2 vs 5 reviews (shrinkage), and `prj_41`/`prj_07`
(a duplicate that must not double-count into a track mean). A normalization
implementation with no test over those four cases is a normalization claim with
no evidence, and the brief explicitly asks for the method to be documented and
defensible.

**Pairwise mode (bonus, not attempted).** If judging were pairwise rather than
scored, the estimator would be Bradley-Terry by MM iteration —
`p_i ← w_i / Σ_j w_j`, `w_i ← Σ_{i beat j} 1 / Σ_j n_ij / (p_i + p_j)` — with
Laplace-smoothed ties and a bootstrap for rank intervals. It is listed here
because it is the honest alternative to a rubric, not because any of it exists
in this repo.

## Integrity guarantees that are in force today

* **Isolation is in the backend.** `GET /api/v1/judge/scores?judge=jdg_01` with
  judge B's token is `403` before any query for judge A's scores runs. The page
  `/judge/scores` has no control that can request a peer's batch. A curl request
  and a browser get the same answer, and there is a test for both.
* **Refusals are recorded.** `access.peer_scores_refused` lands in `audit_log`
  with who asked, for whom, and when.
* **Nothing leaks sideways.** Public payloads contain no score field at all, so
  a gallery page, a project page or a team page cannot reveal judging progress
  or results. There is a test that walks the public pages asserting no criteria
  key and no invite code appears.
* **The audit trail is readable.** `/admin/audit` lists time, action, actor and
  a sentence of detail; `GET /api/v1/audit` is the same rows as JSON.
