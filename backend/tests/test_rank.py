import json
from collections import Counter
from datetime import UTC, datetime

import pytest

from app.domain.models import Criteria, Posting, Priority, RoleType, TargetField
from app.matching.rank import (
    _MAX_EXPECTED_RESULT_TOKENS,
    _RANK_BATCH_SIZE,
    _RANK_MAX_TOKENS,
    _build_prompt,
    compute_priority,
    rank_postings,
)


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


class ChunkCountingClient:
    """Returns a full score/blurb for every posting_key actually sent in
    each call, and records how many postings were in each call — lets tests
    assert on chunk boundaries without depending on _RANK_BATCH_SIZE's exact
    value."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    async def create_message(self, *, system, messages, tools):
        payload = json.loads(messages[0]["content"].split("Candidate postings: ", 1)[1].split("\n\n")[0])
        self.batch_sizes.append(len(payload))
        results = [{"posting_key": p["posting_key"], "score": 0.5, "blurb": "ok"} for p in payload]
        return {"content": [{"type": "tool_use", "name": tools[0]["name"], "input": {"results": results}}]}


class TruncatedAnthropicClient:
    """Simulates a response cut off by max_tokens mid-array: a well-formed
    tool_use block with fewer (here, zero) results than postings sent."""

    async def create_message(self, *, system, messages, tools):
        return {
            "stop_reason": "max_tokens",
            "content": [{"type": "tool_use", "name": tools[0]["name"], "input": {"results": []}}],
        }


class BisectingTruncationClient:
    """Truncates (stop_reason=max_tokens, zero results) any call sent more
    than one posting; scores normally otherwise. Proves a truncated batch
    gets retried as smaller sub-batches instead of being dropped to zero."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    async def create_message(self, *, system, messages, tools):
        payload = json.loads(messages[0]["content"].split("Candidate postings: ", 1)[1].split("\n\n")[0])
        self.batch_sizes.append(len(payload))
        if len(payload) > 1:
            return {
                "stop_reason": "max_tokens",
                "content": [{"type": "tool_use", "name": tools[0]["name"], "input": {"results": []}}],
            }
        results = [{"posting_key": p["posting_key"], "score": 0.5, "blurb": "ok"} for p in payload]
        return {"content": [{"type": "tool_use", "name": tools[0]["name"], "input": {"results": results}}]}


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


@pytest.mark.asyncio
async def test_rank_postings_chunks_large_batches():
    """Regression for the silent-truncation bug: a survivor set larger than
    _RANK_BATCH_SIZE must be split across multiple calls, each within the
    cap, rather than sent as one oversized call that risks stop_reason ==
    'max_tokens' truncating the whole batch to zero results (see
    test_rank_postings_reports_but_does_not_crash_on_truncated_response for
    the truncation-visibility half of this fix)."""
    postings = [_posting(posting_key=f"k{i}") for i in range(_RANK_BATCH_SIZE + 5)]
    client = ChunkCountingClient()
    criteria = Criteria(user_id=1)

    results = await rank_postings(postings, criteria, client)

    assert len(results) == len(postings)
    assert all(size <= _RANK_BATCH_SIZE for size in client.batch_sizes)
    assert sum(client.batch_sizes) == len(postings)
    assert len(client.batch_sizes) > 1  # actually split into more than one call


@pytest.mark.asyncio
async def test_rank_postings_reports_but_does_not_crash_on_truncated_response(caplog):
    """A response truncated by max_tokens must not silently look identical
    to 'nothing scored above zero' — it should be logged so an operator can
    tell the difference from a genuinely empty result."""
    postings = [_posting(posting_key="k1")]
    criteria = Criteria(user_id=1)

    with caplog.at_level("WARNING"):
        results = await rank_postings(postings, criteria, TruncatedAnthropicClient())

    assert results == []
    assert any("truncated" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_truncated_batch_is_retried_in_smaller_pieces():
    """Regression for new users getting zero matches: a full-size chunk whose
    response is cut off by max_tokens must be re-scored as smaller sub-batches
    rather than dropped wholesale. Dropping it lost every posting in the chunk,
    and since a profile backfill rebuilds the identical chunk next cycle, the
    user stayed at zero matches indefinitely."""
    postings = [_posting(posting_key=f"k{i}") for i in range(8)]
    client = BisectingTruncationClient()
    criteria = Criteria(user_id=1)

    results = await rank_postings(postings, criteria, client)

    assert {r.posting_key for r in results} == {p.posting_key for p in postings}


@pytest.mark.asyncio
async def test_truncation_retry_gives_up_on_a_single_posting():
    """A lone posting that still truncates cannot be split further — it must
    return empty rather than recurse forever."""
    postings = [_posting(posting_key="k1")]
    criteria = Criteria(user_id=1)

    results = await rank_postings(postings, criteria, TruncatedAnthropicClient())

    assert results == []


@pytest.mark.asyncio
async def test_truncation_retry_does_not_duplicate_scored_postings():
    """Splitting must partition the chunk, not overlap it — a duplicated
    posting_key would violate the unique (user_id, posting_id) match
    constraint downstream in pipeline.match_new_postings."""
    postings = [_posting(posting_key=f"k{i}") for i in range(8)]
    criteria = Criteria(user_id=1)

    results = await rank_postings(postings, criteria, BisectingTruncationClient())

    assert Counter(r.posting_key for r in results).most_common(1)[0][1] == 1


def test_rank_max_tokens_fits_a_full_batch_of_worst_case_results():
    """The output budget must cover a full _RANK_BATCH_SIZE batch where every
    result is as long as the schema allows. Violating this is what made new
    users get zero matches: every full batch truncated, and each retry cycle
    rebuilt the same oversized request. Raising _RANK_BATCH_SIZE without
    raising _RANK_MAX_TOKENS (or vice versa) must fail here."""
    assert _RANK_MAX_TOKENS >= _RANK_BATCH_SIZE * _MAX_EXPECTED_RESULT_TOKENS


def test_compute_priority_high_above_threshold():
    assert compute_priority(0.95) == Priority.HIGH


def test_compute_priority_normal_below_threshold():
    assert compute_priority(0.5) == Priority.NORMAL


def test_compute_priority_at_threshold_is_high():
    assert compute_priority(0.9) == Priority.HIGH


def test_ranking_prompt_uses_target_fields_and_structured_resume_signals():
    criteria = Criteria(
        user_id=1,
        target_fields=(TargetField.CONSULTING,),
        resume_profile={"skills": ["market sizing"], "experience_level": "student"},
    )

    system, messages = _build_prompt([_posting()], criteria)

    assert "software engineering" not in system
    assert "target career fields" in system
    assert '"target_fields": ["consulting"]' in messages[0]["content"]
    assert '"skills": ["market sizing"]' in messages[0]["content"]


def test_ranking_prompt_includes_posting_description_and_instructs_citing_matches():
    posting = _posting(description="Requires Python, SQL, and distributed systems experience.")
    criteria = Criteria(user_id=1, keywords=("Python",))

    system, messages = _build_prompt([posting], criteria)

    assert "genuinely overlap" in system
    assert "Requires Python, SQL, and distributed systems experience." in messages[0]["content"]


def test_ranking_prompt_sends_null_description_when_source_has_none():
    posting = _posting(description=None)

    _system, messages = _build_prompt([posting], Criteria(user_id=1))

    assert '"description": null' in messages[0]["content"]


@pytest.mark.asyncio
async def test_rank_postings_persists_valid_target_field_tag():
    postings = [_posting(posting_key="k1")]
    client = FakeAnthropicClient(
        {
            "results": [
                {
                    "posting_key": "k1",
                    "score": 0.8,
                    "blurb": "good fit",
                    "target_field": "consulting",
                }
            ]
        }
    )
    criteria = Criteria(user_id=1, target_fields=(TargetField.CONSULTING, TargetField.MARKETING))

    results = await rank_postings(postings, criteria, client)

    assert results[0].target_field == TargetField.CONSULTING


@pytest.mark.asyncio
async def test_rank_postings_drops_target_field_not_in_users_selection():
    """Claude occasionally free-associates a field the user never selected —
    that must not be persisted as if it were an authoritative user choice."""
    postings = [_posting(posting_key="k1")]
    client = FakeAnthropicClient(
        {
            "results": [
                {
                    "posting_key": "k1",
                    "score": 0.8,
                    "blurb": "good fit",
                    "target_field": "design",  # not in criteria.target_fields below
                }
            ]
        }
    )
    criteria = Criteria(user_id=1, target_fields=(TargetField.CONSULTING,))

    results = await rank_postings(postings, criteria, client)

    assert results[0].target_field is None


@pytest.mark.asyncio
async def test_rank_postings_target_field_defaults_to_none_when_absent():
    postings = [_posting(posting_key="k1")]
    client = FakeAnthropicClient(
        {"results": [{"posting_key": "k1", "score": 0.8, "blurb": "good fit"}]}
    )
    criteria = Criteria(user_id=1, target_fields=(TargetField.CONSULTING,))

    results = await rank_postings(postings, criteria, client)

    assert results[0].target_field is None


# Sponsorship is now a hard rule filter (app.matching.filters), not a
# priority-demotion signal — see tests/test_filters.py for that coverage.
