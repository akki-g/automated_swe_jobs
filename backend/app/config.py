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

    fast_lane_interval_minutes: int = 2
    slow_lane_interval_minutes: int = 15
    digest_hours: str = "8,20"  # comma-separated hours (local time) for the digest cron
    daily_email_hour: int = 8
    scheduler_timezone: str = "America/New_York"
    stale_after_cycles: int = 4
    digest_max_sms_matches: int = 8
    high_priority_score_threshold: float = 0.9

    @property
    def frontend_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]


settings = Settings()
