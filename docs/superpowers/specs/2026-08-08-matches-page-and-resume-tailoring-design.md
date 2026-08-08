# Matches page + resume tailoring — Design

**Date:** 2026-08-08
**Status:** Approved design, pre-implementation

## Purpose

Two additions to the existing web app (`frontend/` + `backend/app/api/`):

1. A **matches page** where a signed-in user can browse, filter, and bookmark
   every posting already matched to their profile.
2. A **resume tailoring** flow: select one or more matched postings, upload a
   resume, and get back a separate tailored PDF per selected posting —
   content drafted by Claude, formatted via a fixed LaTeX template, compiled
   to PDF server-side.

Both build on infrastructure that already exists and is tested: the
`matches` table and its ranking pipeline (`app/matching/rank.py`,
`app/pipeline.py::match_new_postings`), the resume upload/parse path
(`app/resume/parse.py`, `app/api/resume.py`), and the React app in
`frontend/`. Neither the scraping/matching/notification core nor the daily
email cycle changes.

## Scope

**In scope:**
- `GET /api/matches` — the signed-in user's own matches, filterable and
  paginated.
- `POST /api/matches/{id}/save` — toggle a persistent bookmark on a match.
- `POST /api/resume/tailor` — resume upload + selected posting IDs → one
  tailored PDF per posting.
- A small ranking-schema extension so each match can be tagged with which of
  the user's selected target fields it best fits (used as a matches-page
  filter facet).
- Frontend: real client-side routing (`react-router-dom`), a `/matches` page,
  a tailoring flow reachable from it.
- `Tectonic` (a self-contained LaTeX-to-PDF compiler) added to the backend
  Docker image.

**Out of scope (explicitly not this task):**
- Any change to scraping, matching/ranking's core scoring logic, or the
  daily/legacy digest cycles beyond the target-field tagging addition.
- Persisting resume text or generated `.tex`/PDF content — both are
  produced and discarded within a single tailoring request (see "Resume
  tailoring" below).
- Editing a tailored PDF after generation, or any tailoring history/versioning.
- General postings browsing beyond a user's own `matches` rows (this is not
  a "search all postings" feature).

## Data model changes

- `Match.saved: bool`, default `False`. Independent of tailoring selection —
  a persistent bookmark toggled via `POST /api/matches/{id}/save`.
- `Match.matched_target_field: str | None` (a `TargetField` value). Populated
  during ranking (see below), nullable for matches ranked before this change
  and for users with no `target_fields` set.
- `User.matches_last_viewed_at: datetime | None`. Read to compute "new since
  last visit" on a `GET /api/matches` call, then updated to the current time
  at the end of that same call (so the *next* call's "new" flag is relative
  to this one, not to itself).

All three are additive, nullable-or-defaulted columns — no backfill required
beyond the existing idempotent-startup-migration pattern already used for
the web-profile columns (see `app/db/session.py`'s migration step).

## Ranking extension: target-field tagging

`app/matching/rank.py`'s `RANK_TOOL` schema gains one more optional field per
result: `target_field`, constrained to the user's own `criteria.target_fields`
values (passed in the same prompt payload that already sends
`target_fields`). Claude already sees full context to classify this
accurately in the same call that produces the fit score — no additional LLM
call. `_rank_batch`'s existing per-item validation (already discards
unknown/malformed items) extends to validate `target_field` is one of the
user's actual selected fields, defaulting to `None` if missing or invalid so
a bad tag never blocks the rest of the result. `pipeline.match_new_postings`
persists it onto the new `Match.matched_target_field` column alongside the
existing score/blurb/priority fields it already writes.

## `GET /api/matches`

Query params (all optional):
- `company`, `location` — case-insensitive substring match.
- `target_field` — filters on `matched_target_field`.
- `priority` — `high` | `normal`.
- `min_score` — float.
- `since` / `until` — filters on `Match.created_at`.
- `saved` — boolean; `true` restricts to bookmarked matches.
- `new_only` — boolean; `true` restricts to matches created after the
  user's previous `matches_last_viewed_at`.
- `limit` / `offset` — pagination (default limit e.g. 50, capped e.g. 200).

Response: match rows joined with their posting (company, title, url,
location, role_type, posted_at), plus score, blurb, priority,
matched_target_field, saved, created_at, and a per-row `is_new` boolean
computed against the *previous* `matches_last_viewed_at` value. After
building the response, the endpoint updates `matches_last_viewed_at` to now.

Only ever queries `WHERE user_id = <current user>` — no cross-user access
path.

## `POST /api/matches/{id}/save`

Body: `{"saved": true | false}`. 404s if the match doesn't belong to the
current user. Idempotent — setting the same value twice is a no-op.

## Resume tailoring

`POST /api/resume/tailor` — multipart form: `file` (PDF/DOCX, same
constraints as the existing profile resume upload: 5 MB cap, in-memory) +
`posting_ids` (list of match/posting IDs, all must belong to the current
user's matches).

Flow, per posting ID, run concurrently (bounded by a semaphore — same
pattern as `rank.py`'s `_CHUNK_CONCURRENCY`):
1. Parse the uploaded resume to text in memory (reuses
   `app/resume/parse.py`; not persisted, same as today's profile-resume
   upload path).
2. Call Claude with a new tool schema (distinct from both `RANK_TOOL` and
   the existing resume-extraction tool) asking for tailored resume *content*
   — bullet points, summary, skills emphasis — targeted at that specific
   posting's title/company/description, returned as structured sections
   (not raw LaTeX from the model) so the template step below can't be broken
   by a malformed `\` escape or unbalanced braces in freeform model output.
3. Render those structured sections into a **fixed, pre-tested LaTeX
   template** (one house style, not user-uploaded/arbitrary LaTeX) — plain
   string substitution into known-good template slots, not model-generated
   document structure.
4. Compile the filled template with **Tectonic** to PDF.
5. Return the PDF (if one posting was selected) or a zip of PDFs named by
   company/title (if multiple).

Nothing from this flow is persisted: the parsed resume text, the filled
`.tex`, and Tectonic's working directory are all discarded once the
response is sent (in-memory / a request-scoped temp dir cleaned up in a
`finally`).

**Selection semantics** (per earlier decision): each selected posting
produces its **own** independently-tailored PDF — no blending across
postings.

## Frontend changes

- Add `react-router-dom`; routes: existing auth/profile flow, `/matches`,
  and the tailoring flow (either its own `/tailor` route or a panel reached
  from `/matches` — implementation plan decides based on how it feels once
  the matches table exists).
- `/matches`: table/list of the current user's matches, filter controls
  matching the query params above, a save-toggle per row, multi-select
  checkboxes, a "Tailor resume for selected" action that opens the upload +
  generate flow and surfaces per-job download links (or a single zip
  download) once ready.
- Loading/error states for tailoring follow the same pattern already used
  for resume upload in `ResumePanel` (busy state, inline error message).

## Error handling

- A Tectonic compile failure (e.g. an unexpected character surviving
  template substitution) is caught, logged with the posting ID, and
  surfaces as a per-job failure in the response (that job's slot reports
  "couldn't generate this one" rather than failing the whole batch) — same
  isolate-the-failure principle as `rank_postings`'s per-chunk error
  handling.
- A Claude call failure for one posting in a multi-posting tailoring request
  does not block the others (same concurrent-with-isolated-failure pattern
  as `_rank_for_user`/`_rank_batch`).
- `/api/matches/{id}/save` and `/api/resume/tailor` both require CSRF (same
  `require_csrf` dependency already used by `PUT /api/profile`).

## Testing

- `GET /api/matches` filter logic: unit-testable query-building kept
  separable from the route handler where reasonable; DB-backed tests via the
  existing in-memory SQLite pattern, covering each filter facet and the
  `is_new`/`matches_last_viewed_at` update-after-read ordering.
- Target-field tagging: extends `tests/test_rank.py`'s existing fake-client
  pattern — a fake client returning a `target_field` per result, and a case
  where it returns an invalid one (must be dropped to `None`, not persisted
  as garbage).
- Resume tailoring: fake Claude client (structured sections, not raw LaTeX)
  + a real Tectonic compile of the fixed template in tests (deterministic,
  fast for a small fixed template — no need to mock the compiler itself);
  a separate test asserts a compile failure for one posting doesn't affect
  others in the same request.
- Save-toggle idempotency and cross-user access rejection (404 on someone
  else's match ID).

## Non-goals (this task)

- Tailoring against multiple postings blended into one document.
- Persisting any tailored PDF, generated `.tex`, or resume text.
- A general "browse all postings" view independent of the user's own
  matches.
- Editing a resume template per-user (one house LaTeX template for all
  users in this iteration).
