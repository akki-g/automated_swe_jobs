from app.domain.models import RoleType
from app.sources.ats.ashby import AshbySource
from app.sources.ats.common import infer_role_type
from app.sources.ats.greenhouse import GreenhouseSource
from app.sources.ats.lever import LeverSource


def test_infer_role_type_intern():
    assert infer_role_type("Software Engineering Intern") == RoleType.INTERN
    assert infer_role_type("Summer Co-op - Firmware") == RoleType.INTERN
    assert infer_role_type("2027 Summer Analyst") == RoleType.INTERN


def test_infer_role_type_new_grad():
    assert infer_role_type("New Grad Software Engineer") == RoleType.NEW_GRAD
    assert infer_role_type("Entry-Level Backend Engineer") == RoleType.NEW_GRAD
    assert infer_role_type("2027 Analyst, Liquid Structured Credit") == RoleType.NEW_GRAD
    assert infer_role_type("Marketing Graduate Program") == RoleType.NEW_GRAD


def test_infer_role_type_unclassified():
    assert infer_role_type("Senior Staff Engineer") is None


def test_greenhouse_to_raw_posting():
    source = GreenhouseSource("acme")
    job = {
        "title": "New Grad Software Engineer",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        "location": {"name": "Remote"},
    }
    posting = source._to_raw_posting(job)

    assert posting.source == "greenhouse:acme"
    assert posting.title == "New Grad Software Engineer"
    assert posting.role_type == RoleType.NEW_GRAD
    assert posting.location == "Remote"


def test_lever_to_raw_posting():
    source = LeverSource("acme")
    posting_dict = {
        "text": "Software Engineer Intern",
        "hostedUrl": "https://jobs.lever.co/acme/1",
        "categories": {"location": "New York"},
        "createdAt": 1700000000000,
    }
    posting = source._to_raw_posting(posting_dict)

    assert posting.source == "lever:acme"
    assert posting.role_type == RoleType.INTERN
    assert posting.location == "New York"
    assert posting.posted_at is not None


def test_ashby_to_raw_posting():
    source = AshbySource("acme")
    job = {
        "title": "New Grad Backend Engineer",
        "jobUrl": "https://jobs.ashbyhq.com/acme/1",
        "location": "San Francisco",
    }
    posting = source._to_raw_posting(job)

    assert posting.source == "ashby:acme"
    assert posting.role_type == RoleType.NEW_GRAD
    assert posting.location == "San Francisco"
