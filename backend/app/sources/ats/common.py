from __future__ import annotations

import re

from app.domain.models import RoleType

_INTERN_PATTERN = re.compile(
    r"\bintern(ship)?\b|\bco-?op\b|\bsummer\s+(?:analyst|associate)\b",
    re.IGNORECASE,
)
_NEW_GRAD_PATTERN = re.compile(
    r"\bnew\s*grad(uate)?\b|\bentry[\s-]?level\b|\buniversity\s*grad(uate)?\b|"
    r"\bearly[\s-]?career\b|\brecent\s+grad(uate)?\b|\bcollege\s+grad(uate)?\b|"
    r"\bgraduate\s+(?:role|program(?:me)?|scheme|analyst|associate)\b|"
    r"\b(?:campus|university)\s+(?:hire|hiring|program(?:me)?|recruit(?:ing)?)\b|"
    r"\b(?:rotational|development|analyst)\s+program(?:me)?\b|\bapprentice(ship)?\b|"
    r"\b(?:management|managerial)\s+trainee\b|"
    r"\b20\d{2}\s+(?:full[\s-]?time\s+)?(?:analyst|associate)\b",
    re.IGNORECASE,
)


def infer_role_type(title: str, *context: str | None) -> RoleType | None:
    """ATS boards list every open role, not just new-grad/intern ones, so we
    infer role type from the title. A posting we can't classify is dropped by
    the rule filters for any user with role_types set (see spec: Matching) —
    intentional, since an unclassified senior role should not slip through.
    """
    text = " ".join(part for part in (title, *context) if part)
    if _INTERN_PATTERN.search(text):
        return RoleType.INTERN
    if _NEW_GRAD_PATTERN.search(text):
        return RoleType.NEW_GRAD
    return None
