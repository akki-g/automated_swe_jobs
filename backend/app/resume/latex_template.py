from __future__ import annotations

from app.resume.tailor import ResumeEntry, ResumeSection, TailoredResumeContent

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
    "²": r"\textsuperscript{2}",
    "\u00a0": "~",
}


def escape_latex(text: str) -> str:
    return "".join(_LATEX_SPECIAL_CHARS.get(char, char) for char in text)


def _separated_line(values: tuple[str, ...]) -> str:
    return r" \enspace\textcolor{accent}{\textbullet}\enspace ".join(
        escape_latex(value) for value in values if value
    )


def _itemize(bullets: tuple[str, ...]) -> str:
    if not bullets:
        return ""
    items = "\n".join(f"  \\item {escape_latex(bullet)}" for bullet in bullets)
    return f"\\begin{{itemize}}\n{items}\n\\end{{itemize}}"


def _entry_block(entry: ResumeEntry) -> str:
    heading = entry.title or entry.subtitle
    secondary_parts = tuple(
        value
        for value in (
            entry.subtitle if entry.title else "",
            entry.location,
        )
        if value
    )
    parts = [
        "\\begin{tabularx}{\\textwidth}{@{}X r@{}}",
        f"  \\textbf{{{escape_latex(heading)}}} & \\textbf{{{escape_latex(entry.dates)}}} \\\\",
    ]
    if secondary_parts:
        parts.extend(
            (
                f"  \\textit{{{_separated_line(secondary_parts)}}} &",
                "\\end{tabularx}",
            )
        )
    else:
        parts.append("\\end{tabularx}")
    if entry.body:
        parts.append(escape_latex(entry.body))
    if entry.bullets:
        parts.append(_itemize(entry.bullets))
    return "\\begin{samepage}\n" + "\n".join(parts) + "\n\\end{samepage}"


def _section_block(section: ResumeSection) -> str:
    parts = [f"\\ressection{{{escape_latex(section.heading.upper())}}}"]
    if section.intro:
        parts.append(escape_latex(section.intro) + r"\par")
    if section.items:
        parts.append(_separated_line(section.items) + r"\par")
    if section.entries:
        if section.intro or section.items:
            parts.append(r"\vspace{2.5pt}")
        parts.append("\n\\vspace{3pt}\n".join(_entry_block(entry) for entry in section.entries))
    return "\n".join(parts)


def render_latex(
    content: TailoredResumeContent,
    *,
    contact_name: str,
    contact_email: str | None = None,
    contact_phone: str | None = None,
) -> str:
    """Render every model-preserved section using one consistent house style."""
    name = escape_latex(contact_name or "Candidate")
    contact_values = tuple(
        value
        for value in (
            contact_email or content.contact_email,
            contact_phone or content.contact_phone,
            content.contact_location,
            *content.contact_links,
        )
        if value
    )
    contact_line = _separated_line(contact_values)
    headline = escape_latex(content.headline)
    sections = "\n".join(_section_block(section) for section in content.sections)
    content_weight = sum(
        len(section.intro)
        + sum(len(item) for item in section.items)
        + sum(
            len(entry.title)
            + len(entry.subtitle)
            + len(entry.location)
            + len(entry.dates)
            + len(entry.body)
            + sum(len(bullet) for bullet in entry.bullets)
            for entry in section.entries
        )
        for section in content.sections
    )
    if content_weight > 3_600:
        margins = "top=0.32in,bottom=0.32in,left=0.48in,right=0.48in"
        body_font = r"\fontsize{8.45}{9.35}\selectfont"
        section_gap = "4pt"
        item_spacing = "0.25pt"
        item_top_spacing = "0.8pt"
    else:
        margins = "top=0.45in,bottom=0.45in,left=0.58in,right=0.58in"
        body_font = r"\small"
        section_gap = "6pt"
        item_spacing = "1pt"
        item_top_spacing = "1.5pt"

    template = r"""\documentclass[10pt,letterpaper]{article}
\usepackage[letterpaper,__MARGINS__]{geometry}
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
\setlist[itemize]{leftmargin=1.25em, label=\textbullet, itemsep=__ITEM_SPACING__, parsep=0pt, topsep=__ITEM_TOP_SPACING__, partopsep=0pt}
\newcommand{\ressection}[1]{%
  \vspace{__SECTION_GAP__}%
  {\color{accent}\large\bfseries #1}\par\vspace{1.5pt}%
  {\color{accent}\hrule height 0.7pt}\vspace{3pt}%
}
\pagestyle{empty}
\raggedright
\begin{document}
__BODY_FONT__
\begin{center}
{\fontsize{18}{20}\selectfont\bfseries\color{accent} __NAME__}\par
\vspace{2pt}
__CONTACT__
__HEADLINE__
\end{center}
\vspace{-2pt}

__SECTIONS__

\end{document}
"""
    headline_line = f"\\par\\vspace{{2pt}}\\textit{{{headline}}}" if headline else ""
    return (
        template.replace("__MARGINS__", margins)
        .replace("__BODY_FONT__", body_font)
        .replace("__SECTION_GAP__", section_gap)
        .replace("__ITEM_SPACING__", item_spacing)
        .replace("__ITEM_TOP_SPACING__", item_top_spacing)
        .replace("__NAME__", name)
        .replace("__CONTACT__", contact_line)
        .replace("__HEADLINE__", headline_line)
        .replace("__SECTIONS__", sections)
    )
