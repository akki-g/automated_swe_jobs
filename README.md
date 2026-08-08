# automated_swe_jobs

Scrapes new-grad SWE and internship postings from many sources, matches them
against each user's criteria, and alerts by SMS and email. A conversational
assistant (over SMS) lets users refine their criteria, search postings, and
track specific companies on a watchlist.

Full design: `docs/superpowers/specs/2026-08-07-automated-swe-jobs-design.md`.

## What's built (working MVP)

This implements the spec's full Phase 1 build order (steps 1–6), plus a
post-review hardening pass and a new company-watchlist feature.

- **Sources** (`app/sources/`): SimplifyJobs New-Grad-Positions and
  Summer-Internships GitHub lists; Greenhouse/Lever/Ashby ATS adapters driven
  by a curated `companies.yaml` — **32 companies**, every slug live-verified
  against the provider's real public API during this build (see "Testing"
  below); a generic RSS/JSON aggregator adapter (`RssFeedSource`,
  unconfigured by default — add a feed URL to use it). All reliable-tier
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
  webhook signature verification), `GmailEmailProvider`, and `dispatch.py`
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
- **Scheduler** (`app/scheduler.py`): three independent APScheduler jobs —
  `fast_lane_cycle` (~2 min, reliable-tier + watchlist sources,
  change-detection first), `slow_lane_cycle` (~15 min, full sweep +
  staleness marking, the correctness backstop), and `digest_cycle` (cron,
  default 8am/8pm) which reads *all* un-sent normal-priority matches from
  either lane and sends them — decoupled from scrape cadence, per spec.
- **DB** (`app/db/`): `users`, `criteria`, `postings`, `matches`, `messages`,
  `watchlist` via SQLAlchemy async + `create_all` (no Alembic, matching
  Posted's convention). `store_new_postings` catches `IntegrityError` from a
  same-`posting_key` race between lanes and retries the *whole* pass
  (including `last_seen_at` bumps on already-known postings in the same
  batch, not just the new-row insert) against the post-race DB state rather
  than crashing the cycle.

## Bug fixes from the pre-implementation review

A code review before this pass identified several correctness gaps against
the spec's stated guarantees; all are fixed and covered by regression tests
(`uv run pytest -k <name>` to see the specific test):

- **Fast-lane sources were rebuilt fresh every cycle**, silently resetting
  `check_for_changes()`'s state so it always reported "changed" — the fast
  lane was doing a full fetch every ~2 min instead of a cheap check. Fixed
  via `app/sources/registry.py`; see `tests/test_source_registry.py`.
- **Notification sends were never checked before marking a match
  `notified`** — a failed SignalWire/Gmail send was silently and
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
- **Aggregator feeds are unconfigured.** `RssFeedSource` is implemented and
  unit-tested against a fixture, but no real feed URL is wired in — I didn't
  have a specific one to point at. Add one via `default_sources()` when you
  have a URL in mind.
- **A2P 10DLC registration and Phase 2 multi-user rollout** — explicitly a
  Phase 2 gate per the spec, not part of this iteration.
- **Telnyx** — SignalWire only, per the spec's phasing.

## What I could not live-test

I don't have your SignalWire, Gmail, or Anthropic credentials in this
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

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
cp ../.env.example ../.env   # fill in SignalWire, Gmail, Anthropic creds
uv run python scripts/seed_demo.py        # seeds one demo user + criteria
uv run python scripts/run_scrape_once.py  # dry-run: scrape, rank, match, print — no sends
```

Run the tests:

```bash
cd backend
uv run pytest
```

Run the service (FastAPI + all three scheduler jobs):

```bash
cd backend
uv run uvicorn app.main:app --reload
```

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

112 automated tests, all passing (`uv run pytest`):
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
`watchlist/` (new), plus `scheduler.py`, `webhooks.py`, and `pipeline.py`
(I/O orchestration gluing the pure modules together, kept separate from them
per the design's isolation principle).
