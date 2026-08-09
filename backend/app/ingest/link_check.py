from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Statuses that are unambiguous evidence the posting is actually gone (not
# just a network hiccup) — a closed/removed job listing. 5xx, timeouts, and
# other errors are deliberately NOT treated as dead: this codebase's
# consistent philosophy (see GitHubListSource.check_for_changes,
# Source.fetch failure handling) is to fail open on an inconclusive result
# rather than risk suppressing a posting that might still be perfectly live
# because of a transient blip on our end or theirs.
_DEAD_STATUS_CODES = {404, 410, 451}


async def check_link_alive(url: str, client: httpx.AsyncClient) -> bool | None:
    """Returns True (confirmed reachable), False (confirmed dead — an
    unambiguous 404/410/451), or None (inconclusive: network error, timeout,
    or a server error that says nothing about whether the listing itself is
    gone). Callers should treat None the same as True — see module docstring
    on why an inconclusive check must never suppress a posting.

    Uses a streamed GET rather than a plain GET so only the response status
    is read, not the full page body — this runs across every newly-seen
    posting each cycle (see pipeline.py), so avoiding a full-body download
    per check matters at volume. HEAD isn't used instead because many ATS/
    career-page hosts don't implement it correctly (405, or a different
    status than the real page would give).
    """
    if not url or not url.strip():
        return False
    try:
        async with client.stream("GET", url) as response:
            status = response.status_code
    except Exception:  # noqa: BLE001 - see below; every failure here is inconclusive
        # Deliberately broader than httpx.HTTPError. A scraped URL can be
        # malformed enough that httpx rejects it before any request is made,
        # and not all of those are HTTPError subclasses — httpx.InvalidURL
        # derives straight from Exception. Letting one escape would abort
        # the caller's asyncio.gather and skip link validation for every
        # other posting in the same cycle, so an unparseable URL is treated
        # like any other inconclusive result.
        logger.debug("check_link_alive: request failed for %s", url, exc_info=True)
        return None

    if status in _DEAD_STATUS_CODES:
        return False
    if status >= 500:
        return None
    return True
