from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, nullable=True
    )
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sms_provider: Mapped[str] = mapped_column(String(20), default="signalwire")
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_method: Mapped[str] = mapped_column(
        String(50), default="verbal-friend-onboarding"
    )
    profile_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set after the scheduler has matched a newly-completed web profile
    # against the recent open inventory that predates that user's signup.
    # Without this marker, matching only ever sees brand-new Posting rows and
    # a new user can have an empty dashboard indefinitely.
    initial_matches_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Versioned separately from the timestamp so a corrected backfill can
    # safely reprocess profiles whose older run incorrectly marked success.
    initial_match_backfill_version: Mapped[int] = mapped_column(Integer, default=0)
    # How many times the backfill has ranked this profile's inventory without
    # covering every survivor. Retrying recovers from transient ranking
    # failures, but some postings never get a result no matter how often they
    # are sent, so retries need a bound — see
    # pipeline._MAX_BACKFILL_ATTEMPTS.
    initial_match_backfill_attempts: Mapped[int] = mapped_column(Integer, default=0)
    email_digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_digest_time: Mapped[str] = mapped_column(String(5), default="08:00")
    last_email_digest_sent_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # When the user's current visit to the matches page began. Paired with
    # matches_last_viewed_at (the comparison baseline, which only advances at
    # a visit boundary) so that "New" stays stable across the many requests
    # one visit makes — see api/matches.py::list_matches.
    matches_visit_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    matches_last_viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    criteria: Mapped["Criteria | None"] = relationship(
        back_populates="user", uselist=False
    )


class Criteria(Base):
    __tablename__ = "criteria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    role_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    target_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    locations: Mapped[list[str]] = mapped_column(JSON, default=list)
    sponsorship_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    min_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    freeform_notes: Mapped[str] = mapped_column(String(2000), default="")
    resume_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    resume_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="criteria")


class Posting(Base):
    __tablename__ = "postings"
    __table_args__ = (UniqueConstraint("posting_key", name="uq_postings_posting_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 500 comfortably covers 3 components capped at 150 chars each plus two
    # "|" separators (see ingest/normalize.py::_MAX_COMPONENT_LENGTH) — the
    # cap there is the real guarantee; this width is headroom, not the
    # enforcement point.
    posting_key: Mapped[str] = mapped_column(String(500), index=True)
    source: Mapped[str] = mapped_column(String(255))
    company: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    location: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    role_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("user_id", "posting_id", name="uq_matches_user_posting"),
        Index("ix_matches_user_notified_at", "user_id", "notified_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    blurb: Mapped[str] = mapped_column(String(2000), default="")
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    lane: Mapped[str] = mapped_column(String(10), default="slow")
    match_reason: Mapped[str] = mapped_column(String(50), default="new_posting")
    notified_channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    matched_target_field: Mapped[str | None] = mapped_column(String(50), nullable=True)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Watchlist(Base):
    """A user's explicit ask to be tracked on a specific company (see spec
    addendum: Company watchlist). `company_key` is the same normalized form
    used for posting_key components, so it can be matched against a
    posting's company without re-normalizing. `ats_provider`/`ats_slug` are
    filled in once auto-detection (app/watchlist/detect.py) finds a real
    board; `status` tracks whether that detection succeeded."""

    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("user_id", "company_key", name="uq_watchlist_user_company"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    company_name: Mapped[str] = mapped_column(String(255))
    company_key: Mapped[str] = mapped_column(String(255), index=True)
    ats_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ats_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending/active/not_found
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    match_id: Mapped[int | None] = mapped_column(
        ForeignKey("matches.id"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(10))  # "inbound" | "outbound"
    channel: Mapped[str] = mapped_column(String(10))  # "sms" | "email"
    provider: Mapped[str] = mapped_column(String(20), default="")
    body: Mapped[str] = mapped_column(String(4000), default="")
    status: Mapped[str] = mapped_column(String(20), default="sent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
