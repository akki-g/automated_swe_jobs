import pytest

from app.assistant.agent import run_assistant
from app.watchlist import service as watchlist_service


class ScriptedAgentClient:
    """Replays a fixed sequence of API responses, one per create_message call."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = responses
        self.calls = 0

    async def create_message(self, *, system, messages, tools):
        response = self._responses[self.calls]
        self.calls += 1
        return response


class FailingAgentClient:
    async def create_message(self, *, system, messages, tools):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_run_assistant_returns_direct_text_when_no_tool_use(db_session, demo_user):
    client = ScriptedAgentClient(
        [{"content": [{"type": "text", "text": "Sure, what role are you looking for?"}]}]
    )

    reply = await run_assistant(db_session, demo_user, "hi", [], client)

    assert reply == "Sure, what role are you looking for?"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_run_assistant_executes_tool_then_returns_final_text(db_session, demo_user):
    client = ScriptedAgentClient(
        [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "update_criteria",
                        "input": {"locations": ["remote"]},
                    }
                ]
            },
            {"content": [{"type": "text", "text": "Updated your locations to remote."}]},
        ]
    )

    reply = await run_assistant(db_session, demo_user, "only remote jobs please", [], client)

    assert reply == "Updated your locations to remote."
    assert client.calls == 2


@pytest.mark.asyncio
async def test_run_assistant_gives_up_after_max_turns(db_session, demo_user):
    tool_use_response = {
        "content": [
            {"type": "tool_use", "id": "tool_1", "name": "get_criteria", "input": {}}
        ]
    }
    client = ScriptedAgentClient([tool_use_response] * 10)

    reply = await run_assistant(db_session, demo_user, "hi", [], client)

    assert "took too long" in reply.lower()


@pytest.mark.asyncio
async def test_run_assistant_handles_llm_failure_gracefully(db_session, demo_user):
    reply = await run_assistant(db_session, demo_user, "hi", [], FailingAgentClient())
    assert "trouble" in reply.lower()


@pytest.mark.asyncio
async def test_run_assistant_truncates_long_reply_to_sms_length(db_session, demo_user):
    long_text = "x" * 500
    client = ScriptedAgentClient([{"content": [{"type": "text", "text": long_text}]}])

    reply = await run_assistant(db_session, demo_user, "hi", [], client)

    assert len(reply) == 320


@pytest.mark.asyncio
async def test_run_assistant_survives_unexpected_tool_arguments(db_session, demo_user):
    """A hallucinated/malformed tool call (extra key search_postings doesn't
    accept) must come back as a tool_result error, not crash the loop."""
    client = ScriptedAgentClient(
        [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "search_postings",
                        "input": {"role_type": "new_grad", "totally_made_up_field": "x"},
                    }
                ]
            },
            {"content": [{"type": "text", "text": "Here's what I found."}]},
        ]
    )

    reply = await run_assistant(db_session, demo_user, "any new grad roles?", [], client)

    assert reply == "Here's what I found."
    assert client.calls == 2


@pytest.mark.asyncio
async def test_run_assistant_survives_unknown_tool_name(db_session, demo_user):
    client = ScriptedAgentClient(
        [
            {"content": [{"type": "tool_use", "id": "tool_1", "name": "delete_everything", "input": {}}]},
            {"content": [{"type": "text", "text": "Can't do that, but happy to help otherwise."}]},
        ]
    )

    reply = await run_assistant(db_session, demo_user, "hi", [], client)

    assert "happy to help" in reply


@pytest.mark.asyncio
async def test_run_assistant_survives_tool_use_block_missing_input(db_session, demo_user):
    client = ScriptedAgentClient(
        [
            {"content": [{"type": "tool_use", "id": "tool_1", "name": "get_criteria"}]},
            {"content": [{"type": "text", "text": "Got your criteria."}]},
        ]
    )

    reply = await run_assistant(db_session, demo_user, "what are my criteria?", [], client)

    assert reply == "Got your criteria."


@pytest.mark.asyncio
async def test_run_assistant_adds_company_mentioned_in_conversation_to_watchlist(
    db_session, demo_user, monkeypatch
):
    """End-to-end: when the model recognizes a company mention and calls
    add_watchlist_company, the tool actually persists it (see spec addendum:
    Company watchlist)."""
    monkeypatch.setattr(watchlist_service, "detect_ats_board", lambda name: _resolved(("greenhouse", "stripe")))

    client = ScriptedAgentClient(
        [
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "add_watchlist_company",
                        "input": {"company_name": "Stripe"},
                    }
                ]
            },
            {"content": [{"type": "text", "text": "Got it, I'll watch Stripe for you."}]},
        ]
    )

    reply = await run_assistant(
        db_session, demo_user, "let me know if Stripe posts anything", [], client
    )

    assert "watch Stripe" in reply
    watched = await watchlist_service.list_companies(db_session, demo_user.id)
    assert watched == [{"company_name": "Stripe", "status": "active", "ats_provider": "greenhouse"}]


async def _resolved(value):
    return value


@pytest.mark.asyncio
async def test_run_assistant_survives_malformed_response_shape(db_session, demo_user):
    """A response whose "content" isn't the expected list-of-blocks shape
    must fall back to the generic error reply rather than raising an
    unhandled exception out of run_assistant (e.g. into the webhook)."""
    client = ScriptedAgentClient([{"content": "not-a-list-of-blocks"}])

    reply = await run_assistant(db_session, demo_user, "hi", [], client)

    assert "trouble" in reply.lower()
