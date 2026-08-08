"""One-off sanity check for the Resend integration: sends a single real email
via ResendEmailProvider to confirm RESEND_API_KEY/RESEND_FROM_EMAIL actually
work end to end (see check_keys.py for confirming they're merely *set*).

Usage:
    uv run python scripts/send_test_email.py you@example.com
"""

import asyncio
import sys

from app.notify.email_resend import ResendEmailProvider


async def main() -> None:
    if len(sys.argv) != 2:
        print("usage: send_test_email.py <to-address>")
        raise SystemExit(1)
    to = sys.argv[1]

    provider = ResendEmailProvider()
    print(f"Sending from {provider.from_email!r} to {to!r}...")
    ok = await provider.send(
        to,
        "automated_swe_jobs: Resend test",
        "This is a test email confirming the Resend integration works.",
    )
    print("sent" if ok else "FAILED — check logs above / the Resend dashboard's Logs tab")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
