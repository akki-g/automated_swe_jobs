from __future__ import annotations

import re

import httpx

_PUNCT_PATTERN = re.compile(r"[^a-z0-9\s]")
_SUFFIX_PATTERN = re.compile(r"\b(?:inc|llc|ltd|corp|corporation|co)\b\.?", re.IGNORECASE)

_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}"
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def candidate_slugs(company_name: str) -> list[str]:
    """Plausible ATS board slugs for a company's display name. A heuristic,
    not a guarantee — see detect_ats_board for what happens when none hit."""
    base = _SUFFIX_PATTERN.sub("", company_name.lower())
    base = _PUNCT_PATTERN.sub("", base).strip()
    words = base.split()
    if not words:
        return []
    no_space = "".join(words)
    candidates = [no_space]
    if len(words) > 1:
        hyphenated = "-".join(words)
        candidates.append(hyphenated)
    return candidates


async def _probe(client: httpx.AsyncClient, url: str, *, expect_list: bool) -> bool:
    try:
        response = await client.get(url, params={"mode": "json"} if expect_list else None)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    return isinstance(data, list) if expect_list else isinstance(data, dict)


async def detect_ats_board(company_name: str) -> tuple[str, str] | None:
    """Probe Greenhouse, Lever, and Ashby's real public APIs for a board
    matching this company name, trying a couple of plausible slug spellings.
    Returns (provider, slug) for the first hit, or None if nothing was
    found. Deliberately limited to these three real ATS APIs — never
    generic career-page scraping, for the same ToS/reliability reasons the
    design ruled out LinkedIn/Indeed scraping."""
    slugs = candidate_slugs(company_name)
    if not slugs:
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        for slug in slugs:
            if await _probe(client, _GREENHOUSE_URL.format(slug=slug), expect_list=False):
                return "greenhouse", slug
            if await _probe(client, _LEVER_URL.format(slug=slug), expect_list=True):
                return "lever", slug
            if await _probe(client, _ASHBY_URL.format(slug=slug), expect_list=False):
                return "ashby", slug
    return None
