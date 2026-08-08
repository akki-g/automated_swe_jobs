"""One-off sanity check for the SignalWire integration: sends a single real
SMS via SignalWireSmsProvider to confirm SIGNALWIRE_SPACE/PROJECT_ID/API_TOKEN/
FROM_NUMBER actually work end to end (see check_keys.py for confirming
they're merely *set*).

This only exercises outbound sending — it does not depend on the inbound
webhook being reachable (see app/webhooks.py for that separate path).

Usage:
    uv run python scripts/send_test_sms.py +1XXXXXXXXXX
"""

import asyncio
import sys

from app.notify.sms.signalwire import SignalWireSmsProvider


async def main() -> None:
    if len(sys.argv) != 2:
        print("usage: send_test_sms.py <to-number-E.164, e.g. +15551234567>")
        raise SystemExit(1)
    to = sys.argv[1]

    provider = SignalWireSmsProvider()
    print(f"Sending from {provider.from_number!r} to {to!r}...")
    result = await provider.send(to, "automated_swe_jobs: SignalWire test — outbound SMS works.")
    if result.success:
        print("sent")
        raise SystemExit(0)
    print(f"FAILED — provider={result.provider} ambiguous={result.ambiguous} error={result.error}")
    raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
