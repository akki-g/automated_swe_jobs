# automated_swe_jobs

Finds new-grad and internship postings across software, data, product,
finance, consulting, marketing, sales, operations, and design; matches them
to each user's profile; and sends a focused daily email. A web signup and
questionnaire are now the primary onboarding path. The existing instant SMS,
SMS assistant, and company-watchlist paths remain intact while A2P approval is
pending.

Full design: `docs/superpowers/specs/2026-08-07-automated-swe-jobs-design.md`.

## Web onboarding expansion

- **Web client:** a responsive Vite/React/TypeScript app in `frontend/` with
  signup/login, a three-step profile questionnaire, privacy-explicit resume
  upload, inferred-field suggestions, and a profile dashboard.
- **Auth:** email/password with Argon2 hashes, signed HttpOnly session cookies,
  SameSite protection, and a double-submit CSRF token. Signup writes real
  `consent_at` and `web-signup-terms-v1` consent metadata. Phone is optional.
- **Target-field taxonomy:** structured fields cover software engineering,
  data/analytics, product, finance/investment banking, consulting, marketing,
  sales, operations, and design. The user's explicit choices remain
  authoritative during Claude ranking.
- **Resume minimization:** PDF uses `pypdf`; DOCX uses `python-docx`. Uploads
  are limited to 5 MB and processed from memory. Claude returns only bounded
  skills, past titles, experience signal, inferred fields, education fields,
  and a short summary. Only that structured JSON is persisted; raw bytes and
  extracted text are never stored or logged. Re-upload replaces the result.
- **Daily email:** completed profiles receive one email at their chosen time
  in the configured timezone. A settings page controls the per-user time and
  pause state; successful-send dates prevent duplicates and the minute-level
  due check catches up after restarts. The legacy 08:00/20:00 SMS digest
  remains separate. Delivery state is channel-aware, so delivery on one
  channel does not suppress another.
- **New-profile backfill:** a completed profile is matched within about a
  minute against a bounded window of recent open jobs already in the database.
  Failed or partial ranking calls leave the profile pending for retry, and
  pair-level deduplication prevents repeated matches.
- **Workday:** 14 live-verified Workday boards expand the curated source set
  across banking, investing, consulting, consumer goods, retail, payments,
  media, and generalist employers. Workday enforces 20 results per page, so
  the adapter explicitly caps each board at its newest 200 roles per sweep;
  tune `max_pages` per entry if broader coverage is worth the request volume.
- **Deployment:** development and hardened production Compose files run
  Postgres + one API/scheduler process + an nginx-served web client. Keeping
  APScheduler in the single API container avoids duplicate jobs and remains
  appropriate for the stated 30-user target (and the existing hundreds-user
  headroom).

Implementation decisions and staging are recorded in
`docs/superpowers/plans/2026-08-08-web-onboarding-and-daily-email-plan.md`.

### Live source coverage audit (2026-08-20)

The 69 configured reliable and best-effort feeds returned 22,174 deduplicated
postings in a live sweep. Of those, 5,188 were classified as internships or
new-grad/entry-level roles across 1,792 companies. A conservative title-term
audit found coverage in every selectable field: software 2,575 / 1,006
companies, data 1,676 / 754, product 120 / 76, finance 192 / 101, consulting
65 / 39, marketing 100 / 47, sales 76 / 40, operations 121 / 74, and design
190 / 83. Counts are posting/company respectively and will change as boards
change; the target-field coverage test ensures every taxonomy value retains a
configured category feed.

## What's built (working MVP)

This implements the spec's full Phase 1 build order (steps 1–6), plus a
post-review hardening pass and a new company-watchlist feature.

- **Sources** (`app/sources/`): SimplifyJobs New-Grad-Positions and active
  Summer 2027 Internships GitHub lists; Greenhouse/Lever/Ashby/Workday ATS adapters driven
  by a curated `companies.yaml` — **46 companies**, every entry live-verified
  against the provider's real public API during this build (see "Testing"
  below); a generic RSS/JSON aggregator adapter (`RssFeedSource`); and 21
  PagesXYZ category feeds spanning every supported target field when
  `PAGESXYZ_API_KEY` is configured. All reliable-tier
  sources implement `check_for_changes()` for the fast lane.
  LinkedIn/Indeed exist only as inert placeholder classes — see "Deliberately
  not built" below.
- **Source registry** (`app/sources/registry.py`): a process-lifetime cache
  ensuring the *same* `Source` instances are reused across every scheduler
  tick, not rebuilt fresh each cycle — this is what makes
  `check_for_changes()`'s cheap change-detection state (ETags, job counts)
  actually mean something across the fast lane's ~2-minute cadence.
- **Ingest** (`app/ingest/`): `normalize.py` (posting_key) and `dedupe.py`,
  pure and unit-tested.
- **Matching** (`app/matching/`): `filters.py` (rule-based, including
  sponsorship as a hard filter) and `rank.py` (Claude tool-use
  scoring/blurb, batched per user). `compute_priority()` implements the
  spec's score-threshold rule; a separate watchlist check in
  `pipeline.match_new_postings` can independently force a match to
  high-priority (see "Company watchlist" below).
- **Notify** (`app/notify/`): `SignalWireSmsProvider` (with Twilio-compatible
  webhook signature verification), `ResendEmailProvider`, and `dispatch.py`
  (instant sends + capped/uncapped digest formatting, bounded concurrency).
  Every send returns a real success/failure outcome; the scheduler only
  marks a match `notified` — and only logs a `messages` row — for sends that
  actually succeeded (see "Bug fixes" below).
- **Assistant** (`app/assistant/`): `tools.py`
  (`search_postings`, `get_criteria`, `update_criteria`,
  `add_watchlist_company`, `remove_watchlist_company`, `list_watchlist`) and
  `agent.py` (the Claude tool-use loop), wired to `app/webhooks.py` —
  inbound SMS with `STOP`/`START`/`HELP` handled before the assistant is
  invoked. Tool inputs are validated/sandboxed before they can crash the
  loop or reach the DB (see "Bug fixes").
- **Company watchlist** (`app/watchlist/`, new): a user can ask the
  assistant in plain text ("let me know if Stripe posts anything") to track
  a specific company. `detect.py` auto-detects that company's real
  Greenhouse/Lever/Ashby board (no generic career-page scraping — same
  ToS-risk reasoning the spec used to rule out LinkedIn/Indeed); if found,
  it's wired into the scheduler as a real, persistent source via the
  registry. Watchlisted-company postings still pass the user's normal
  criteria filters, but are always instant-priority once matched. If no
  board is found, the user is told rather than the system silently doing
  nothing.
- **Scheduler** (`app/scheduler.py`): five independent APScheduler jobs —
  `fast_lane_cycle` (~2 min, reliable-tier + watchlist sources,
  change-detection first), `slow_lane_cycle` (~15 min, full sweep +
  staleness marking, the correctness backstop), `profile_backfill_cycle`
  (~1 min), the legacy `digest_cycle` (SMS at 8am/8pm), and
  `daily_email_cycle` (per-user email time) — all delivery is decoupled from
  scrape cadence.
- **DB** (`app/db/`): `users`, `criteria`, `postings`, `matches`, `messages`,
  `watchlist` via SQLAlchemy async + `create_all` (no Alembic, matching
  Posted's convention). A narrow idempotent startup migration adds the web
  profile columns to existing Phase-1 databases. `store_new_postings` catches `IntegrityError` from a
  same-`posting_key` race between lanes and retries the *whole* pass
  (including `last_seen_at` bumps on already-known postings in the same
  batch, not just the new-row insert) against the post-race DB state rather
  than crashing the cycle.

## Performance fixes (post-MVP hardening pass)

A follow-up review focused specifically on latency and scalability — the
things that erode "fast and personalized" as postings and users accumulate,
as opposed to the correctness gaps in the section below:

- **Sources were fetched sequentially**, in both `pipeline.run_sources`
  (slow lane's full sweep) and the fast lane's `check_for_changes()` poll —
  contradicting the spec's "sources run concurrently each cycle." Each
  source is independent I/O against a different host, so there was no
  correctness reason for it. Fixed via `asyncio.gather` in both places; a
  cycle now takes roughly the slowest single source's latency instead of the
  sum of all of them.
- **`store_new_postings` loaded the entire `postings` table every cycle**
  just to check which posting_keys already existed — an unscoped
  `SELECT * FROM postings` on every ~2 min fast-lane and ~15 min slow-lane
  tick, against a table that only grows (9,810+ rows in one live run
  already). Fixed by scoping the existence check to the batch's own
  posting_keys (`WHERE posting_key IN (...)`, index-backed and chunked for
  large multi-category sweeps).
- **Per-user Claude ranking calls ran one at a time**, and the fast lane
  fell back to the same per-user batching as the slow lane instead of the
  spec's per-posting-across-users batching for that lane. A cycle with N
  active users took N sequential round-trips to Claude before any instant
  SMS could go out — directly working against the fast lane's ~2 min
  latency budget. Fixed by rule-filtering all users first (cheap, no I/O),
  then firing the resulting per-user ranking calls concurrently
  (`asyncio.gather`, bounded by a `Semaphore(5)` so a cycle with many active
  users doesn't fire dozens of simultaneous requests at the Anthropic API).
- **`watchlisted_company_keys` was queried once per user inside the match
  loop** (N+1) instead of once for every candidate user up front. Fixed via
  `watchlisted_company_keys_by_user`.
- **A large survivor batch could silently truncate ranking to zero results.**
  `rank_postings` sent every one of a user's rule-filter survivors to Claude
  in a single call with `max_tokens=2048`; a big-enough batch (found live:
  89 postings against a broad demo criteria set) hit `stop_reason ==
  "max_tokens"` mid-array, which parsed as a well-formed but empty
  `results` list — indistinguishable from "the model scored everything as a
  poor fit." Fixed by chunking into groups of `_RANK_BATCH_SIZE` (20)
  postings per call (scored concurrently, bounded), plus a warning log if a
  chunk itself still gets truncated so this failure mode is never silent
  again. Confirmed against the real bug: the same 89-posting batch that
  previously produced 0 matches now correctly produces real scores for all
  89 (15 of which cleared the instant-priority threshold) against the real
  Anthropic API. See `tests/test_rank.py::test_rank_postings_chunks_large_batches`
  and `::test_rank_postings_reports_but_does_not_crash_on_truncated_response`.
- **`.env` at the repo root was never actually read.** `Settings` resolved
  `env_file=".env"` relative to the process's current working directory;
  since the app is normally run with `backend/` as cwd (`cd backend && uv
  run ...`) but `.env` lives at the repo root per this README's own
  Quick-start instructions, every value in it was silently ignored (empty
  defaults, no error — `check_keys.py` reported real keys as `MISSING`).
  Fixed by having `Settings` check both `backend/.env` and the repo-root
  `.env`.
- **Email provider switched from Gmail SMTP to Resend** (`app/notify/email_resend.py`)
  — a plain `httpx` call against Resend's HTTP API, matching how every other
  outbound integration in this repo (ATS sources, SignalWire) talks to its
  provider, rather than pulling in the `resend` SDK for one call site. See
  `RESEND_API_KEY`/`RESEND_FROM_EMAIL` in `.env.example`.

## Bug fixes from the pre-implementation review

A code review before this pass identified several correctness gaps against
the spec's stated guarantees; all are fixed and covered by regression tests
(`uv run pytest -k <name>` to see the specific test):

- **Fast-lane sources were rebuilt fresh every cycle**, silently resetting
  `check_for_changes()`'s state so it always reported "changed" — the fast
  lane was doing a full fetch every ~2 min instead of a cheap check. Fixed
  via `app/sources/registry.py`; see `tests/test_source_registry.py`.
- **Notification sends were never checked before marking a match
  `notified`** — a failed SignalWire/Resend send was silently and
  permanently lost (idempotency then blocked any retry) with no log trail.
  Fixed in `app/notify/dispatch.py`/`app/scheduler.py` (`SendOutcome`/
  `DigestOutcome`, conditional marking, per-send `messages` logging); see
  `tests/test_scheduler.py`, `tests/test_dispatch.py`.
- **A failed/empty LLM ranking result produced a permanent, blank
  score=0 match** instead of leaving the posting unmatched for retry next
  cycle. Fixed in `app/pipeline.py::match_new_postings`; see
  `tests/test_pipeline.py::test_match_new_postings_skips_posting_with_no_rank_result`.
- **The `IntegrityError` retry path in `store_new_postings` dropped
  `last_seen_at` bumps** for already-known postings in the same batch.
  Fixed by sharing one retry-safe helper (`_insert_and_touch`) between the
  first attempt and the retry; see
  `tests/test_pipeline.py::test_store_new_postings_reapplies_last_seen_at_bump_after_integrity_error_retry`.
- **Malformed/unexpected LLM tool-use input could crash the webhook**
  (uncaught `TypeError` from unexpected kwargs, unvalidated `role_types`
  persisted then blowing up the next matching cycle). Fixed with input
  validation in `app/assistant/tools.py`, defensive dispatch in
  `_execute_tool`, and a belt-and-suspenders catch in `webhooks.py`; see
  `tests/test_agent.py`, `tests/test_tools.py`.
- **Sponsorship was only a priority-demotion signal, not a hard filter** —
  a user requiring sponsorship could still be shown a non-sponsoring
  posting. Fixed in `app/matching/filters.py`; see
  `tests/test_filters.py`.
- **Only 4 companies were configured**, nowhere near enough real coverage.
  Expanded to 32, every slug live-verified against the real Greenhouse/
  Lever/Ashby APIs during this build (9,810 real postings fetched in the
  live run below).

## Deliberately not built

- **LinkedIn/Indeed scraping.** The spec itself flags these as ToS-risky and
  possibly "may never work reliably." I did not implement actual scraping —
  `linkedin.py`/`indeed.py` are inert placeholder `Source` subclasses whose
  `fetch()` returns `[]` and are never registered in `default_sources()`.
  Confirmed via a full-repo grep that nothing else touches those domains.
  This was a judgment call, not something the spec forced; flag it if you
  want it revisited. The same reasoning extends to watchlist detection:
  it only ever probes the three real ATS APIs, never generic career-page
  scraping.
- **Generic RSS feeds are unconfigured.** `RssFeedSource` is implemented and
  unit-tested against a fixture, but no arbitrary RSS URL is wired in. The
  PagesXYZ best-effort tier is configured across software, data, product,
  finance, consulting, marketing, sales, operations, and design when its
  publishable API key is present.
- **A2P 10DLC registration and Phase 2 multi-user rollout** — explicitly a
  Phase 2 gate per the spec, not part of this iteration.
- **Telnyx** — SignalWire only, per the spec's phasing.

## What I could not live-test

I don't have your SignalWire, Resend, or Anthropic credentials in this
environment (no `.env` exists yet). Everything downstream of those three
integrations is covered by tests using fake/mocked clients instead of real
API calls:

- `AnthropicClient`/`AgentClient` — `tests/test_rank.py`, `tests/test_agent.py`
  use scripted fake clients that return realistic tool-use response shapes,
  including malformed/unexpected ones (unknown tool names, missing fields,
  wrong content shapes — see the bug-fix list above).
- `SmsProvider`/email — `tests/test_dispatch.py` uses fake providers to
  verify routing, opt-out, message formatting, and both success and failure
  delivery outcomes.
- SignalWire webhook signature verification — real HMAC math, tested against
  itself in `tests/test_signalwire.py` (no live SignalWire request available).
  Note: if you deploy behind a TLS-terminating reverse proxy (nginx, an ALB),
  make sure it forwards proxy headers correctly (`--proxy-headers` on
  uvicorn, or equivalent) — otherwise the signature check will see an
  `http://` URL where SignalWire signed an `https://` one, and every real
  webhook will 403. This can't be caught by a unit test; verify it once
  against your actual deployment topology.

**What I did live-test** (no credentials needed): all real job sources
(GitHub lists + 22 Greenhouse companies + 2 Lever + 8 Ashby) against their
actual public APIs — see "Testing" below for exact numbers. Also live-tested
the watchlist's ATS auto-detection against five real companies (Stripe,
Notion, Palantir, Ramp, and a nonexistent one) — each correctly resolved to
its real board or correctly reported "not found." Also live-tested: the real
FastAPI app booting with the scheduler attached, and the real webhook
endpoint (signature enforcement, and the STOP/START flow against a seeded
user) via an actual running `uvicorn` process, not just `TestClient`.

Once you add real credentials to `.env`, the LLM ranking, SMS sends, and
email sends should be spot-checked once for real before trusting them in
production — nothing about their live behavior has been verified end-to-end.
A reasonable order: (1) `check_keys.py` to confirm all three are set; (2)
`run_scrape_once.py` once with a real `ANTHROPIC_API_KEY` and a small
`companies.yaml`/one seeded user, to confirm the real model's tool-use
output actually matches what `rank.py` expects (the one thing the fake
clients can't validate); (3) send yourself one real instant SMS and one
real digest before adding friends; (4) verify the webhook signature check
against a real SignalWire-originated request once actually deployed, per
the proxy-header note above.

## Quick start

Requirements: Python 3.12+, Node 22+, [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
cp ../.env.example ../.env   # fill in auth, Resend, and Anthropic values
uv run python scripts/seed_demo.py        # seeds one demo user + criteria
uv run python scripts/run_scrape_once.py  # dry-run: scrape, rank, match, print — no sends
```

Run the tests:

```bash
cd backend
uv run pytest
```

Run the API (FastAPI + all four scheduler jobs):

```bash
cd backend
uv run uvicorn app.main:app --reload
```

Run the web app in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Then open `http://localhost:5173`. Vite proxies `/api` to port 8000.

Or run the complete local stack with Postgres:

```bash
docker compose up --build
```

The web app is at `http://localhost:5173`; the API health endpoint is at
`http://localhost:8000/health`.

Production uses the same single-API/scheduler topology:

```bash
docker network create portfolio-edge  # once per host
docker compose -f compose.production.yml up -d --build
```

Set every required `${VAR:?...}` value in the root `.env` first. Configure the
shared edge proxy to route the public hostname to the `automated-jobs-web`
network alias. Do not scale the API service above one replica while
APScheduler runs in-process.

Confirm secrets are present without printing them:

```bash
cd backend
uv run python scripts/check_keys.py
```

Note: `check_keys.py` (and the app generally) reflects whatever
`ANTHROPIC_API_KEY`/etc. are set in your *process environment*, not only
`.env` — pydantic-settings reads both. If you have any of these exported in
your shell already, `check_keys.py` will report them as "set" even without a
`.env` file.

## Testing

133 automated tests, all passing (`uv run pytest`):
- Pure logic: `normalize`, `dedupe`, `filters` (including the sponsorship
  hard filter), `rank.compute_priority`, `infer_role_type`, ATS/RSS payload
  parsing, digest formatting, watchlist slug-candidate generation.
- DB-backed (in-memory SQLite): assistant tools (including the new
  watchlist tools and criteria-change validation), the tool-use agent loop
  (including malformed/unexpected LLM tool-use input), cross-lane match
  idempotency, the rank-failure-must-not-permanently-block-a-match
  regression, the watchlist-forces-instant-priority behavior, the
  `IntegrityError` retry path, and scheduler-level delivery-outcome
  recording (`_record_instant_outcomes`/`_record_digest_outcomes`).
- Web/API: signup consent, password hashing, cookie/CSRF enforcement, strict
  profile updates, in-memory DOCX parsing, structured-only resume persistence,
  Workday pagination/change detection, generalized ranking inputs, and
  independent SMS/email pending-delivery behavior.
- Source registry: instance-persistence-across-cycles regression (the exact
  bug that broke the fast lane's change detection — see `test_source_registry.py`).
- HTTP-level: the real webhook endpoint via FastAPI's `TestClient`
  (STOP/START/HELP/unknown-number), plus a real running `uvicorn` process hit
  with `curl` for the same flows and for signature enforcement.

Live (real network, no credentials) verification performed during this
build, each re-run to confirm idempotency:
- `run_scrape_once.py` against the real, expanded `companies.yaml` (GitHub
  lists + 22 Greenhouse + 2 Lever + 8 Ashby companies): fetched **9,810**
  real postings, 0 new on re-run, 0 crashes.
- `app.watchlist.detect.detect_ats_board` against 5 real companies (Stripe,
  Notion, Palantir, Ramp, and a nonexistent company): correctly resolved
  each real company to its actual board (`greenhouse`/`ashby`/`lever`
  respectively) and correctly returned "not found" for the nonexistent one.
- A temporary script substituting a fake (but realistic) ranking client
  against a real posting set, to exercise the LLM-ranking →
  priority-split → instant/digest routing logic without spending real API
  credits: correctly produced high-priority instant matches when scored
  above threshold, correctly routed below-threshold postings to the digest
  bucket, and `gather_pending_digests` correctly found and then, after
  marking notified, stopped finding those matches.

## Architecture

See the design doc for full rationale. Module boundaries match the target
shape exactly: `sources/` (plus `sources/registry.py` for cross-cycle
instance persistence), `ingest/`, `matching/`, `notify/`, `assistant/`,
`watchlist/`, `resume/`, `auth/`, and `api/`, plus `scheduler.py`,
`webhooks.py`, and `pipeline.py`
(I/O orchestration gluing the pure modules together, kept separate from them
per the design's isolation principle).
