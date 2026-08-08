from __future__ import annotations

from app.domain.models import Posting


def dedupe_postings(postings: list[Posting]) -> list[Posting]:
    """Collapse postings sharing a posting_key within one batch, keeping the
    first occurrence."""
    seen: set[str] = set()
    deduped: list[Posting] = []
    for posting in postings:
        if posting.posting_key in seen:
            continue
        seen.add(posting.posting_key)
        deduped.append(posting)
    return deduped


def filter_new(postings: list[Posting], existing_keys: set[str]) -> list[Posting]:
    """Given already-deduped postings and the set of posting_keys already
    stored, return only the ones not yet seen. Pure — the caller owns the DB
    read that produces existing_keys."""
    return [p for p in postings if p.posting_key not in existing_keys]
