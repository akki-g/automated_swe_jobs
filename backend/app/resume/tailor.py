from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)

PARSER_MODEL = "claude-haiku-4-5-20251001"
WRITER_MODEL = "claude-sonnet-5"

_ENTRY_PROPERTIES = {
    "title": {"type": "string"},
    "subtitle": {"type": "string"},
    "location": {"type": "string"},
    "dates": {"type": "string"},
    "body": {"type": "string"},
    "bullets": {"type": "array", "items": {"type": "string"}},
}
_ENTRY_SCHEMA = {
    "type": "object",
    "properties": _ENTRY_PROPERTIES,
    "required": list(_ENTRY_PROPERTIES),
    "additionalProperties": False,
}
_CONTACT_PROPERTIES = {
    "email": {"type": "string"},
    "phone": {"type": "string"},
    "location": {"type": "string"},
    "links": {"type": "array", "items": {"type": "string"}},
}
_CONTACT_SCHEMA = {
    "type": "object",
    "properties": _CONTACT_PROPERTIES,
    "required": list(_CONTACT_PROPERTIES),
    "additionalProperties": False,
}

# The extraction pass is intentionally section-generic. A fixed Experience /
# Education / Skills schema caused projects, publications, research, awards,
# coursework, and custom headings to disappear before the writing pass.
PARSED_RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "contact": _CONTACT_SCHEMA,
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "kind": {"type": "string"},
                    "raw_text": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                    "entries": {"type": "array", "items": _ENTRY_SCHEMA},
                },
                "required": ["heading", "kind", "raw_text", "items", "entries"],
                "additionalProperties": False,
            },
        },
        "unassigned_text": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "contact", "sections", "unassigned_text"],
    "additionalProperties": False,
}

_TAILORED_SECTION_PROPERTIES = {
    "heading": {"type": "string"},
    "kind": {"type": "string"},
    "intro": {"type": "string"},
    "items": {"type": "array", "items": {"type": "string"}},
    "entries": {"type": "array", "items": _ENTRY_SCHEMA},
}
TAILORED_RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "contact": _CONTACT_SCHEMA,
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _TAILORED_SECTION_PROPERTIES,
                "required": list(_TAILORED_SECTION_PROPERTIES),
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "contact", "sections"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ResumeEntry:
    title: str
    subtitle: str
    location: str
    dates: str
    body: str
    bullets: tuple[str, ...]


@dataclass(frozen=True)
class ResumeSection:
    heading: str
    kind: str
    intro: str
    items: tuple[str, ...]
    entries: tuple[ResumeEntry, ...]


@dataclass(frozen=True)
class TailoredResumeContent:
    headline: str
    contact_email: str
    contact_phone: str
    contact_location: str
    contact_links: tuple[str, ...]
    sections: tuple[ResumeSection, ...]


class TailorClient(Protocol):
    async def parse_resume(self, resume_text: str) -> dict:
        ...

    async def rewrite_resume(
        self,
        parsed_resume: dict,
        *,
        company: str,
        title: str,
        location: str | None,
        description: str | None,
        job_url: str | None,
    ) -> dict:
        ...


def _output_config(schema: dict) -> dict:
    return {"format": {"type": "json_schema", "schema": schema}}


def _response_text(response: object) -> str:
    content = getattr(response, "content", [])
    parts = [getattr(block, "text", "") for block in content if getattr(block, "type", "") == "text"]
    return "\n".join(part for part in parts if part).strip()


def _response_json(response: object) -> dict:
    text = _response_text(response)
    if not text:
        raise ValueError("Claude returned no structured resume content")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Claude returned an invalid structured resume")
    return value


class ClaudeTailorClient:
    def __init__(
        self,
        api_key: str | None = None,
        parser_model: str = PARSER_MODEL,
        writer_model: str = WRITER_MODEL,
    ) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        self._parser_model = parser_model
        self._writer_model = writer_model

    async def parse_resume(self, resume_text: str) -> dict:
        response = await self._client.messages.create(
            model=self._parser_model,
            max_tokens=10_000,
            system=(
                "You are a lossless resume parser. Identify every section in its original order, "
                "including custom sections such as projects, research, publications, leadership, "
                "awards, certifications, coursework, volunteering, and interests. Preserve every "
                "factual detail, metric, technology, date, link, and bullet. Put the complete text "
                "for each detected section in raw_text even when you also split it into entries. "
                "Do not rewrite, summarize, rank, or omit content. Put text that cannot be assigned "
                "confidently in unassigned_text. Use an empty string or list for inapplicable fields."
            ),
            messages=[
                {
                    "role": "user",
                    "content": "Parse this resume into the supplied schema without losing content:\n\n"
                    + resume_text,
                }
            ],
            output_config=_output_config(PARSED_RESUME_SCHEMA),
            timeout=75,
        )
        return _response_json(response)

    async def _research_posting(
        self,
        *,
        company: str,
        title: str,
        location: str | None,
        description: str | None,
        job_url: str | None,
    ) -> str:
        prompt = (
            f"Research the {title} role at {company}."
            + (f" Location: {location}." if location else "")
            + (f" Posting URL: {job_url}." if job_url else "")
            + (f"\nStored posting excerpt:\n{description}" if description else "")
            + "\nUse the exact posting URL when accessible and search official company sources for "
            "additional role/team context. Return a concise evidence-based brief covering required "
            "skills, repeated keywords, responsibilities, engineering values, and product/team context. "
            "Distinguish explicit requirements from inferred context. Do not make claims about the candidate."
        )
        try:
            response = await self._client.messages.create(
                model=self._writer_model,
                max_tokens=3_000,
                system=(
                    "You research a job before a resume rewrite. Prefer the supplied posting and official "
                    "company sources. Search only for context that can improve keyword selection and factual "
                    "role alignment; never use the web to add facts to the candidate's history."
                ),
                messages=[{"role": "user", "content": prompt}],
                tools=[
                    {"type": "web_search_20250305", "name": "web_search", "max_uses": 2},
                    {
                        "type": "web_fetch_20250910",
                        "name": "web_fetch",
                        "max_uses": 2,
                        "max_content_tokens": 12_000,
                    },
                ],
                timeout=90,
            )
            return _response_text(response)
        except Exception:  # noqa: BLE001 - the supplied posting remains a valid fallback
            logger.warning("resume tailoring: web research unavailable; using stored posting context", exc_info=True)
            return "Web research was unavailable. Use only the supplied posting metadata and excerpt."

    async def rewrite_resume(
        self,
        parsed_resume: dict,
        *,
        company: str,
        title: str,
        location: str | None,
        description: str | None,
        job_url: str | None,
    ) -> dict:
        research = await self._research_posting(
            company=company,
            title=title,
            location=location,
            description=description,
            job_url=job_url,
        )
        job_context = {
            "company": company,
            "title": title,
            "location": location or "",
            "url": job_url or "",
            "stored_description": description or "",
            "web_research": research,
        }
        response = await self._client.messages.create(
            model=self._writer_model,
            max_tokens=12_000,
            system=(
                "You are an expert technical resume writer. Rewrite the parsed resume for the target job "
                "using the job context and research. Preserve every original section and its order, including "
                "projects, research, publications, awards, certifications, coursework, or custom sections. "
                "Preserve all employers, schools, titles, dates, metrics, technologies, links, and factual "
                "claims. Never invent or upgrade experience. You may reorder bullets within a section, combine "
                "true overlapping statements, and rewrite for clarity, impact, and ATS keyword alignment. "
                "Use exact job keywords only where the source resume genuinely supports them. Make bullets "
                "specific, action-led, and natural; avoid meta-commentary such as 'relevant to this role', "
                "'showcasing', or 'demonstrating'. Keep approximately the source resume's information density "
                "instead of reducing it to a sparse one-page summary. Prefer one page when the source is close "
                "to one page; otherwise produce enough substantive material for two useful pages and avoid a "
                "nearly empty overflow page. Return only the requested JSON."
            ),
            messages=[
                {
                    "role": "user",
                    "content": "TARGET JOB CONTEXT\n"
                    + json.dumps(job_context, ensure_ascii=False, indent=2)
                    + "\n\nLOSSLESS PARSED RESUME\n"
                    + json.dumps(parsed_resume, ensure_ascii=False, indent=2),
                }
            ],
            output_config=_output_config(TAILORED_RESUME_SCHEMA),
            timeout=90,
        )
        return _response_json(response)


_WHITESPACE = re.compile(r"\s+")


def _clean_str(value: object, *, length: int) -> str:
    if value is None:
        return ""
    text = _WHITESPACE.sub(" ", str(value)).strip()
    if len(text) <= length:
        return text
    budget = max(1, length - 1)
    prefix = text[: budget + 1]
    shortened = prefix.rsplit(" ", 1)[0].rstrip(" ,;:-") if " " in prefix else ""
    return (shortened or text[:budget].rstrip(" ,;:-")) + "…"


def _clean_strings(value: object, *, count: int, length: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for raw in value[:count]:
        item = _clean_str(raw, length=length)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


def normalize_tailored_content(payload: dict) -> TailoredResumeContent:
    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    sections: list[ResumeSection] = []
    raw_sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    for raw_section in raw_sections[:16]:
        if not isinstance(raw_section, dict):
            continue
        entries: list[ResumeEntry] = []
        raw_entries = raw_section.get("entries") if isinstance(raw_section.get("entries"), list) else []
        for raw_entry in raw_entries[:12]:
            if not isinstance(raw_entry, dict):
                continue
            entry = ResumeEntry(
                title=_clean_str(raw_entry.get("title"), length=160),
                subtitle=_clean_str(raw_entry.get("subtitle"), length=200),
                location=_clean_str(raw_entry.get("location"), length=100),
                dates=_clean_str(raw_entry.get("dates"), length=80),
                body=_clean_str(raw_entry.get("body"), length=800),
                bullets=_clean_strings(raw_entry.get("bullets"), count=10, length=500),
            )
            if any((entry.title, entry.subtitle, entry.body, entry.bullets)):
                entries.append(entry)
        heading = _clean_str(raw_section.get("heading"), length=80)
        section = ResumeSection(
            heading=heading,
            kind=_clean_str(raw_section.get("kind"), length=40),
            intro=_clean_str(raw_section.get("intro"), length=1_200),
            # A skills section often uses a few categorized lines rather than
            # one item per skill. Keep those complete instead of turning the
            # end of each category into an ellipsis.
            items=_clean_strings(raw_section.get("items"), count=60, length=800),
            entries=tuple(entries),
        )
        if heading and any((section.intro, section.items, section.entries)):
            sections.append(section)

    return TailoredResumeContent(
        headline=_clean_str(payload.get("headline"), length=180),
        contact_email=_clean_str(contact.get("email"), length=254),
        contact_phone=_clean_str(contact.get("phone"), length=40),
        contact_location=_clean_str(contact.get("location"), length=100),
        contact_links=_clean_strings(contact.get("links"), count=8, length=200),
        sections=tuple(sections),
    )


async def parse_resume_structure(resume_text: str, *, client: TailorClient) -> dict:
    parsed = await client.parse_resume(resume_text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sections"), list):
        raise ValueError("Claude did not return a structured resume")
    return parsed


async def tailor_resume_content(
    parsed_resume: dict,
    *,
    company: str,
    title: str,
    location: str | None,
    description: str | None,
    job_url: str | None,
    client: TailorClient,
) -> TailoredResumeContent:
    payload = await client.rewrite_resume(
        parsed_resume,
        company=company,
        title=title,
        location=location,
        description=description,
        job_url=job_url,
    )
    return normalize_tailored_content(payload)
