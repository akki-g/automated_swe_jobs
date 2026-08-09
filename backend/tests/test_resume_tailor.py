from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.resume.compile_pdf import LatexCompileError, compile_latex_to_pdf
from app.resume.latex_template import escape_latex, render_latex
from app.resume.tailor import (
    ClaudeTailorClient,
    ResumeEntry,
    ResumeSection,
    TailoredResumeContent,
    normalize_tailored_content,
    parse_resume_structure,
    tailor_resume_content,
)

PARSED_PAYLOAD = {
    "name": "Alex Morgan",
    "contact": {
        "email": "alex@example.com",
        "phone": "555-0100",
        "location": "Orlando, FL",
        "links": ["github.com/alex"],
    },
    "sections": [
        {
            "heading": "Experience",
            "kind": "experience",
            "raw_text": "SWE Intern — Acme — 2025. Shipped a distributed Python service.",
            "items": [],
            "entries": [],
        },
        {
            "heading": "Publications",
            "kind": "publications",
            "raw_text": "Graph Learning for Coordinated Agents, 2026.",
            "items": ["Graph Learning for Coordinated Agents, 2026"],
            "entries": [],
        },
    ],
    "unassigned_text": [],
}

TAILORED_PAYLOAD = {
    "headline": "Machine Learning Platform Engineer",
    "contact": {
        "email": "alex@example.com",
        "phone": "555–0100",
        "location": "Orlando, FL",
        "links": ["github.com/alex"],
    },
    "sections": [
        {
            "heading": "Summary",
            "kind": "summary",
            "intro": "Backend engineer building distributed ML systems.",
            "items": [],
            "entries": [],
        },
        {
            "heading": "Technical Skills",
            "kind": "skills",
            "intro": "",
            "items": ["Python", "Go", "Kafka", "PyTorch"],
            "entries": [],
        },
        {
            "heading": "Experience",
            "kind": "experience",
            "intro": "",
            "items": [],
            "entries": [
                {
                    "title": "Software Engineering Intern",
                    "subtitle": "Acme & Co",
                    "location": "Orlando, FL",
                    "dates": "May–Aug 2025",
                    "body": "Core Platform Team",
                    "bullets": [
                        "Shipped a distributed Python service processing 2M events/day.",
                        "Raised automated test coverage from 40% to 80%.",
                    ],
                }
            ],
        },
        {
            "heading": "Projects",
            "kind": "projects",
            "intro": "",
            "items": [],
            "entries": [
                {
                    "title": "CitePilot",
                    "subtitle": "Independent Project",
                    "location": "",
                    "dates": "2026",
                    "body": "",
                    "bullets": ["Combined pgvector and Neo4j retrieval using reciprocal rank fusion."],
                }
            ],
        },
        {
            "heading": "Publications",
            "kind": "publications",
            "intro": "",
            "items": ["Graph Learning for Coordinated Agents, 2026"],
            "entries": [],
        },
        {
            "heading": "Education",
            "kind": "education",
            "intro": "",
            "items": [],
            "entries": [
                {
                    "title": "University of Central Florida",
                    "subtitle": "B.S. Computer Science & B.S. Statistics",
                    "location": "Orlando, FL",
                    "dates": "Expected 2027",
                    "body": "GPA: 3.7",
                    "bullets": [],
                }
            ],
        },
    ],
}


class FakeTailorClient:
    def __init__(self) -> None:
        self.parse_calls: list[str] = []
        self.rewrite_calls: list[dict] = []

    async def parse_resume(self, resume_text: str) -> dict:
        self.parse_calls.append(resume_text)
        return PARSED_PAYLOAD

    async def rewrite_resume(
        self, parsed_resume, *, company, title, location, description, job_url
    ) -> dict:
        self.rewrite_calls.append(
            {
                "parsed_resume": parsed_resume,
                "company": company,
                "title": title,
                "location": location,
                "description": description,
                "job_url": job_url,
            }
        )
        return TAILORED_PAYLOAD


class FailingTailorClient(FakeTailorClient):
    async def rewrite_resume(self, *args, **kwargs):
        raise RuntimeError("boom")


class RecordingMessages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"] == "parser-model":
            payload = PARSED_PAYLOAD
        elif "tools" in kwargs:
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Official role keywords: Python, Kafka")]
            )
        else:
            payload = TAILORED_PAYLOAD
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(payload))]
        )


@pytest.mark.asyncio
async def test_anthropic_client_uses_haiku_schema_then_sonnet_search_and_rewrite():
    messages = RecordingMessages()
    client = object.__new__(ClaudeTailorClient)
    client._client = SimpleNamespace(messages=messages)
    client._parser_model = "parser-model"
    client._writer_model = "writer-model"

    parsed = await client.parse_resume("Complete source resume")
    rewritten = await client.rewrite_resume(
        parsed,
        company="Acme",
        title="ML Platform Engineer",
        location="Remote",
        description="Build Python and Kafka services.",
        job_url="https://example.com/jobs/1",
    )

    parse_call, research_call, rewrite_call = messages.calls
    assert parse_call["model"] == "parser-model"
    assert parse_call["output_config"]["format"]["type"] == "json_schema"
    assert [tool["name"] for tool in research_call["tools"]] == ["web_search", "web_fetch"]
    assert research_call["model"] == "writer-model"
    assert rewrite_call["model"] == "writer-model"
    assert rewrite_call["output_config"]["format"]["type"] == "json_schema"
    rewrite_prompt = rewrite_call["messages"][0]["content"]
    assert "Official role keywords: Python, Kafka" in rewrite_prompt
    assert "Graph Learning for Coordinated Agents" in rewrite_prompt
    assert rewritten["headline"] == "Machine Learning Platform Engineer"


@pytest.mark.asyncio
async def test_small_model_parse_preserves_generic_sections():
    client = FakeTailorClient()

    parsed = await parse_resume_structure("Full resume text", client=client)

    assert client.parse_calls == ["Full resume text"]
    assert [section["heading"] for section in parsed["sections"]] == [
        "Experience",
        "Publications",
    ]
    assert "Graph Learning" in parsed["sections"][1]["raw_text"]


@pytest.mark.asyncio
async def test_writer_receives_lossless_resume_and_complete_posting_context():
    client = FakeTailorClient()

    content = await tailor_resume_content(
        PARSED_PAYLOAD,
        company="Acme",
        title="New Grad ML Platform Engineer",
        location="Remote",
        description="Build Python data infrastructure.",
        job_url="https://example.com/job",
        client=client,
    )

    call = client.rewrite_calls[0]
    assert call["parsed_resume"] is PARSED_PAYLOAD
    assert call["description"] == "Build Python data infrastructure."
    assert call["job_url"] == "https://example.com/job"
    assert content.headline == "Machine Learning Platform Engineer"
    assert [section.heading for section in content.sections] == [
        "Summary",
        "Technical Skills",
        "Experience",
        "Projects",
        "Publications",
        "Education",
    ]


@pytest.mark.asyncio
async def test_tailor_resume_content_propagates_writer_failure():
    with pytest.raises(RuntimeError):
        await tailor_resume_content(
            PARSED_PAYLOAD,
            company="Acme",
            title="SWE",
            location=None,
            description=None,
            job_url=None,
            client=FailingTailorClient(),
        )


def test_normalize_tailored_content_keeps_custom_sections_and_full_entries():
    result = normalize_tailored_content(TAILORED_PAYLOAD)

    publications = next(section for section in result.sections if section.kind == "publications")
    experience = next(section for section in result.sections if section.kind == "experience")
    assert publications.items == ("Graph Learning for Coordinated Agents, 2026",)
    assert experience.entries[0].subtitle == "Acme & Co"
    assert len(experience.entries[0].bullets) == 2


def test_normalize_tailored_content_handles_missing_fields_gracefully():
    result = normalize_tailored_content({})

    assert result.headline == ""
    assert result.sections == ()


def test_escape_latex_neutralizes_special_characters_and_unicode():
    escaped = escape_latex("100% & $5 back_slash {test} — May–Aug · done")

    assert r"\&" in escaped
    assert r"\$" in escaped
    assert r"\_" in escaped
    assert r"\{" in escaped and r"\}" in escaped
    assert "---" in escaped and "May--Aug" in escaped
    assert r"\textperiodcentered{}" in escaped


def _content() -> TailoredResumeContent:
    return normalize_tailored_content(TAILORED_PAYLOAD)


def test_render_latex_renders_every_preserved_section_and_real_separators():
    tex = render_latex(
        _content(),
        contact_name="Alex Morgan",
        contact_email="alex@example.com",
        contact_phone="555–0100",
    )

    assert all(f"\\ressection{{{heading}}}" in tex for heading in (
        "SUMMARY",
        "TECHNICAL SKILLS",
        "EXPERIENCE",
        "PROJECTS",
        "PUBLICATIONS",
        "EDUCATION",
    ))
    assert r"Python \enspace\textcolor{accent}{\textbullet}\enspace Go" in tex
    assert r"\textbackslash{}textbullet" not in tex
    assert "555--0100" in tex
    assert r"Acme \& Co" in tex


@pytest.mark.asyncio
async def test_rich_multisection_resume_compiles_and_remains_searchable():
    pdf_bytes = await compile_latex_to_pdf(
        render_latex(
            _content(),
            contact_name="Alex Morgan",
            contact_email="alex@example.com",
            contact_phone="(555) 010-1000",
        )
    )

    reader = PdfReader(io.BytesIO(pdf_bytes))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert pdf_bytes.startswith(b"%PDF-")
    assert "PROJECTS" in extracted
    assert "PUBLICATIONS" in extracted
    assert "Graph Learning for Coordinated Agents" in extracted


@pytest.mark.asyncio
async def test_dense_resume_uses_compact_layout_without_truncation_or_overflow_page():
    bullet = (
        "Designed distributed machine-learning platform services with production monitoring, "
        "failure isolation, automated testing, and measurable improvements across data pipelines."
    )
    dense_entries = tuple(
        ResumeEntry(
            title=f"Engineering and Research Role {index}",
            subtitle=f"Organization {index}",
            location="Orlando, FL",
            dates="2024–Present",
            body="Platform and Applied ML Team",
            bullets=tuple(bullet for _ in range(count)),
        )
        for index, count in enumerate((5, 3, 4, 3, 3))
    )
    complete_skills = (
        "Systems & Data: FastAPI, REST APIs, Kafka, PostgreSQL/pgvector, MongoDB, Redis, "
        "Neo4j, HDFS/HBase, PostGIS, GraphDB, SQLAlchemy, DB2, InfluxDB"
    )
    content = TailoredResumeContent(
        headline="Machine Learning Platform Engineer",
        contact_email="alex@example.com",
        contact_phone="555-0100",
        contact_location="Orlando, FL",
        contact_links=("github.com/alex",),
        sections=(
            ResumeSection("Technical Skills", "skills", "", (complete_skills,), ()),
            ResumeSection("Experience", "experience", "", (), dense_entries[:3]),
            ResumeSection("Projects", "projects", "", (), dense_entries[3:]),
        ),
    )

    tex = render_latex(content, contact_name="Alex Morgan")
    pdf_bytes = await compile_latex_to_pdf(tex)

    assert r"\fontsize{8.45}{9.35}\selectfont" in tex
    assert "InfluxDB" in tex
    assert len(PdfReader(io.BytesIO(pdf_bytes)).pages) == 1


@pytest.mark.asyncio
async def test_compile_latex_to_pdf_raises_on_malformed_source():
    with pytest.raises(LatexCompileError):
        await compile_latex_to_pdf(r"\documentclass{article}\begin{document}\unbalanced{")
