from __future__ import annotations

import pytest

from app.resume.compile_pdf import LatexCompileError, compile_latex_to_pdf
from app.resume.latex_template import escape_latex, render_latex
from app.resume.tailor import (
    EducationEntry,
    ExperienceEntry,
    TailoredResumeContent,
    normalize_tailored_content,
    tailor_resume_content,
)


class FakeTailorClient:
    def __init__(self, tool_input: dict) -> None:
        self.tool_input = tool_input
        self.calls = 0
        self.last_message: str | None = None

    async def create_message(self, *, system, messages, tools):
        self.calls += 1
        self.last_message = messages[0]["content"]
        return {"content": [{"type": "tool_use", "name": tools[0]["name"], "input": self.tool_input}]}


class FailingTailorClient:
    async def create_message(self, *, system, messages, tools):
        raise RuntimeError("boom")


SAMPLE_PAYLOAD = {
    "summary": "Backend-focused CS student.",
    "skills": ["Python", "python", "SQL", "x" * 100],
    "experience": [
        {
            "title": "SWE Intern",
            "organization": "Acme",
            "dates": "Summer 2025",
            "bullets": ["Shipped a feature", "Fixed bugs"],
        },
        {"title": "", "organization": "Missing title, should be dropped", "bullets": []},
    ],
    "education": [{"school": "State University", "detail": "B.S. CS"}],
}


@pytest.mark.asyncio
async def test_tailor_resume_content_calls_client_with_resume_and_posting():
    client = FakeTailorClient(SAMPLE_PAYLOAD)

    content = await tailor_resume_content(
        "Python and SQL experience.",
        company="Acme",
        title="New Grad SWE",
        location="Remote",
        client=client,
    )

    assert client.calls == 1
    assert "Acme" in client.last_message
    assert "New Grad SWE" in client.last_message
    assert content.summary == "Backend-focused CS student."


@pytest.mark.asyncio
async def test_tailor_resume_content_propagates_client_failure():
    with pytest.raises(RuntimeError):
        await tailor_resume_content(
            "resume text", company="Acme", title="SWE", location=None, client=FailingTailorClient()
        )


def test_normalize_tailored_content_bounds_and_dedupes_skills():
    result = normalize_tailored_content(SAMPLE_PAYLOAD)

    assert result.skills == ("Python", "SQL", "x" * 60)
    assert len(result.experience) == 1  # entry missing a title is dropped
    assert result.experience[0].title == "SWE Intern"
    assert result.education[0].school == "State University"


def test_normalize_tailored_content_handles_missing_fields_gracefully():
    result = normalize_tailored_content({})

    assert result.summary == ""
    assert result.skills == ()
    assert result.experience == ()
    assert result.education == ()


def test_escape_latex_neutralizes_special_characters():
    escaped = escape_latex("100% & $5 back_slash {test} ~tilde^caret\\end")

    assert "%" not in escaped.replace(r"\%", "")
    assert r"\&" in escaped
    assert r"\$" in escaped
    assert r"\_" in escaped
    assert r"\{" in escaped and r"\}" in escaped


def _content(**overrides) -> TailoredResumeContent:
    defaults = dict(
        summary="A tailored summary.",
        skills=("Python", "SQL"),
        experience=(
            ExperienceEntry(
                title="SWE Intern", organization="Acme", dates="2025", bullets=("Did a thing",)
            ),
        ),
        education=(EducationEntry(school="State University", detail="B.S. CS"),),
    )
    defaults.update(overrides)
    return TailoredResumeContent(**defaults)


def test_render_latex_produces_compilable_document_with_special_characters():
    content = _content(
        summary="Improved throughput by 50% & cut costs $5k.",
        experience=(
            ExperienceEntry(
                title="SWE Intern",
                organization="Acme & Co",
                dates="2025",
                bullets=("Fixed a #1 bug worth 10%",),
            ),
        ),
    )

    tex = render_latex(content, contact_name="Alex O'Brien & Co")

    assert r"\&" in tex
    assert r"\%" in tex
    assert r"\$" in tex
    assert r"\#" in tex


@pytest.mark.asyncio
async def test_compile_latex_to_pdf_produces_real_pdf_bytes():
    tex = render_latex(_content(), contact_name="Alex Morgan")

    pdf_bytes = await compile_latex_to_pdf(tex)

    assert pdf_bytes.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_compile_latex_to_pdf_raises_on_malformed_source():
    with pytest.raises(LatexCompileError):
        await compile_latex_to_pdf(r"\documentclass{article}\begin{document}\unbalanced{")
