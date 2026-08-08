from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RoleType(str, Enum):
    NEW_GRAD = "new_grad"
    INTERN = "intern"


class TargetField(str, Enum):
    SOFTWARE_ENGINEERING = "software_engineering"
    DATA_SCIENCE_ANALYTICS = "data_science_analytics"
    PRODUCT_MANAGEMENT = "product_management"
    FINANCE_INVESTMENT_BANKING = "finance_investment_banking"
    CONSULTING = "consulting"
    MARKETING = "marketing"
    SALES = "sales"
    OPERATIONS = "operations"
    DESIGN = "design"


class PostingStatus(str, Enum):
    OPEN = "open"
    STALE = "stale"
    CLOSED = "closed"


class Priority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"


@dataclass(frozen=True)
class RawPosting:
    """A posting as returned by a source adapter, before normalization."""

    source: str
    company: str
    title: str
    url: str
    location: str | None
    role_type: RoleType | None
    posted_at: datetime | None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Posting:
    """A normalized posting, identified by posting_key (see ingest/normalize.py)."""

    posting_key: str
    source: str
    company: str
    title: str
    url: str
    location: str | None
    role_type: RoleType | None
    posted_at: datetime | None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Criteria:
    user_id: int
    role_types: tuple[RoleType, ...] = ()
    target_fields: tuple[TargetField, ...] = ()
    keywords: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    sponsorship_required: bool | None = None
    min_date: datetime | None = None
    freeform_notes: str = ""
    resume_profile: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Match:
    user_id: int
    posting_key: str
    score: float
    blurb: str
    priority: Priority
    lane: str
    match_reason: str = "new_posting"
