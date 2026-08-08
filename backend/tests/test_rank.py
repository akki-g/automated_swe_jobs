from datetime import UTC, datetime

import pytest

from app.domain.models import Criteria, Posting, Priority, RoleType
from app.matching.rank import compute_priority, rank_postings


def _posting(**overrides) -> Posting:
    defaults = dict(
        posting_key="k1",
        source="test",
        company="Acme",
        title="Software Engineer New Grad",
        url="https://example.com/1",
        location="Remote",
        role_type=RoleType.NEW_GRAD,
        posted_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    defaults.update(overrides)
    return Posting(**defaults)


class FakeAnthropicClient:
    def __init__(self, tool_input: dict) -> None:
        self.tool_input = tool_input
        self.calls = 0

    async def create_message(self, *, system, messages, tools):
        self.calls += 1
        return {
            "content": [
                {"type": "tool_use", "name": tools[0]["name"], "input": self.tool_input}
            ]
        }


class FailingAnthropicClient:
    async def create_message(self, *, system, messages, tools):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_rank_postings_returns_scores_for_known_keys():
    postings = [_posting(posting_key="k1"), _posting(posting_key="k2")]
    client = FakeAnthropicClient(
        {
            "results": [
                {"posting_key": "k1", "score": 0.95, "blurb": "great fit"},
                {"posting_key": "k2", "score": 0.4, "blurb": "meh"},
            ]
        }
    )
    criteria = Criteria(user_id=1)

    results = await rank_postings(postings, criteria, client)

    assert {r.posting_key: r.score for r in results} == {"k1": 0.95, "k2": 0.4}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_rank_postings_ignores_unknown_keys():
    postings = [_posting(posting_key="k1")]
    client = FakeAnthropicClient({"results": [{"posting_key": "unknown", "score": 1.0, "blurb": ""}]})
    criteria = Criteria(user_id=1)

    results = await rank_postings(postings, criteria, client)

    assert results == []


@pytest.mark.asyncio
async def test_rank_postings_empty_input_skips_llm_call():
    client = FakeAnthropicClient({"results": []})
    criteria = Criteria(user_id=1)

    results = await rank_postings([], criteria, client)

    assert results == []
    assert client.calls == 0


@pytest.mark.asyncio
async def test_rank_postings_returns_empty_on_llm_failure():
    postings = [_posting()]
    criteria = Criteria(user_id=1)

    results = await rank_postings(postings, criteria, FailingAnthropicClient())

    assert results == []


def test_compute_priority_high_above_threshold():
    assert compute_priority(0.95) == Priority.HIGH


def test_compute_priority_normal_below_threshold():
    assert compute_priority(0.5) == Priority.NORMAL


def test_compute_priority_at_threshold_is_high():
    assert compute_priority(0.9) == Priority.HIGH


# Sponsorship is now a hard rule filter (app.matching.filters), not a
# priority-demotion signal — see tests/test_filters.py for that coverage.
