from __future__ import annotations

from pathlib import Path

import yaml

from app.sources.ats.ashby import AshbySource
from app.sources.ats.greenhouse import GreenhouseSource
from app.sources.ats.lever import LeverSource
from app.sources.base import Source

DEFAULT_COMPANIES_PATH = Path(__file__).resolve().parent.parent / "companies.yaml"


def load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    return {
        "greenhouse": data.get("greenhouse", []) or [],
        "lever": data.get("lever", []) or [],
        "ashby": data.get("ashby", []) or [],
    }


def build_ats_sources(path: Path = DEFAULT_COMPANIES_PATH) -> list[Source]:
    companies = load_companies(path)
    sources: list[Source] = []
    sources += [GreenhouseSource(slug) for slug in companies["greenhouse"]]
    sources += [LeverSource(slug) for slug in companies["lever"]]
    sources += [AshbySource(slug) for slug in companies["ashby"]]
    return sources
