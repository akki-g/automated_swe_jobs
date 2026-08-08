# Web onboarding, resume profile, daily email, and multi-field expansion plan

**Date:** 2026-08-08
**Status:** Implemented and verified

## Product and architecture decisions

- Build a web-first Vite + React + TypeScript client. There is no mobile or
  shared-client requirement in this product, so Expo would add operational and
  dependency weight without a concrete reuse benefit.
- Use email/password authentication. Passwords are Argon2-hashed; successful
  signup/login issues a short-lived signed token in an HttpOnly, SameSite cookie
  plus a double-submit CSRF token for authenticated mutations. This is
  proportionate to a known-user rollout and does not make signup depend on email
  delivery.
- Keep APScheduler in the API process. The existing single-box deployment,
  bounded database pool, and bounded outbound/Claude concurrency already have
  ample headroom for 30 concurrent users; another process would introduce
  duplicate-scheduler coordination without solving a current constraint. Run one
  API replica while the scheduler is in-process.
- Add a separate daily email job. Keep the existing 08:00/20:00 SMS digest job
  for the dormant/returning SMS channel, but make pending-delivery and recording
  channel-aware so an SMS delivery cannot hide a match from the daily email (or
  vice versa).
- Parse PDF with `pypdf` and DOCX with `python-docx`. Both accept file-like
  objects, which lets the route enforce a small upload limit and process bytes in
  memory. No upload or extracted text is written to disk, persisted, or logged.
- Add Workday as a first-class configured source. Workday's public career-site
  API is usable but tenant/site-specific, unlike the single slug used by the
  other ATS adapters. Live validation on 2026-08-08 found multiple generalist
  employers with valid payloads. Workday limits result pages to 20 and some
  boards expose 1,000–2,000 jobs, so each configured board uses a bounded newest
  window. This avoids hundreds of requests per company every slow-lane tick while
  still covering newly posted roles; the cap is explicit and configurable.

## Stage 1: schema and domain model

1. Make `users.phone` nullable for email-only web users; make `users.email`
   indexed/unique; add `password_hash`, `profile_completed_at`, and
   `email_digest_enabled`.
2. Extend `criteria` with `target_fields`, `resume_profile`, and
   `resume_updated_at`. Keep the existing role types, keywords, locations,
   sponsorship, date, and freeform notes intact.
3. Add a curated `TargetField` enum with software engineering, data
   science/analytics, product management, finance/investment banking,
   consulting, marketing, sales, operations, and design.
4. Flow the new fields through DB-to-domain conversion and ranking payloads.

## Stage 2: auth and profile API

1. Add isolated auth helpers for Argon2 password hashing, token signing,
   cookie issuance, current-user lookup, and CSRF validation.
2. Add `/api/auth/signup`, `/api/auth/login`, `/api/auth/logout`, and
   `/api/auth/me`. Signup records `consent_at` and a real
   `web-signup-terms-v1` consent method.
3. Add `/api/profile` GET/PUT with strict Pydantic validation for taxonomy,
   role types, bounded strings/lists, and profile completion.
4. Add same-origin CORS configuration for the Vite development server while
   production uses nginx's same-origin `/api/` proxy.

## Stage 3: resume ingestion

1. Add an in-memory parser boundary that validates extension/content type,
   limits upload size/page/text length, and extracts text from PDF/DOCX.
2. Add a dedicated Claude tool schema returning bounded skills, past titles,
   experience level/years, inferred target fields, education fields, and a
   short matching summary.
3. Validate and normalize the tool response before persistence. The API
   returns inferred fields for form prefill but does not overwrite the user's
   selected taxonomy.
4. Add `/api/profile/resume`; each upload replaces the prior structured result.
5. Test parsers with generated in-memory documents and Claude calls with a fake
   client. Assert persisted/serialized data never contains raw text.

## Stage 4: daily email and channel-aware delivery

1. Generalize `gather_pending_digests` with a requested channel and eligibility
   filters. Email includes all new unmatched-by-email match priorities for users
   with completed profiles and email digest enabled; legacy SMS remains normal
   priority and phone/opt-out aware.
2. Add email-only and SMS-only dispatch functions while retaining the existing
   combined function for compatibility.
3. Merge successful channels into `notified_channels` and set `notified_at` as
   the legacy "delivered somewhere" timestamp. Failed channels remain pending.
4. Schedule `daily_email_cycle` once per day at a configurable hour/timezone and
   retain the existing SMS cadence separately. Do not alter fast/slow matching.

## Stage 5: Workday and multi-field coverage

1. Implement tenant/host/site-aware Workday pagination, payload conversion,
   and cheap total-count change detection.
2. Extend `companies.yaml` to accept structured Workday board entries with
   human-readable company names and per-board fetch limits.
3. Add only live-verified boards spanning finance, consulting/investing,
   consumer, retail, payments, entertainment/media, and generalist employers.
4. Broaden new-grad inference only with unambiguous program language such as
   graduate roles/programs and management trainees; do not classify every
   generic analyst/associate title as entry-level.
5. Generalize the Claude ranking system message and include target fields plus
   the structured resume profile as additional fit signals.

## Stage 6: frontend

1. Build responsive signup/login screens and an authenticated onboarding shell.
2. Build a guided questionnaire for target fields, role types, locations,
   keywords, sponsorship, and notes.
3. Add PDF/DOCX upload with explicit privacy copy, progress/error states, and
   inferred-field suggestions the user can accept or ignore.
4. Add a completion/dashboard state where users can review and update profile,
   resume signals, and daily email preference.
5. Verify TypeScript and production build; test the critical signup/profile/
   upload flow against the running API in a real browser if the environment
   supports it.

## Stage 7: containers, documentation, and verification

1. Add the multi-stage uv backend image with non-root runtime and `/health`
   healthcheck.
2. Add a multi-stage Vite/nginx web image with same-origin API proxy.
3. Add local Postgres/API/web Compose and hardened production Compose using the
   external `portfolio-edge` network and required secrets.
4. Extend `.env.example`, `check_keys.py`, and README with setup, architecture,
   privacy, scheduling, schema upgrade, and deployment notes.
5. Run the full backend suite, frontend checks/build, Compose config validation,
   and image builds where Docker is available. Preserve all existing SMS and
   assistant behavior.

## Verification result

- Backend: 133 tests pass, including the original 116-test suite and new auth,
  CSRF, profile, privacy, Workday, channel-delivery, scheduler, and schema
  compatibility coverage.
- Frontend: TypeScript check and Vite production build pass.
- Browser: signup → questionnaire → completed dashboard passed against a
  disposable real API/SQLite process; desktop and 390px mobile layouts were
  visually inspected.
- Sources: all configured Workday endpoints returned valid payloads during the
  implementation-day probe; the actual adapter fetched all 25 currently listed
  Bain Capital postings in a live smoke test.
- Deployment: local and production Compose files pass `docker compose config`.
  Image builds could not run in this environment because the installed Docker
  CLI had no running daemon.
