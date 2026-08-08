"""Seed a demo user + criteria for local testing of the matching pipeline."""

import asyncio
from datetime import UTC, datetime

from app.db.models import Criteria, User
from app.db.session import init_db, session_scope
from app.domain.models import RoleType


async def main() -> None:
    await init_db()
    async with session_scope() as session:
        user = User(
            name="Demo User",
            phone="+15555550100",
            email="demo@example.com",
            sms_provider="signalwire",
            opted_out=False,
            consent_at=datetime.now(UTC),
            consent_method="verbal-friend-onboarding",
            created_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()

        criteria = Criteria(
            user_id=user.id,
            role_types=[RoleType.NEW_GRAD.value, RoleType.INTERN.value],
            keywords=["software", "engineer", "swe"],
            locations=["remote", "new york", "san francisco"],
            sponsorship_required=None,
            min_date=None,
            freeform_notes="Interested in backend or full-stack new-grad and intern roles.",
            updated_at=datetime.now(UTC),
        )
        session.add(criteria)
        await session.commit()
        print(f"seeded user id={user.id} phone={user.phone}")


if __name__ == "__main__":
    asyncio.run(main())
