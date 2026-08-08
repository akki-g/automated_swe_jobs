from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"

    anthropic_api_key: str = ""

    signalwire_space: str = ""
    signalwire_project_id: str = ""
    signalwire_api_token: str = ""
    signalwire_from_number: str = ""
    signalwire_allow_unsigned_webhooks: bool = False

    telnyx_api_key: str = ""
    telnyx_from_number: str = ""

    gmail_address: str = ""
    gmail_app_password: str = ""

    fast_lane_interval_minutes: int = 2
    slow_lane_interval_minutes: int = 15
    digest_hours: str = "8,20"  # comma-separated hours (local time) for the digest cron
    stale_after_cycles: int = 4
    digest_max_sms_matches: int = 8
    high_priority_score_threshold: float = 0.9


settings = Settings()
