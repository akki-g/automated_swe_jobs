# Prompt: web frontend + resume ingestion + daily email + multi-major expansion + Docker

Hand this whole document to the implementing agent as its task brief. It
assumes no prior context beyond this repo's own README and
`docs/superpowers/specs/2026-08-07-automated-swe-jobs-design.md` — read those
two first.

## Why this work now

SMS delivery (SignalWire, and Telnyx as the planned fallback) is blocked on
carrier-side A2P 10DLC campaign approval, which has real external lead time
we don't control. Rather than wait idle, we're standing up a **web
signup/profile flow + a daily email digest** as the primary path for now.
SMS/the assistant webhook are not being removed — they stay in the codebase
exactly as they are, ready to switch back on the moment SignalWire clears
compliance — but they are no longer the only way in. Email (Resend, already
wired and verified working) becomes the default delivery channel until then.

## What already exists — reuse it, don't rebuild it

The backend (`backend/app/`) already has a working, tested pipeline: source
adapters (Greenhouse/Lever/Ashby ATS boards + SimplifyJobs GitHub lists) →
normalize → dedupe → rule-filter → Claude ranking → priority split →
notify (SMS via SignalWire, email via Resend). 116 passing tests. Two
scheduler lanes (fast ~2min, slow ~15min) plus a digest cycle. All of this
is sound and scales to "hundreds of users" per the original design's
target — this task adds a front door and a new delivery cadence on top of
it, it does not re-architect the matching/notification core.

**Read before touching anything:**
- `README.md` — current state, what's built, known bugs fixed in review passes.
- `docs/superpowers/specs/2026-08-07-automated-swe-jobs-design.md` — the original design (data model, scheduling, matching, notifications).
- `backend/app/domain/models.py`, `backend/app/db/models.py` — current schema.
- `backend/app/matching/filters.py`, `backend/app/matching/rank.py` — current matching/ranking logic.
- `backend/app/notify/dispatch.py`, `backend/app/notify/email_resend.py` — current notification path.

## Scope of this task

1. **Web frontend** with auth, so a user can sign up without "me manually
   seeding a DB row" (Phase 1's current onboarding).
2. **A questionnaire** to capture job-search criteria — the structured
   equivalent of what `criteria` + `freeform_notes` already store, but
   collected via a form instead of assistant back-and-forth.
3. **Resume ingestion**: upload a resume (PDF/DOCX), extract **structured
   skills/keywords/experience signal** from it via Claude, and use *that
   extracted structure* — not the raw resume text — as ranking input.
   **Do not store the raw resume file or its full extracted text
   long-term.** Parse it, pull out what's useful (skills, past
   titles/seniority signal, target role signal), persist only that
   structured result, and discard the raw upload once parsed (see
   "Resume handling" below for the exact policy).
4. **Daily email digest**: every user with a completed profile gets one
   email per day summarizing new matching postings since their last email
   — reusing `notify/dispatch.py`'s digest formatting and
   `notify/email_resend.py`, on a new daily cadence (not the existing
   8am/20:00 twice-daily digest_cycle — decide whether to replace it or
   add a distinct daily job; see "Scheduling" below).
5. **Multi-major expansion**: this system currently assumes SWE/tech.
   Generalize it so Finance, Consulting, Marketing, Data/Analytics,
   Product, Operations, Sales — anything large companies run structured
   new-grad/intern pipelines for — are first-class, not an afterthought.
   See "Generalizing beyond SWE" below for exactly what needs to change
   and what doesn't.
6. **Dockerize** to match this developer's existing project conventions —
   see "Dockerization" below for the exact pattern to mirror from the
   sibling `Posted` repo.
7. **Scale target: at least 30 concurrent users**, with headroom — this is
   well within what the existing bounded-pool/bounded-concurrency
   architecture already targets (hundreds), so this is a "don't
   regress it" constraint on the frontend/auth layer more than a new
   backend engineering problem.

## Generalizing beyond SWE — what actually needs to change

Good news: the ATS adapters (Greenhouse/Lever/Ashby) already fetch **every**
open role a company posts, not just engineering — the SWE bias today is in
three specific places, not the core pipeline:

1. **`companies.yaml`** — currently 32 tech companies. Needs entries for
   companies with real structured new-grad/intern pipelines outside tech:
   investment banks and asset managers (Goldman Sachs, JPMorgan, Morgan
   Stanley, BlackRock, ...), consulting (McKinsey, BCG, Bain, Deloitte, ...),
   consumer/CPG and retail (P&G, Unilever, Target, ...), and large
   generalist employers with big grad programs. Same live-verification
   discipline as the existing list: confirm each company's real
   Greenhouse/Lever/Ashby slug resolves before adding it (many of these
   won't be on those three ATS platforms at all — Workday is extremely
   common for large non-tech employers and finance; **a Workday adapter is
   probably required for this expansion to be meaningful**, not optional —
   evaluate this early since it's the biggest unknown in this task).
2. **`RoleType` / role inference** (`app/domain/models.py`,
   `app/sources/ats/common.py::infer_role_type`) — the regexes for
   "intern"/"new grad" are already field-agnostic (they match on
   seniority/program language, not tech-specific words), so `RoleType`
   itself doesn't need new values for "finance" vs "SWE." What's needed
   instead is a way to capture the user's **target field/function**
   (software engineering, investment banking, consulting, marketing,
   data/analytics, product, etc.) as part of their profile/criteria, and
   have ranking use it — see "Job profile taxonomy" below.
3. **The ranking prompt** (`app/matching/rank.py::_build_prompt`'s system
   message) is hardcoded to "new-grad software engineering or internship
   roles." Generalize it to score fit against whatever field/function the
   user's profile specifies, using their resume-derived skills as
   additional signal.
4. The **SimplifyJobs GitHub list sources** (`app/sources/github_lists.py`)
   are inherently SWE/tech-specific (that's what those lists are) — keep
   them as one input among several, not the assumption. Users targeting
   non-tech fields simply won't get postings from that specific source,
   which is fine as long as ATS coverage (once broadened per #1) picks up
   the slack.

**Job profile taxonomy**: add a `target_fields` (or similarly named)
structured field to the user's profile — a short, curated enum/list (not
freeform) covering the major categories large companies hire new
grads/interns for: Software Engineering, Data Science/Analytics, Product
Management, Finance/Investment Banking, Consulting, Marketing, Sales,
Operations, Design, at minimum. Store it alongside the existing
`role_types`/`keywords`/`locations`/`sponsorship_required` criteria fields
already in the schema — extend `Criteria`, don't replace it.

## Resume handling — the specific privacy/minimization policy

The user has been explicit: **extract keywords/skills, don't just store
everything.** Concretely:

1. Accept a resume upload (PDF/DOCX) via the web frontend, sent to the
   backend.
2. Parse text out of it server-side (a PDF/DOCX text-extraction library —
   pick one, note the choice and why in your implementation notes).
3. Send the extracted text to Claude with a structured tool-use call
   (same pattern as `rank.py`'s `RANK_TOOL`) asking for: a skills list, a
   seniority/experience signal, inferred target field(s) (to pre-fill —
   not silently override — the profile taxonomy above), and anything else
   directly useful for matching. Define this as its own tool schema; don't
   reuse `RANK_TOOL`.
4. **Persist only the structured extraction result** (a JSON-ish
   skills/summary blob on the user's profile) — not the raw file bytes,
   not the full extracted resume text. Process the upload in memory /
   a short-lived temp location and discard it once the structured result
   is saved. If you need the raw text transiently to debug the extraction
   during development, make sure it isn't what ends up in the persisted
   schema or in any log line.
5. Let the user re-upload to replace their extracted profile at any time
   (resumes change) — don't build versioning/history for this in v1.

## Scheduling: where the daily email fits

Decide and document one of:
- **(a)** A new, separate daily job (e.g. cron at a fixed local hour) that
  gathers each web-onboarded user's un-sent matches and emails them,
  running alongside the existing `digest_cycle` (which stays for
  SMS-opted-in users once SMS is unblocked).
- **(b)** Fold web users into the existing `digest_cycle` at a daily
  cadence for email specifically, keeping the 8am/20:00 SMS cadence
  separate once SMS resumes.

Whichever you pick, **do not touch the fast/slow lane matching cycles** —
they keep writing `matches` rows exactly as now; only the *delivery*
cadence for email is new. Reuse `gather_pending_digests` /
`_format_email_digest` rather than duplicating that logic.

## Frontend & auth — decisions and defaults

This developer's other repos use different stacks (Posted: Expo/React
Native exported to web, behind a shared nginx reverse-proxy network on the
same EC2 box; sibling repos may differ) — **don't assume you must match
Posted's frontend framework choice.** The explicit ask here is a **web
interface**, not a mobile app, so a plain web-first stack (e.g. a Vite or
Next.js React app, or whatever the implementing agent judges simplest to
build and operate for this scope) is the right default unless there's a
concrete reason to share code with another repo's mobile client. State
this decision explicitly in your implementation notes rather than picking
silently.

**Auth**: pick something proportionate to "≥30 known users," not
enterprise SSO. Reasonable defaults, pick one and justify it: email
magic-link (works well with Resend already being wired in), or a
lightweight email+password flow. Avoid building a custom OAuth provider
from scratch. Whatever you choose, `users.consent_at`/`consent_method`
already exist in the schema for consent tracking (per the original design's
Phase 2 prep) — populate them from the real signup flow instead of the
Phase 1 placeholder value (`"verbal-friend-onboarding"`).

## Dockerization — mirror this exact pattern

The sibling `Posted` repo (`/Users/akshatguduru/Desktop/Projects/Posted/`)
has the Docker conventions to copy:
- `backend/Dockerfile` — multi-stage `uv`-based build (builder stage syncs
  deps + copies app, runtime stage is slim, runs as a non-root user, has a
  `HEALTHCHECK` hitting a real health endpoint).
- `docker-compose.yml` at the repo root — local dev: Postgres service with
  a healthcheck + named volume, an `api` service depending on
  `postgres: condition: service_healthy`, ports exposed for local access.
- `compose.production.yml` at the repo root — production: same services,
  `restart: unless-stopped`, `security_opt: [no-new-privileges:true]`,
  `read_only: true` + `tmpfs` mounts where the app needs writable scratch
  space, bounded JSON logging (`max-size`/`max-file`), secrets required via
  `${VAR:?Set VAR in .env}` rather than defaulted, and a shared **external**
  Docker network (`portfolio-edge` in Posted's case) so multiple projects
  on the same EC2 box sit behind one reverse proxy — add a frontend web
  service to that same shared network here too, following the same
  `aliases:` pattern Posted uses for its `web` service.
- A frontend `Dockerfile` that builds the web app and serves it (Posted
  serves its Expo web export via nginx in a slim image) — adapt to whatever
  frontend stack you choose above.

This repo doesn't have any Docker files yet — you're adding
`backend/Dockerfile`, a frontend Dockerfile, `docker-compose.yml`, and
`compose.production.yml` from scratch, using Posted's as the template for
structure/conventions, not copying its actual services (this app has no
Plaid/Schwab/banking concerns — just Postgres + this API + this frontend +
whatever background scheduler process hosts the existing APScheduler jobs).

**Decide explicitly**: does the scheduler run inside the same container as
the API process (as it does today — `uvicorn app.main:app` boots
APScheduler in-process per `app/main.py`), or split into a separate
container? Given the "no distributed task queue, single box, hundreds of
users" scale target from the original design, keeping it in-process (one
`api` container) is almost certainly still correct — don't split it out
without a concrete reason tied to this task's actual requirements.

## Constraints — don't regress these

- Every existing test (116, `uv run pytest` from `backend/`) must keep
  passing. Add new tests for everything new, following this repo's existing
  conventions: pure logic gets unit tests with no I/O; anything hitting
  the DB uses the in-memory SQLite pattern already used throughout
  `tests/`; anything calling an external API (Claude, Resend, a PDF
  parser) gets a fake/mocked client in tests, never a live call.
- Keep the pure/impure module boundary this repo already follows
  (`ingest/`, `matching/`, `notify/` stay pure or clearly-isolated I/O;
  `pipeline.py`/`scheduler.py` do orchestration) — new resume-extraction
  and profile logic should follow the same shape (e.g. a
  `resume/extract.py` that's testable with a fake Claude client, separate
  from the upload-handling web route).
- Don't remove or weaken the SMS/assistant path — it stays intact and
  dormant, ready for when SignalWire compliance clears.
- Keep secrets in `.env`, never hardcoded or logged (matches this repo's
  existing `check_keys.py` convention — extend it with whatever new
  secrets this task introduces, e.g. an auth signing secret).

## Deliverable expectations

Given the scope here (auth, file upload, a new frontend app, a new
adapter, schema changes, new scheduling), treat this as a multi-step
build: propose a concrete plan (this repo's contributors use
`superpowers:writing-plans`/`superpowers:brainstorming` conventions — worth
checking `docs/superpowers/` for how prior work here was staged) before
writing code, and flag early — before spending significant effort — if the
Workday-adapter question above turns out to be a bigger lift than expected,
since it gates how much the "beyond SWE" goal is actually achievable versus
aspirational for v1.
