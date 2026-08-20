from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The documented layout (README: `cp ../.env.example ../.env`) keeps .env at
# the repo root, one level above backend/ — but the app is normally run with
# backend/ as cwd (`cd backend && uv run ...`), and pydantic-settings' relative
# env_file is resolved against cwd, not this file's location. Without an
# explicit path, that mismatch meant .env was silently never read (empty
# defaults, no error) unless a caller happened to run from the repo root.
# Listing both paths lets either layout work; a repo-root .env still wins if
# both existed, since later entries override earlier ones.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"
DEV_AUTH_SECRET = "dev-only-change-me-at-least-32-bytes"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", _REPO_ROOT_ENV), extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"

    auth_secret: str = DEV_AUTH_SECRET
    auth_session_hours: int = 24 * 7
    auth_cookie_secure: bool = False
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # The user-facing web app's own URL (not an API origin) — used to link
    # back to the matches page from notification emails.
    frontend_app_url: str = "http://localhost:5173"

    anthropic_api_key: str = ""

    signalwire_space: str = ""
    signalwire_project_id: str = ""
    signalwire_api_token: str = ""
    signalwire_from_number: str = ""
    signalwire_allow_unsigned_webhooks: bool = False

    telnyx_api_key: str = ""
    telnyx_from_number: str = ""

    resend_api_key: str = ""
    resend_from_email: str = ""

    # Publishable (client-exposed-by-design) Supabase key pagesxyz.com's own
    # frontend uses against its jobs endpoint — see app/sources/aggregators.py.
    pagesxyz_api_key: str = ""

    fast_lane_interval_minutes: int = 2
    slow_lane_interval_minutes: int = 15
    profile_backfill_interval_minutes: int = 1
    profile_backfill_max_users_per_cycle: int = 5
    profile_backfill_max_postings_per_user: int = 300
    digest_hours: str = "8,20"  # comma-separated hours (local time) for the digest cron
    daily_email_hour: int = 8
    scheduler_timezone: str = "America/New_York"
    stale_after_cycles: int = 4
    digest_max_sms_matches: int = 8
    digest_max_email_matches: int = 15
    # A ranked survivor scoring below this never becomes a stored `matches`
    # row at all (see pipeline.match_new_postings) — without this gate,
    # every rule-filter survivor that got ranked at all (even a 0.05, an
    # obviously poor fit) was persisted as a "match" and could reach a
    # digest, which is what actually made early digests feel irrelevant.
    min_match_score: float = 0.55
    # Per-company cap applied when curating a digest (see
    # notify/dispatch.py::_curate_matches) — a single prolific company
    # posting many similar roles must not crowd out every other company's
    # matches in one email/SMS digest.
    digest_max_per_company: int = 2
    # The email digest's "Just Dropped" section (matches never emailed
    # before) is capped independent of the overall cap — the rest of
    # digest_max_email_matches is filled by "For You" (matches a previous
    # email already showed, still open/relevant). See notify/curate.py.
    digest_max_just_dropped: int = 5
    # How far back a previously-emailed match can still surface in "For
    # You" — bounds that section from resurfacing arbitrarily old matches
    # forever as a user's history grows.
    digest_for_you_max_age_days: int = 14
    high_priority_score_threshold: float = 0.9

    @property
    def frontend_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.frontend_origins.split(",")
            if origin.strip()
        ]


settings = Settings()
