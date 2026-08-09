from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import settings

TAILOR_TOOL = {
    "name": "tailored_resume_content",
    "description": (
        "Produce tailored resume content, drawn only from the seeker's actual resume, "
        "rewritten to emphasize what's most relevant to one specific job posting."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A 2-3 sentence professional summary tailored to this posting.",
                "maxLength": 500,
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 20,
                "description": "Skills from the resume, ordered by relevance to this posting.",
            },
            "experience": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "organization": {"type": "string"},
                        "dates": {"type": "string"},
                        "bullets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 6,
                            "description": "Rewritten bullets emphasizing relevance to this posting.",
                        },
                    },
                    "required": ["title", "organization", "bullets"],
                },
            },
            "education": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "school": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["school"],
                },
            },
        },
        "required": ["summary", "skills", "experience", "education"],
    },
}


@dataclass(frozen=True)
class ExperienceEntry:
    title: str
    organization: str
    dates: str
    bullets: tuple[str, ...]


@dataclass(frozen=True)
class EducationEntry:
    school: str
    detail: str


@dataclass(frozen=True)
class TailoredResumeContent:
    summary: str
    skills: tuple[str, ...]
    experience: tuple[ExperienceEntry, ...]
    education: tuple[EducationEntry, ...]


class TailorClient(Protocol):
    async def create_message(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        ...


class ClaudeTailorClient:
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
            tool_choice={"type": "tool", "name": TAILOR_TOOL["name"]},
        )
        return response.model_dump()


def _tool_input(response: dict) -> dict:
    for block in response.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == TAILOR_TOOL["name"]:
            value = block.get("input")
            if isinstance(value, dict):
                return value
    raise ValueError("Claude did not return tailored resume content")


def _clean_str(value: object, *, length: int) -> str:
    return str(value).strip()[:length] if value is not None else ""


def _clean_strings(value: object, *, count: int, length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value[:count]:
        item = _clean_str(raw, length=length)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def normalize_tailored_content(payload: dict) -> TailoredResumeContent:
    experience: list[ExperienceEntry] = []
    for entry in (payload.get("experience") or [])[:6]:
        if not isinstance(entry, dict):
            continue
        title = _clean_str(entry.get("title"), length=150)
        organization = _clean_str(entry.get("organization"), length=150)
        if not title or not organization:
            continue
        experience.append(
            ExperienceEntry(
                title=title,
                organization=organization,
                dates=_clean_str(entry.get("dates"), length=60),
                bullets=tuple(_clean_strings(entry.get("bullets"), count=6, length=250)),
            )
        )

    education: list[EducationEntry] = []
    for entry in (payload.get("education") or [])[:4]:
        if not isinstance(entry, dict):
            continue
        school = _clean_str(entry.get("school"), length=150)
        if not school:
            continue
        education.append(EducationEntry(school=school, detail=_clean_str(entry.get("detail"), length=200)))

    return TailoredResumeContent(
        summary=_clean_str(payload.get("summary"), length=500),
        skills=tuple(_clean_strings(payload.get("skills"), count=20, length=60)),
        experience=tuple(experience),
        education=tuple(education),
    )


async def tailor_resume_content(
    resume_text: str,
    *,
    company: str,
    title: str,
    location: str | None,
    client: TailorClient,
) -> TailoredResumeContent:
    """Draft tailored resume content for one specific posting, from resume
    text that is transient to this request (never persisted — see spec:
    Resume tailoring)."""
    system = (
        "You rewrite resume content to emphasize what's most relevant to one specific job "
        "posting. Use only experience, skills, and education actually present in the supplied "
        "resume text — never invent employers, titles, dates, or accomplishments. Do not infer "
        "or mention protected characteristics."
    )
    message = (
        f"Target posting: {title} at {company}"
        + (f" ({location})" if location else "")
        + "\n\nResume text (transient; do not reproduce verbatim contact details):\n"
        + resume_text
    )
    response = await client.create_message(
        system=system,
        messages=[{"role": "user", "content": message}],
        tools=[TAILOR_TOOL],
    )
    return normalize_tailored_content(_tool_input(response))
