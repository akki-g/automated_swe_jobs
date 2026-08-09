from __future__ import annotations

from app.resume.tailor import TailoredResumeContent

# Characters LaTeX treats specially; must be escaped in any user-derived
# string (name, bullets, summary, ...) before it goes into the template —
# otherwise a resume/job title containing e.g. "&" or "%" breaks the compile
# or, worse, is interpreted as a LaTeX command.
_LATEX_SPECIAL_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(text: str) -> str:
    result = []
    for char in text:
        result.append(_LATEX_SPECIAL_CHARS.get(char, char))
    return "".join(result)


def _itemize(bullets: tuple[str, ...]) -> str:
    if not bullets:
        return ""
    items = "\n".join(f"  \\item {escape_latex(bullet)}" for bullet in bullets)
    return f"\\begin{{itemize}}\n{items}\n\\end{{itemize}}"


def render_latex(content: TailoredResumeContent, *, contact_name: str) -> str:
    """Fill a fixed, pre-tested one-page LaTeX resume template via plain
    string substitution into known-good slots — the model never generates
    LaTeX directly (see spec: Resume tailoring), so a malformed brace or
    stray backslash from the model can't reach the document structure."""
    name = escape_latex(contact_name or "Candidate")
    summary = escape_latex(content.summary)
    skills_line = escape_latex(" \\textbullet\\ ".join(content.skills)) if content.skills else ""

    experience_blocks = []
    for entry in content.experience:
        dates = f" \\hfill {escape_latex(entry.dates)}" if entry.dates else ""
        experience_blocks.append(
            "\\textbf{%s} --- %s%s\\\\\n%s"
            % (
                escape_latex(entry.title),
                escape_latex(entry.organization),
                dates,
                _itemize(entry.bullets),
            )
        )
    experience_section = "\n\\vspace{4pt}\n".join(experience_blocks)

    education_lines = []
    for entry in content.education:
        detail = f" --- {escape_latex(entry.detail)}" if entry.detail else ""
        education_lines.append(f"{escape_latex(entry.school)}{detail}\\\\")
    education_section = "\n".join(education_lines)

    return r"""\documentclass[11pt]{article}
\usepackage[margin=0.75in]{geometry}
\usepackage{enumitem}
\setlist[itemize]{leftmargin=1.2em, itemsep=1pt, topsep=2pt}
\pagestyle{empty}
\begin{document}
\begin{center}
{\LARGE \textbf{%s}}
\end{center}
\vspace{6pt}

\textbf{\large Summary}\\
%s

\vspace{8pt}
\textbf{\large Skills}\\
%s

\vspace{8pt}
\textbf{\large Experience}\\
%s

\vspace{8pt}
\textbf{\large Education}\\
%s

\end{document}
""" % (name, summary, skills_line, experience_section, education_section)
