from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import settings
from app.domain.models import Criteria, Posting, Priority, TargetField

logger = logging.getLogger(__name__)

# Max postings scored in a single Claude call. A batch this size keeps the
# expected output (one score+blurb per posting) comfortably under max_tokens
# even for a user with a large survivor set in one cycle — without a cap, a
# big-enough batch (observed: 89 postings against a fresh/broad criteria
# backfill) hits stop_reason="max_tokens" mid-response and yields *zero*
# results for the whole batch, silently, since a truncated-but-parseable
# tool call looks like a normal "the model just didn't return anything"
# response rather than an error (see _rank_batch's truncation check for the
# other half of this fix: making that case loud instead of silent).
_RANK_BATCH_SIZE = 20

# Bounds how many chunk-scoring calls run at once, independent of the
# per-user concurrency bound in pipeline.py's _RANK_CONCURRENCY — a single
# user with a very large survivor set now fans out into multiple chunk
# calls, and this keeps that fan-out from itself becoming unbounded.
_CHUNK_CONCURRENCY = asyncio.Semaphore(5)

RANK_TOOL = {
    "name": "rank_postings",
    "description": "Score and write a one-line blurb for each candidate job posting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "posting_key": {"type": "string"},
                        "score": {
                            "type": "number",
                            "description": "Fit score from 0.0 (poor fit) to 1.0 (excellent fit).",
                        },
                        "blurb": {
                            "type": "string",
                            "description": (
                                "One sentence, SMS-length, explaining why this posting fits. When "
                                "the posting includes a description, name specific overlapping "
                                "skills/keywords between it and the seeker's criteria/resume "
                                "skills (e.g. 'Python and distributed-systems experience match "
                                "their SQL/backend focus') rather than generic praise. If no "
                                "description was given, explain fit from title/location/field."
                            ),
                        },
                        "target_field": {
                            "type": "string",
                            "description": (
                                "Which of the seeker's own target_fields (from the criteria "
                                "payload) this posting best fits. Omit if none fit well."
                            ),
                            "enum": [field.value for field in TargetField],
                        },
                    },
                    "required": ["posting_key", "score", "blurb"],
                },
            }
        },
        "required": ["results"],
    },
}


@dataclass(frozen=True)
class RankResult:
    posting_key: str
    score: float
    blurb: str
    target_field: TargetField | None = None


class AnthropicClient(Protocol):
    """The minimal surface of anthropic.Anthropic/AsyncAnthropic we depend on,
    so tests can inject a fake without an SDK dependency."""

    async def create_message(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        ...


class AnthropicMessagesClient:
    """Thin wrapper around the real Anthropic SDK, matching AnthropicClient."""

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5") -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model

    async def create_message(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice={"type": "tool", "name": tools[0]["name"]},
        )
        return response.model_dump()


def _extract_tool_input(response: dict, tool_name: str) -> dict:
    for block in response.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            return block.get("input", {})
    raise ValueError(f"no {tool_name} tool_use block in response")


def _build_prompt(postings: list[Posting], criteria: Criteria) -> tuple[str, list[dict]]:
    system = (
        "You score how well candidate job postings fit a job seeker's stated "
        "target career fields and new-graduate or internship preferences. "
        "Use the structured resume signals as supporting evidence, while "
        "treating the seeker's explicitly selected target fields and criteria "
        "as authoritative. When a posting's description is provided, ground the "
        "blurb in specific, concrete skills or requirements from that description "
        "that genuinely overlap with the seeker's keywords, resume skills, or "
        "freeform notes — real matching terms, not generic praise like 'great fit'. "
        "If no description is available for a posting, explain fit from its title, "
        "location, and target field instead. Do not infer fit from protected "
        "characteristics. Score strictly: 1.0 only for an excellent, unambiguous fit."
    )
    postings_payload = [
        {
            "posting_key": p.posting_key,
            "company": p.company,
            "title": p.title,
            "location": p.location,
            "role_type": p.role_type.value if p.role_type else None,
            "description": p.description,
        }
        for p in postings
    ]
    criteria_payload = {
        "role_types": [rt.value for rt in criteria.role_types],
        "target_fields": [field.value for field in criteria.target_fields],
        "keywords": list(criteria.keywords),
        "locations": list(criteria.locations),
        "sponsorship_required": criteria.sponsorship_required,
        "freeform_notes": criteria.freeform_notes,
        "resume_profile": criteria.resume_profile,
    }
    user_message = (
        f"Criteria: {json.dumps(criteria_payload)}\n\n"
        f"Candidate postings: {json.dumps(postings_payload)}\n\n"
        "Call rank_postings with one result per posting_key given."
    )
    return system, [{"role": "user", "content": user_message}]


async def _rank_batch(
    postings: list[Posting], criteria: Criteria, client: AnthropicClient
) -> list[RankResult]:
    """Score one chunk (<= _RANK_BATCH_SIZE postings) in a single Claude
    call."""
    system, messages = _build_prompt(postings, criteria)
    try:
        async with _CHUNK_CONCURRENCY:
            response = await client.create_message(system=system, messages=messages, tools=[RANK_TOOL])
        tool_input = _extract_tool_input(response, RANK_TOOL["name"])
    except Exception:  # noqa: BLE001 - a ranking failure must not crash the cycle
        logger.warning("rank_postings: LLM call failed for user_id=%s", criteria.user_id, exc_info=True)
        return []

    # The user's own selected fields are the only valid tags — Claude
    # occasionally free-associates a field the user never chose (or
    # hallucinates one outside the taxonomy entirely), and that must not be
    # persisted as if it were an authoritative user-selected classification.
    valid_target_fields = set(criteria.target_fields)

    valid_keys = {p.posting_key for p in postings}
    results: list[RankResult] = []
    for item in tool_input.get("results", []):
        key = item.get("posting_key")
        if key not in valid_keys:
            continue
        try:
            score = max(0.0, min(1.0, float(item["score"])))
        except (KeyError, TypeError, ValueError):
            continue

        target_field: TargetField | None = None
        raw_target_field = item.get("target_field")
        if raw_target_field is not None:
            try:
                candidate = TargetField(raw_target_field)
            except ValueError:
                candidate = None
            if candidate in valid_target_fields:
                target_field = candidate

        results.append(
            RankResult(
                posting_key=key,
                score=score,
                blurb=str(item.get("blurb", ""))[:300],
                target_field=target_field,
            )
        )

    # A response can be well-formed JSON so far as _extract_tool_input is
    # concerned and still be truncated mid-array by the token limit — that
    # shows up as stop_reason="max_tokens" with fewer (sometimes zero)
    # results than postings sent in, and nothing above would otherwise catch
    # it. Surface it loudly: a silently-truncated batch previously looked
    # identical to "the model scored everything below the threshold."
    if response.get("stop_reason") == "max_tokens":
        logger.warning(
            "rank_postings: response truncated by max_tokens for user_id=%s "
            "(%d postings sent, %d results parsed) — consider lowering _RANK_BATCH_SIZE",
            criteria.user_id,
            len(postings),
            len(results),
        )
    return results


async def rank_postings(
    postings: list[Posting], criteria: Criteria, client: AnthropicClient
) -> list[RankResult]:
    """Batch-score postings for one user's criteria (see spec: Matching —
    slow lane batches per user). Chunked into groups of at most
    _RANK_BATCH_SIZE so a large survivor set can't silently truncate a single
    huge call (see _RANK_BATCH_SIZE); chunks are scored concurrently."""
    if not postings:
        return []

    chunks = [postings[i : i + _RANK_BATCH_SIZE] for i in range(0, len(postings), _RANK_BATCH_SIZE)]
    chunk_results = await asyncio.gather(*(_rank_batch(chunk, criteria, client) for chunk in chunks))
    return [result for results in chunk_results for result in results]


def compute_priority(score: float) -> Priority:
    """See spec: Notifications — priority is a concrete, testable rule.
    Postings reaching this point have already passed matching.filters, so
    role_type/keyword/location/sponsorship requirements are already
    satisfied; only the score threshold decides high vs. normal here.
    (Callers may separately upgrade a normal-priority match to high for
    other reasons — e.g. a watchlisted company, see pipeline.match_new_postings.)
    """
    if score < settings.high_priority_score_threshold:
        return Priority.NORMAL
    return Priority.HIGH
