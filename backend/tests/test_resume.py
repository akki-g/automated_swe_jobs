from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from app.resume.extract import normalize_resume_profile
from app.resume.parse import ResumeParseError, extract_resume_text


def test_extract_docx_text_in_memory():
    document = Document()
    document.add_paragraph("Python · SQL · experimentation")
    content = BytesIO()
    document.save(content)

    text = extract_resume_text(content.getvalue(), "resume.docx")

    assert text == "Python · SQL · experimentation"


def test_resume_parser_rejects_extension_content_mismatch():
    with pytest.raises(ResumeParseError, match="does not appear"):
        extract_resume_text(b"not a document", "resume.pdf")


def test_normalize_resume_profile_bounds_and_validates_model_output():
    result = normalize_resume_profile(
        {
            "skills": ["Python", "python", "x" * 100],
            "past_titles": "not-a-list",
            "experience_level": "executive",
            "years_experience": 900,
            "inferred_target_fields": ["consulting", "made_up"],
            "education_fields": ["Economics"],
            "summary": "s" * 700,
        }
    )

    assert result.skills == ["Python", "x" * 80]
    assert result.experience_level == "unknown"
    assert result.years_experience == 60
    assert result.inferred_target_fields == ["consulting"]
    assert len(result.summary) == 500
