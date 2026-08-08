from __future__ import annotations

import re

from app.domain.models import RoleType

_INTERN_PATTERN = re.compile(r"\bintern(ship)?\b|\bco-?op\b", re.IGNORECASE)
_NEW_GRAD_PATTERN = re.compile(
    r"\bnew\s*grad(uate)?\b|\bentry[\s-]?level\b|\buniversity\s*grad(uate)?\b|\bearly[\s-]?career\b",
    re.IGNORECASE,
)


def infer_role_type(title: str) -> RoleType | None:
    """ATS boards list every open role, not just new-grad/intern ones, so we
    infer role type from the title. A posting we can't classify is dropped by
    the rule filters for any user with role_types set (see spec: Matching) —
    intentional, since an unclassified senior role should not slip through.
    """
    if _INTERN_PATTERN.search(title):
        return RoleType.INTERN
    if _NEW_GRAD_PATTERN.search(title):
        return RoleType.NEW_GRAD
    return None
