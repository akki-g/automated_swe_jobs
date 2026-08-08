from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import settings
from app.domain.models import Criteria, Posting, Priority

logger = logging.getLogger(__name__)

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
                            "description": "One sentence, SMS-length, explaining why this posting fits.",
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
        "criteria for new-grad software engineering or internship roles. "
        "Score strictly: 1.0 only for an excellent, unambiguous fit."
    )
    postings_payload = [
        {
            "posting_key": p.posting_key,
            "company": p.company,
            "title": p.title,
            "location": p.location,
            "role_type": p.role_type.value if p.role_type else None,
        }
        for p in postings
    ]
    criteria_payload = {
        "role_types": [rt.value for rt in criteria.role_types],
        "keywords": list(criteria.keywords),
        "locations": list(criteria.locations),
        "sponsorship_required": criteria.sponsorship_required,
        "freeform_notes": criteria.freeform_notes,
    }
    user_message = (
        f"Criteria: {json.dumps(criteria_payload)}\n\n"
        f"Candidate postings: {json.dumps(postings_payload)}\n\n"
        "Call rank_postings with one result per posting_key given."
    )
    return system, [{"role": "user", "content": user_message}]


async def rank_postings(
    postings: list[Posting], criteria: Criteria, client: AnthropicClient
) -> list[RankResult]:
    """Batch-score postings for one user's criteria in a single Claude call
    (see spec: Matching — slow lane batches per user)."""
    if not postings:
        return []

    system, messages = _build_prompt(postings, criteria)
    try:
        response = await client.create_message(system=system, messages=messages, tools=[RANK_TOOL])
        tool_input = _extract_tool_input(response, RANK_TOOL["name"])
    except Exception:  # noqa: BLE001 - a ranking failure must not crash the cycle
        logger.warning("rank_postings: LLM call failed for user_id=%s", criteria.user_id, exc_info=True)
        return []

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
        results.append(RankResult(posting_key=key, score=score, blurb=str(item.get("blurb", ""))[:300]))
    return results


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
