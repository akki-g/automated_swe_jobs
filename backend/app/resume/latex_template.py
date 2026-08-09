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
    # Normalize common Unicode punctuation explicitly. The Tectonic/XeTeX
    # build paired with Helvetica's T1 encoding otherwise drops en dashes or
    # renders middle dots as unrelated accented glyphs.
    "–": "--",
    "—": "---",
    "−": "-",
    "·": r"\textperiodcentered{}",
    "•": r"\textbullet{}",
    "‘": "`",
    "’": "'",
    "“": "``",
    "”": "''",
    "…": r"\ldots{}",
    "\u00a0": "~",
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


def _separated_line(values: tuple[str, ...]) -> str:
    """Escape values individually so the separator remains real LaTeX."""
    return r" \enspace\textcolor{accent}{\textbullet}\enspace ".join(
        escape_latex(value) for value in values if value
    )


def render_latex(
    content: TailoredResumeContent,
    *,
    contact_name: str,
    contact_email: str | None = None,
    contact_phone: str | None = None,
) -> str:
    """Fill a compact, fixed one-page template with escaped structured data."""
    name = escape_latex(contact_name or "Candidate")
    summary = escape_latex(content.summary)
    contact_line = _separated_line(tuple(value for value in (contact_email, contact_phone) if value))
    skills_line = _separated_line(content.skills)

    experience_blocks = []
    for entry in content.experience:
        experience_blocks.append(
            "\\begin{tabularx}{\\textwidth}{@{}X r@{}}\n"
            "  \\textbf{%s} & \\textbf{%s} \\\\\n"
            "  \\textit{%s} &\n"
            "\\end{tabularx}\n%s"
            % (
                escape_latex(entry.title),
                escape_latex(entry.dates),
                escape_latex(entry.organization),
                _itemize(entry.bullets),
            )
        )
    experience_section = "\n\\vspace{2.5pt}\n".join(experience_blocks)

    education_lines = []
    for entry in content.education:
        detail = f" \\enspace {escape_latex(entry.detail)}" if entry.detail else ""
        education_lines.append(f"\\textbf{{{escape_latex(entry.school)}}}{detail}\\\\[1pt]")
    education_section = "\n".join(education_lines)

    template = r"""\documentclass[10pt,letterpaper]{article}
\usepackage[letterpaper,top=0.42in,bottom=0.42in,left=0.55in,right=0.55in]{geometry}
\usepackage[T1]{fontenc}
\usepackage{helvet}
\usepackage{tabularx}
\usepackage{enumitem}
\usepackage{xcolor}
\definecolor{accent}{HTML}{243B53}
\renewcommand{\familydefault}{\sfdefault}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\setlength{\tabcolsep}{0pt}
\setlist[itemize]{leftmargin=1.25em, label=\textbullet, itemsep=0.5pt, parsep=0pt, topsep=1pt, partopsep=0pt}
\newcommand{\ressection}[1]{%
  \vspace{5pt}%
  {\color{accent}\large\bfseries #1}\par\vspace{1.5pt}%
  {\color{accent}\hrule height 0.7pt}\vspace{3pt}%
}
\pagestyle{empty}
\raggedright
\begin{document}
\small
\begin{center}
{\fontsize{18}{20}\selectfont\bfseries\color{accent} __NAME__}\par
\vspace{2pt}
__CONTACT__
\end{center}
\vspace{-2pt}

\ressection{SUMMARY}
__SUMMARY__

\ressection{SKILLS}
__SKILLS__

\ressection{EXPERIENCE}
__EXPERIENCE__

\ressection{EDUCATION}
__EDUCATION__

\end{document}
"""
    return (
        template.replace("__NAME__", name)
        .replace("__CONTACT__", contact_line)
        .replace("__SUMMARY__", summary)
        .replace("__SKILLS__", skills_line)
        .replace("__EXPERIENCE__", experience_section)
        .replace("__EDUCATION__", education_section)
    )
