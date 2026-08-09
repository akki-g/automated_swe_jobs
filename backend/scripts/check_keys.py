"""Confirm required secrets are present in .env without printing their values."""

from app.config import DEV_AUTH_SECRET, settings

CHECKS = {
    "AUTH_SECRET": settings.auth_secret if settings.auth_secret != DEV_AUTH_SECRET else "",
    "ANTHROPIC_API_KEY": settings.anthropic_api_key,
    "SIGNALWIRE_SPACE": settings.signalwire_space,
    "SIGNALWIRE_PROJECT_ID": settings.signalwire_project_id,
    "SIGNALWIRE_API_TOKEN": settings.signalwire_api_token,
    "SIGNALWIRE_FROM_NUMBER": settings.signalwire_from_number,
    "RESEND_API_KEY": settings.resend_api_key,
    "RESEND_FROM_EMAIL": settings.resend_from_email,
    "PAGESXYZ_API_KEY": settings.pagesxyz_api_key,
}


def main() -> None:
    for name, value in CHECKS.items():
        status = "set" if value else "MISSING"
        print(f"{name}: {status}")


if __name__ == "__main__":
    main()
