# automated_swe_jobs — Design

**Date:** 2026-08-07
**Status:** Approved design, pre-implementation

## Purpose

Automatically discover the most recent **new-grad SWE** and **internship** job
postings from many sources, match them against each user's criteria, and alert
users by SMS and email. A conversational assistant (over SMS) lets users refine
their criteria and search postings in natural language.

## Scope & rollout

- **Phase 1 (now):** "me + a few friends." A small `users` table with per-user
  criteria and contact info. No public signup, no auth UI.
- **Phase 2 (later):** public multi-user product. The data model and opt-in/
  opt-out plumbing are built now so this is a growth step, not a rewrite. A2P
  10DLC carrier registration is an explicit external prerequisite for this
  phase (see [Notifications](#notifications)) — start that process before
  Phase 2 launch planning assumes SMS works at scale.

**Phase 1 build order** (this is one product but not one implementation
plan — sequence it so a working, if simple, pipeline exists early):
1. Scaffold + DB models + one source (a GitHub list, cheapest to integrate) +
   normalize/dedupe, exercised via `run_scrape_once.py`.
2. Rule-based matching + digest SMS/email send, no LLM ranking yet, no
   instant tier, no assistant — get the deterministic end-to-end path
   working and observable first.
3. LLM ranking + the instant/digest priority split.
4. Assistant/webhook + tool-use loop + `STOP`/`START`/`HELP`.
5. Remaining sources (ATS APIs, aggregators, best-effort boards) added
   incrementally — the pluggable `Source` protocol makes this naturally
   independent of the above.
6. **Fast lane** (see [Scheduling](#scheduling-two-lane)) — added once the
   slow-cycle pipeline from steps 1-5 is correct and running. The fast lane
   is a latency *optimization* layered on top of an already-correct system,
   not something to build in parallel with it.

## Runtime & stack

- Deployed on an always-on **EC2** instance.
- **Python 3.12 + FastAPI + SQLAlchemy (asyncio)**. **PostgreSQL from the start**,
  not "later" — a fast (~2 min) scheduling lane and a slow (~15 min) lane can
  both be writing concurrently (see [Scheduling](#scheduling-two-lane)), and
  SQLite's single-writer lock becomes real contention well before "hundreds of
  users" is reached. SQLite is retained only for local dev/tests, where a
  single-process, single-writer assumption is safe. Dependency management via
  **uv**. Assistant uses the **Anthropic SDK** (Claude). Mirrors the existing
  Posted project's stack and conventions.
- **In-process APScheduler** for scheduled scraping and digests (no external cron
  needed; the box is always on) — two scheduled lanes, not one (see below).
  Still in-process/single-box: at Phase 1's target scale (hundreds of users,
  not thousands) this doesn't need a distributed task queue (Celery/RQ/etc.);
  a bounded async DB connection pool and a bounded concurrency semaphore for
  outbound sends (see [Notifications](#notifications)) are enough headroom.

## Architecture

Two entry points into one shared core:

1. **Scheduler** — pull jobs → normalize → dedupe → store → match per user →
   LLM rank → notify (instant vs digest). Runs as **two lanes** (see
   [Scheduling](#scheduling-two-lane)): a fast, cheap-change-detection lane
   for low-latency instant alerts, and a slow, full-sweep lane that is the
   source of correctness (it re-checks everything, so the fast lane can be
   best-effort without a durable queue).
2. **Webhook** — inbound SMS reply → assistant (Claude tool-use) → update
   criteria / search → reply via SMS.

Both share the DB, the source layer, and the notifier. Everything is built around
**pluggable providers** so adding a source or swapping an SMS vendor is config,
not surgery. Adapters translate vendor payloads at the boundary; the core owns the
business logic and never depends on a specific job site or SMS vendor (same
boundary discipline as Posted's Schwab/Plaid adapters).

```
  Job sources                 EC2 (always-on)
  ATS APIs   ─┐    APScheduler (every N min)
  GitHub lists┼─► Source adapters → normalize → dedupe → store (new postings)
  Aggregators ┤                                     │
  Big boards ─┘ (best-effort)                       ▼
                                       Matching: rule filters per user
                                                    │
                                                    ▼
                                    LLM rank + blurb (Claude, batched)
                                          ┌─────────┴────────┐
                                          ▼                  ▼
                                   high-priority        rest → digest
                                   → instant SMS        (scheduled)
                                          │                  │
                                          ▼                  ▼
                          Notifier: SmsProvider (SignalWire→Telnyx) + Gmail

  FastAPI webhook ◄── inbound SMS reply ── user
        │
        ▼
  Assistant (Claude tool-use): search_postings, get_criteria, update_criteria
        → replies via SMS
```

The diagram shows one logical pipeline, but the scheduler drives it via two
lanes at different cadences — see [Scheduling](#scheduling-two-lane) for how
the fast (~2 min) and slow (~15 min) lanes divide the work.

## Sources (tiered, pluggable)

All sources implement a `Source` protocol (`fetch() -> list[RawPosting]`). Enabled
sources run concurrently each cycle. **A source that errors or is blocked logs and
yields nothing — it never breaks the run.**

Reliable-tier sources additionally implement a cheap
`check_for_changes() -> bool` distinct from `fetch()` — this is what makes
polling them every ~2 min (the fast lane, see
[Scheduling](#scheduling-two-lane)) affordable:
- ATS APIs: compare a cheap signal (job count, or a board `updated_at` if the
  provider exposes one) against the last-seen value.
- GitHub lists: conditional GET (ETag / If-Modified-Since) on the raw file.
- Aggregators and best-effort boards don't implement this — they stay on the
  slow lane only, since they're either not cheap enough to poll that often or
  not reliable enough to be worth the extra load.

- **Reliable backbone:**
  - **ATS APIs** — Greenhouse, Lever, Ashby job boards (Workday later). Curated
    company list.
  - **Curated GitHub lists** — SimplifyJobs New-Grad-Positions & Summer/Internship
    repos (structured, high-signal for exactly these roles).
  - **Aggregator feeds** — RSS/JSON feeds built for consumption.
- **Best-effort tier:**
  - **Big boards** — LinkedIn / Indeed. Highest coverage but brittle and
    ToS-risky; isolated so their failures are expected, not fatal. Never the
    backbone. LinkedIn in particular has no public jobs API for this use case
    and actively fights scraping (IP bans, JS challenges, CFAA claims in past
    litigation) — treat this adapter as "may never work reliably," not a
    near-term deliverable. If pursued, low-frequency and isolated to its own
    IP/credentials so a ban never touches the reliable-tier sources.

**Company-list maintenance (ATS tier):** the curated Greenhouse/Lever/Ashby
company list lives in a config file or DB table (not hardcoded per-adapter),
since it's an operational concern that will need frequent editing without a
deploy. It also needs an explicit refresh path — e.g., periodically diffing
the SimplifyJobs GitHub lists against the curated list to surface companies
worth adding — since ATS coverage is only as good as the list of companies
you thought to include.

**Posting staleness:** sources don't announce closures, so the system infers
them. A posting not re-seen by its source for N consecutive cycles (default:
N=4, ~1 hour at the 15-min cadence, per source) is marked `stale` and
excluded from matching and digests even if never explicitly notified. See
`postings.status`/`last_seen_at` in the data model.

## Scheduling (two lanes)

Latency for high-priority alerts matters, but the 15-min full-sweep cycle
described in [Data flow](#data-flow) can't shrink much without hitting
rate-limit/ToS risk (see [Sources](#sources-tiered-pluggable)) or adding
infra this system doesn't need at Phase 1 scale. Instead of one cycle, the
scheduler runs two:

- **Fast lane (~2 min):** reliable-tier sources only, using
  `check_for_changes()` — nearly free when nothing changed. On a detected
  change, it runs a real `fetch()` for *that source only*, normalizes,
  dedupes, and immediately runs match → rank → notify for just the new
  postings (see [Matching](#matching) for how ranking batches in this lane).
  This is what gets a high-priority match out in ~2 min instead of ~15.
- **Slow lane (~15 min, unchanged from the original design):** full sweep
  across *all* source tiers, staleness marking, and the digest sweep. Also
  re-evaluates everything, including postings the fast lane should already
  have caught — this is the correctness backstop. Because idempotency
  (`posting_key` + `matches` uniqueness) makes re-processing safe, the fast
  lane can be **best-effort with no durable queue**: if it misses a change
  (a bug, a restart mid-cycle), the slow lane catches it within 15 min
  regardless. This is what keeps the fast lane cheap to build — it never has
  to be the source of truth, only a latency shortcut.

Both lanes are APScheduler jobs sharing the same DB, the same source
adapters, and the same matching/notify code — the fast lane doesn't
duplicate logic, it just calls a narrower slice of it (fewer sources, and
per-posting instead of per-cycle-batch).

## Matching

**Rule-based filters + LLM ranking:**
1. Deterministic filters narrow the field: role type (new-grad/intern), keywords,
   location, sponsorship, date posted. The assistant translates a user's
   natural-language criteria into these structured filters.
2. Claude scores/ranks the survivors for fit and writes the alert blurb. The
   batching axis differs by lane:
   - **Slow lane:** batched **per user** (all of that user's new survivors
     for the cycle in one call) — the original design's approach, and the
     right one when a user may have several new candidates at once.
   - **Fast lane:** batched **per posting, across all active users** (one
     new posting × every user's criteria, in one call) — a fast-lane tick
     usually surfaces zero or one new posting, so batching by posting is
     cheaper and lower-latency than one call per user.
   Total call volume is roughly the same either way (same posting×user pairs
   get evaluated once); only the grouping changes with whichever axis has
   fewer items at that moment.

## Notifications

**Hybrid delivery:**
- **Instant SMS** for high-priority matches. Priority is a concrete, testable
  rule, not a vibe: `priority = high` when the LLM fit score clears a
  threshold (default 0.9) **and** all hard-required criteria (role_type,
  sponsorship_required, any required keywords) are satisfied exactly.
  Everything else that survives the rule filters is `normal`. The threshold
  is a config value, expected to need tuning once real score distributions
  are observed.
- **Digest** (SMS + email) for the rest, on a separate schedule (e.g. 8am/8pm).
  Digest SMS caps at the top N matches by score (default N=8) with a
  "+X more — reply LIST for all" tail rather than growing unbounded
  multi-segment messages; the email digest is uncapped since length isn't a
  concern there.

**Channels:**
- **SMS** behind an `SmsProvider` interface. **SignalWire** is the initial POC
  provider (auth available now); **Telnyx** becomes the primary later. Registering
  both behind one interface makes the migration config-only, and lets the
  dispatcher fall back on send failure.
  - Failover is send-attempt-scoped, not match-scoped: the dispatcher only
    fails over to the secondary provider on an unambiguous send error (e.g.
    an explicit rejection response), never on a timeout/ambiguous result,
    since a timeout may mean the message actually delivered and a blind
    retry through a second provider risks a duplicate text to the user. Every
    attempt (provider, timestamp, result) is logged against the `messages`
    row so a stuck send can be diagnosed rather than silently retried.
- **Email** via **Gmail**.
- `STOP`/`START`/`HELP` handled as first-class opt-out/opt-in.
- **Dispatch concurrency:** both instant sends (fast lane) and digest fan-out
  (slow lane's digest cycle) go out via `asyncio.gather` under a bounded
  concurrency semaphore (default ~10 concurrent sends) against the
  `SmsProvider`/Gmail clients, rather than sequential sends — sequential
  sending is fine at "a few friends" scale but becomes the throughput
  bottleneck for a digest fanning out to dozens+ users.

**Compliance note (Phase 2 gate):** unregistered/low-volume SMS is fine for
Phase 1's known, small user set. Public rollout requires **A2P 10DLC campaign
registration** with the carriers before SMS at scale — this has real lead
time (days to weeks) and requires a documented consent/use-case narrative. It
is an external dependency, not just a code change, and should be kicked off
before Phase 2 launch planning assumes SMS "just works" at scale. Consent
capture (see `users.consent_at`/`consent_method` in the data model) needs to
exist before Phase 2 for the same reason: carriers require evidence of
opt-in, not just an opt-out mechanism.

## Assistant & tools

Conversational **over SMS**: users reply to alert texts to refine criteria or
search. Inbound webhook → load user + criteria + recent messages → Claude
tool-use loop → persist changes → reply within SMS length limits.

The "MCP tool" is the **internal tool layer** the assistant calls via Claude
tool-use (not a standalone MCP server for Phase 1). Tools:
- `search_postings(filters)` — query stored postings.
- `get_criteria(user)` — read the user's current criteria.
- `update_criteria(user, changes)` — edit structured filters and freeform notes.

Tools are built cleanly enough to expose as a real MCP server later if desired.

## Directory layout

```
automated_swe_jobs/
├── backend/
│   ├── app/
│   │   ├── sources/          # Source protocol + adapters
│   │   │   ├── base.py
│   │   │   ├── ats/          # greenhouse.py, lever.py, ashby.py
│   │   │   ├── companies.yaml # curated ATS company list (edited without a deploy)
│   │   │   ├── github_lists.py
│   │   │   ├── aggregators.py
│   │   │   └── boards/       # linkedin.py, indeed.py (best-effort, low priority)
│   │   ├── ingest/
│   │   │   ├── normalize.py  # RawPosting -> Posting (pure)
│   │   │   └── dedupe.py     # posting_key, cross-source dedup (pure)
│   │   ├── matching/
│   │   │   ├── filters.py    # rule-based (pure)
│   │   │   └── rank.py       # Claude ranking + blurb
│   │   ├── notify/
│   │   │   ├── sms/          # base.py, signalwire.py, telnyx.py
│   │   │   ├── email_gmail.py
│   │   │   └── dispatch.py   # hybrid routing + opt-out
│   │   ├── assistant/
│   │   │   ├── tools.py
│   │   │   └── agent.py
│   │   ├── scheduler.py      # APScheduler: fast lane, slow lane, digest
│   │   ├── webhooks.py       # inbound SMS + signature verify
│   │   ├── domain/models.py  # RawPosting, Posting, Criteria, Match
│   │   ├── db/               # SQLAlchemy async models + session
│   │   └── main.py           # FastAPI wiring
│   ├── scripts/              # check_keys.py, run_scrape_once.py, seed_demo.py
│   └── tests/
├── .env.example
├── pyproject.toml            # uv
└── README.md
```

## Data model

- **users** — id, name, phone (E.164), email, sms_provider pref, opt_out flags,
  `consent_at`, `consent_method` (e.g. "verbal-friend-onboarding" for Phase 1,
  a real signup-flow value for Phase 2), created_at.
- **criteria** — user_id, structured filters (role_types, keywords, locations,
  sponsorship_required, min_date) + `freeform_notes` (fed to the LLM ranker). One
  row per user, edited by the assistant. Phase 1 assumption: one active
  criteria row per user (e.g. role_types can include both new-grad and
  intern); revisit if Phase 2 wants multiple independently-managed saved
  searches per user.
- **postings** — `posting_key` (dedup, see below), source, company, title, url,
  location, role_type, posted_at, raw JSON, `first_seen_at`, `last_seen_at`,
  `status`. Defaults to `open` at insert; transitions to `stale` via the
  not-re-seen-for-N-cycles rule (see [Sources](#sources-tiered-pluggable)).
  `closed` is reserved for a future explicit-signal source (e.g. an ATS API
  that reports closed reqs directly) — Phase 1 has no source that sets it,
  so in practice only `open`/`stale` are used for now. Unique index on
  `posting_key`.
- **matches** — user_id × posting_id, score, blurb, priority (high/normal),
  notified_channels, notified_at, `lane` (fast/slow — which lane created the
  match, for observability into fast-lane hit rate), `match_reason` (e.g.
  "new_posting" vs. "criteria_backfill" — see below). Prevents re-notifying
  the same job to the same person. Unique index on `(user_id, posting_id)`;
  additional index on `(user_id, notified_at)` so the digest cycle's "gather
  each user's un-sent normal matches" is an index lookup, not a scan, as the
  matches table grows with users.
- **messages** — inbound/outbound SMS log (assistant context + debugging),
  with a nullable FK to the `match` a reply is responding to, where
  determinable.

**`posting_key` construction:** built from normalized `(company, title,
location)` (lowercased, whitespace-collapsed, common suffixes stripped —
e.g. "Inc.", "(Remote)") rather than from the source URL or a
per-source ID, because the same role commonly appears across multiple
sources (a company's own Greenhouse page *and* a GitHub list) with different
URLs, and companies routinely close and re-open a req for the same role. This
normalized key is what cross-source dedup (`dedupe.py`) keys on; the source
URL is retained on the row as a secondary field but is not part of identity.
This is a heuristic, not a guarantee — near-duplicate titles across a
company's teams can still collide or miss; treat `dedupe.py`'s matching rule
as a tunable, tested-in-isolation unit rather than a solved problem.

`posting_key` + `matches` uniqueness prevents re-notifying the exact same
`(user, posting)` pair across sources and restarts — it does **not** by
itself guarantee no duplicate alerts for reposted roles that don't collapse
to the same key (see the dedup heuristic above), which is a known, accepted
gap rather than a silent one.

**Criteria-change re-matching policy:** editing criteria via the assistant
does **not** re-scan the full posting history by default — only postings
ingested going forward are matched against the new criteria, exactly like
the normal cycle. A user can explicitly ask the assistant ("show me what I
missed") to trigger a bounded backfill match against postings from the last
7 days; those matches are written with `match_reason = "criteria_backfill"`
and delivered via the assistant's reply, not the instant-alert path, so an
editing session can never silently trigger a flood of instant SMS.

## Data flow

**Fast-lane cycle (every ~2 min):**
1. Run `check_for_changes()` on reliable-tier sources only (concurrently);
   unchanged sources are skipped, errors are logged and yield nothing.
2. For sources reporting a change: `fetch()` → normalize → dedupe by
   `posting_key` → insert only new `postings`; update `last_seen_at`.
3. For each newly-inserted posting: rule filters across all active users →
   batch survivors **across users** to Claude for rank + blurb (see
   [Matching](#matching)).
4. High-priority results → instant SMS immediately; write `matches` with
   `lane = "fast"` (idempotent — a match already written by a prior cycle,
   from either lane, is skipped). Normal-priority results are left for the
   slow lane's digest sweep to pick up — the fast lane does not maintain its
   own digest queue.

**Slow-lane cycle (every ~15 min, the correctness backstop):**
1. Run enabled sources concurrently — all tiers, including aggregators and
   best-effort boards; failing sources yield nothing.
2. Normalize → dedupe by `posting_key` → insert only new `postings`; update
   `last_seen_at` on postings re-seen this cycle.
3. Mark postings not re-seen for N consecutive cycles as `stale`; excluded
   from matching from this point on.
4. Per active user: rule filters over non-stale postings → batch survivors to
   Claude for rank + blurb. If a user has zero survivors, no LLM call is made
   for them that cycle.
5. Split by priority (see [Notifications](#notifications) for the threshold):
   high → instant SMS; normal → mark for next digest.
6. Write `matches` with `lane = "slow"` (idempotent — already-notified pairs,
   including ones the fast lane already wrote, are skipped). A failed LLM
   call for a user is not retried mid-cycle; the affected postings remain
   unmatched for that user and are naturally re-evaluated next cycle since
   they're still new/non-stale.

Because the slow lane re-runs the full match logic regardless of what the
fast lane already did, a fast-lane miss (crash mid-cycle, a source's
`check_for_changes()` false negative) never causes a missed alert — only a
delayed one, bounded by the slow lane's 15-min cadence.

**Digest cycle (e.g. 8am/8pm):** gather each user's un-sent normal matches
(from either lane) → one SMS + email summary → mark sent.

**Inbound SMS:** verify signature → load user by phone → load criteria + recent
messages → Claude tool-use loop → persist → reply within length limits.
`STOP`/`START`/`HELP` first-class.

## Error handling principles

- **Source isolation** — one dead source never sinks the cycle.
- **Notifier failover** — `SmsProvider` interface; on unambiguous send failure
  (not on timeout/ambiguous result, to avoid a double-text — see
  [Notifications](#notifications)) the dispatcher can fall back (Telnyx
  later). All sends logged with delivery status.
- **Idempotency everywhere** — `posting_key` and `matches` uniqueness make re-runs
  and restarts safe; no double-texts for the same `(user, posting)` pair. This
  is also what makes the fast lane safe to run best-effort (see
  [Scheduling](#scheduling-two-lane)): the slow lane can freely re-process
  anything the fast lane already handled.
- **Source politeness** — the `Source` protocol carries per-source rate/backoff
  config so adding companies to the ATS tier doesn't silently trip provider
  rate limits as the list grows; not needed to solve fully for Phase 1's
  small company list, but the interface should have room for it now.
- **DB connection pool sizing** — the async engine's pool is explicitly bounded
  (not left at a default that assumes one caller) since the fast lane, slow
  lane, digest cycle, and inbound webhook can all be touching the DB
  concurrently; sized generously enough for Phase 1's target scale
  (hundreds of users) without needing PgBouncer or similar in front of it yet.
- **Secrets** — all keys in git-ignored `.env`; a `check_keys.py` confirms presence
  without printing values (matching Posted's habit).

## Testing

- **Pure modules** (`normalize`, `dedupe`, `filters`) — unit tests, no I/O; the
  bulk of coverage.
- **Source adapters** — tested against recorded fixture payloads; no live calls in
  tests. `check_for_changes()` is tested separately from `fetch()` (change
  detected / not detected / detection itself errors) since it's the fast
  lane's only safeguard against needless full fetches.
- **Scheduling** — fast-lane and slow-lane jobs tested for the idempotency
  property directly: running both against the same new posting produces
  exactly one `matches` row, in either order.
- **Notifiers & assistant** — provider clients mocked; verify routing, opt-out,
  length limits, tool-call handling.
- **`scripts/run_scrape_once.py`** — manual end-to-end dry-run (scrape → match →
  print, no send) for real-world sanity checks.

## Explicit non-goals (Phase 1)

- No public signup / auth UI.
- No web or mobile frontend (assistant is SMS-only for now).
- No standalone MCP server (tools are internal; MCP exposure is a later option).
- Big-board scraping is best-effort, not a guaranteed source.
- No distributed task queue (Celery/RQ/etc.) or multi-box deployment — the
  in-process two-lane APScheduler setup is sufficient at Phase 1's target
  scale (hundreds, not thousands, of users); revisit if that target changes.
