"""Confirm required secrets are present in .env without printing their values."""

from app.config import settings

CHECKS = {
    "ANTHROPIC_API_KEY": settings.anthropic_api_key,
    "SIGNALWIRE_SPACE": settings.signalwire_space,
    "SIGNALWIRE_PROJECT_ID": settings.signalwire_project_id,
    "SIGNALWIRE_API_TOKEN": settings.signalwire_api_token,
    "SIGNALWIRE_FROM_NUMBER": settings.signalwire_from_number,
    "GMAIL_ADDRESS": settings.gmail_address,
    "GMAIL_APP_PASSWORD": settings.gmail_app_password,
}


def main() -> None:
    for name, value in CHECKS.items():
        status = "set" if value else "MISSING"
        print(f"{name}: {status}")


if __name__ == "__main__":
    main()
