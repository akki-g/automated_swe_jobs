from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Protocol

from app.config import settings
from app.domain.models import TargetField

RESUME_TOOL = {
    "name": "extract_resume_profile",
    "description": "Extract compact job-matching signals from a resume.",
    "input_schema": {
        "type": "object",
        "properties": {
            "skills": {"type": "array", "items": {"type": "string"}, "maxItems": 50},
            "past_titles": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
            "experience_level": {
                "type": "string",
                "enum": ["student", "entry_level", "early_career", "experienced", "unknown"],
            },
            "years_experience": {"type": ["number", "null"], "minimum": 0, "maximum": 60},
            "inferred_target_fields": {
                "type": "array",
                "items": {"type": "string", "enum": [field.value for field in TargetField]},
                "maxItems": 9,
            },
            "education_fields": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "summary": {
                "type": "string",
                "description": "At most two concise sentences containing only job-matching signals.",
                "maxLength": 500,
            },
        },
        "required": [
            "skills",
            "past_titles",
            "experience_level",
            "years_experience",
            "inferred_target_fields",
            "education_fields",
            "summary",
        ],
    },
}


@dataclass(frozen=True)
class ResumeProfile:
    skills: list[str]
    past_titles: list[str]
    experience_level: str
    years_experience: float | None
    inferred_target_fields: list[str]
    education_fields: list[str]
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)


class ResumeExtractionClient(Protocol):
    async def create_message(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        ...


class ClaudeResumeClient:
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5") -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model

    async def create_message(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1200,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice={"type": "tool", "name": RESUME_TOOL["name"]},
        )
        return response.model_dump()


def _tool_input(response: dict) -> dict:
    for block in response.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == RESUME_TOOL["name"]:
            value = block.get("input")
            if isinstance(value, dict):
                return value
    raise ValueError("Claude did not return structured resume signals")


def _clean_strings(value: object, *, count: int, length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value[:count]:
        item = str(raw).strip()[:length]
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def normalize_resume_profile(payload: dict) -> ResumeProfile:
    levels = {"student", "entry_level", "early_career", "experienced", "unknown"}
    level = str(payload.get("experience_level", "unknown"))
    if level not in levels:
        level = "unknown"

    years: float | None = None
    if payload.get("years_experience") is not None:
        try:
            years = max(0.0, min(60.0, float(payload["years_experience"])))
        except (TypeError, ValueError):
            years = None

    valid_fields = {field.value for field in TargetField}
    inferred_fields = [
        value
        for value in _clean_strings(payload.get("inferred_target_fields"), count=9, length=80)
        if value in valid_fields
    ]
    return ResumeProfile(
        skills=_clean_strings(payload.get("skills"), count=50, length=80),
        past_titles=_clean_strings(payload.get("past_titles"), count=12, length=120),
        experience_level=level,
        years_experience=years,
        inferred_target_fields=inferred_fields,
        education_fields=_clean_strings(payload.get("education_fields"), count=8, length=120),
        summary=str(payload.get("summary", "")).strip()[:500],
    )


async def extract_resume_profile(text: str, client: ResumeExtractionClient) -> ResumeProfile:
    system = (
        "Extract only compact, job-relevant matching signals from the supplied resume. "
        "Do not reproduce contact details, addresses, full work descriptions, or the resume text. "
        "Do not infer protected characteristics. Use only the provided target-field taxonomy."
    )
    message = (
        "Target-field taxonomy: "
        f"{json.dumps([field.value for field in TargetField])}\n\n"
        "Resume text (transient; do not reproduce it):\n"
        f"{text}"
    )
    response = await client.create_message(
        system=system,
        messages=[{"role": "user", "content": message}],
        tools=[RESUME_TOOL],
    )
    return normalize_resume_profile(_tool_input(response))
