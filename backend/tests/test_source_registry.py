import pytest

from app.sources.registry import SourceRegistry


@pytest.fixture
def companies_yaml(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text("greenhouse:\n  - acme\nlever:\n  - beta\nashby:\n  - gamma\n")
    return path


def test_ensure_curated_sources_is_idempotent_and_returns_same_instances(companies_yaml):
    reg = SourceRegistry()

    from app.sources.ats import build_ats_sources
    from app.sources.github_lists import internship_source, new_grad_source

    # Simulate what ensure_curated_sources does, but against a fixture path,
    # to avoid depending on the real companies.yaml contents in this test.
    reg._add(new_grad_source())
    reg._add(internship_source())
    for source in build_ats_sources(companies_yaml):
        reg._add(source)
    reg._curated_loaded = True

    first_call = reg.reliable_tier_sources()
    second_call = reg.reliable_tier_sources()

    assert len(first_call) == 5  # 2 github lists + 3 ATS companies
    # This is the actual regression this registry exists to prevent: two
    # calls a scheduler tick apart must return the *same* Source objects, so
    # check_for_changes() state (e.g. _last_count) survives between ticks —
    # see app.sources.registry docstring and the bug it replaced (sources
    # rebuilt fresh every fast_lane_cycle(), permanently defeating
    # check_for_changes()).
    assert [id(s) for s in first_call] == [id(s) for s in second_call]


@pytest.mark.asyncio
async def test_check_for_changes_state_persists_across_registry_calls(monkeypatch):
    """The concrete failure mode: without persistence, check_for_changes()
    always reports "changed" because _last_count resets to None every call."""
    from app.sources.ats.greenhouse import GreenhouseSource

    reg = SourceRegistry()
    reg._curated_loaded = True  # skip loading companies.yaml/github lists

    call_count = {"n": 0}

    async def fake_check(self):
        # First call: nothing cached yet, so it's "changed". Second call on
        # the SAME instance: nothing changed.
        call_count["n"] += 1
        if self._last_count is None:
            self._last_count = 10
            return True
        return False

    monkeypatch.setattr(GreenhouseSource, "check_for_changes", fake_check)

    source_first = reg.get_or_create_ats_source("greenhouse", "acme")
    changed_first = await source_first.check_for_changes()

    source_second = reg.get_or_create_ats_source("greenhouse", "acme")  # same board, later tick
    changed_second = await source_second.check_for_changes()

    assert source_first is source_second
    assert changed_first is True
    assert changed_second is False  # only true if state actually persisted


def test_get_or_create_ats_source_returns_same_instance_for_repeated_calls():
    reg = SourceRegistry()
    a = reg.get_or_create_ats_source("greenhouse", "acme")
    b = reg.get_or_create_ats_source("greenhouse", "acme")
    assert a is b


def test_get_or_create_ats_source_distinguishes_providers_and_slugs():
    reg = SourceRegistry()
    gh = reg.get_or_create_ats_source("greenhouse", "acme")
    lv = reg.get_or_create_ats_source("lever", "acme")
    other = reg.get_or_create_ats_source("greenhouse", "beta")
    assert len({id(gh), id(lv), id(other)}) == 3


def test_reset_clears_cached_instances():
    reg = SourceRegistry()
    a = reg.get_or_create_ats_source("greenhouse", "acme")
    reg.reset()
    b = reg.get_or_create_ats_source("greenhouse", "acme")
    assert a is not b
