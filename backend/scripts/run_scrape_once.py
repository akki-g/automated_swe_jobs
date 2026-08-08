"""Manual end-to-end dry-run: scrape all sources, rank/match against stored
criteria, and print the results — no sends, no DB writes beyond new postings
and matches (see spec: Testing)."""

import asyncio

from app.config import settings
from app.db.session import init_db, session_scope
from app.matching.rank import AnthropicMessagesClient
from app.pipeline import mark_stale_postings, match_new_postings, run_sources, store_new_postings
from app.scheduler import default_sources


async def main() -> None:
    await init_db()
    rank_client = AnthropicMessagesClient() if settings.anthropic_api_key else None

    async with session_scope() as session:
        sources = await default_sources(session)
        postings = await run_sources(sources)
        print(f"fetched {len(postings)} deduped postings this run")

        new_rows = await store_new_postings(session, postings)
        print(f"{len(new_rows)} are new since last run")

        stale_count = await mark_stale_postings(session)
        print(f"{stale_count} postings marked stale")

        if rank_client is None:
            print("ANTHROPIC_API_KEY not set — skipping LLM ranking/matching")
            await session.commit()
            return

        result = await match_new_postings(session, new_rows, rank_client)
        await session.commit()

        print(f"\n{len(result.instant)} instant (high-priority) match(es):")
        for user, match_row, posting_row in result.instant:
            print(f"  - {user.name}: {posting_row.company} {posting_row.title} (score={match_row.score:.2f})")

        print(f"\n{len(result.digest_items)} user(s) with digest matches:")
        for item in result.digest_items:
            print(f"  {item.user.name} ({item.user.phone}): {len(item.matches)} match(es)")
            for match_row, posting_row in item.matches:
                print(
                    f"    - {posting_row.company}: {posting_row.title} "
                    f"(score={match_row.score:.2f}) {posting_row.url}"
                )


if __name__ == "__main__":
    asyncio.run(main())
