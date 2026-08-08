"""One-off: scrape real sources, rank against a given user's criteria with
the real Anthropic client, and email the *real* result set via Resend —
end-to-end proof the pipeline + Resend wiring produce a real, useful digest,
not just a canned test message (see send_test_email.py for that simpler
"is Resend reachable at all" check).

Uses (and creates if missing) a real users/criteria row for the given email
so this exercises the actual matching path, not a shortcut around it.

Usage:
    uv run python scripts/send_real_digest_email.py you@example.com
"""

import asyncio
import sys

from sqlalchemy import select

from app.config import settings
from app.db.models import Criteria as CriteriaRow
from app.db.models import User
from app.db.session import init_db, session_scope
from app.domain.models import RoleType
from app.matching.rank import AnthropicMessagesClient
from app.notify.dispatch import _format_email_digest
from app.notify.email_resend import ResendEmailProvider
from app.pipeline import match_new_postings, run_sources, store_new_postings
from app.scheduler import default_sources


async def _get_or_create_user(session, email: str) -> User:
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        return existing

    from datetime import UTC, datetime

    user = User(
        name="Digest Test",
        phone="+15555550199",
        email=email,
        sms_provider="signalwire",
        opted_out=False,
        consent_at=datetime.now(UTC),
        consent_method="verbal-friend-onboarding",
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    session.add(
        CriteriaRow(
            user_id=user.id,
            role_types=[RoleType.NEW_GRAD.value, RoleType.INTERN.value],
            keywords=["software", "engineer", "swe"],
            locations=[],
            sponsorship_required=None,
            min_date=None,
            freeform_notes="New-grad and internship SWE roles, any location.",
            updated_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return user


async def main() -> None:
    if len(sys.argv) != 2:
        print("usage: send_real_digest_email.py <to-email>")
        raise SystemExit(1)
    to_email = sys.argv[1]

    await init_db()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY not set — can't rank postings")
        raise SystemExit(1)

    rank_client = AnthropicMessagesClient()

    async with session_scope() as session:
        user = await _get_or_create_user(session, to_email)
        await session.commit()

        sources = await default_sources(session)
        postings = await run_sources(sources)
        print(f"fetched {len(postings)} deduped postings")

        new_rows = await store_new_postings(session, postings)
        print(f"{len(new_rows)} new since last run")

        result = await match_new_postings(session, new_rows, rank_client)
        await session.commit()

        items = [(match_row, posting_row) for _u, match_row, posting_row in result.instant if _u.id == user.id]
        for digest_item in result.digest_items:
            if digest_item.user.id == user.id:
                items.extend(digest_item.matches)

    if not items:
        print("no matches for this user this run — nothing to email (try again once more postings accumulate)")
        raise SystemExit(0)

    items.sort(key=lambda pair: pair[0].score, reverse=True)
    print(f"emailing {len(items)} match(es) to {to_email}")

    provider = ResendEmailProvider()
    ok = await provider.send(to_email, "New new-grad / SWE postings", _format_email_digest(items))
    print("sent" if ok else "FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
