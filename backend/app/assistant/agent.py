from __future__ import annotations

import json
import logging
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.tools import (
    TOOL_SCHEMAS,
    add_watchlist_company,
    get_criteria,
    list_watchlist,
    remove_watchlist_company,
    search_postings,
    update_criteria,
)
from app.config import settings
from app.db.models import User

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a job-search assistant reachable over SMS. The user is looking for "
    "new-grad or internship roles in one or more career fields. You can look up their "
    "saved criteria, update it based on what they ask, and search stored "
    "postings. Always reply in plain text short enough for a single SMS "
    "(under 320 characters) — no markdown, no long lists unless asked."
)

MAX_TOOL_TURNS = 4


class AgentClient(Protocol):
    async def create_message(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        ...


class AnthropicAgentClient:
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5") -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model

    async def create_message(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=messages,
            tools=tools,
        )
        return response.model_dump()


async def _execute_tool(session: AsyncSession, user: User, name: str, tool_input: dict) -> dict | list:
    """Dispatch one tool call. tool_input comes from the LLM and is not
    trusted — a hallucinated/malformed call (wrong types, unexpected keys,
    an unknown tool name) must come back as a tool_result error the model
    can react to, never an unhandled exception that would 500 the whole
    webhook (see spec addendum: malformed tool-use handling)."""
    if not isinstance(tool_input, dict):
        return {"error": "tool input must be an object"}
    try:
        if name == "search_postings":
            return await search_postings(session, **tool_input)
        if name == "get_criteria":
            return await get_criteria(session, user.id)
        if name == "update_criteria":
            return await update_criteria(session, user.id, tool_input)
        if name == "add_watchlist_company":
            return await add_watchlist_company(session, user.id, **tool_input)
        if name == "remove_watchlist_company":
            return await remove_watchlist_company(session, user.id, **tool_input)
        if name == "list_watchlist":
            return await list_watchlist(session, user.id)
        return {"error": f"unknown tool {name}"}
    except TypeError as exc:
        logger.warning("tool call %s: bad arguments %r: %s", name, tool_input, exc)
        return {"error": f"invalid arguments for {name}"}
    except Exception:  # noqa: BLE001 - a tool bug must not crash the webhook
        logger.warning("tool call %s: execution failed", name, exc_info=True)
        return {"error": f"{name} failed"}


def _extract_text(content: list[dict]) -> str:
    return " ".join(block.get("text", "") for block in content if block.get("type") == "text").strip()


async def run_assistant(
    session: AsyncSession,
    user: User,
    message_text: str,
    recent_messages: list[dict],
    client: AgentClient,
) -> str:
    """Runs the Claude tool-use loop for one inbound SMS and returns the reply
    text (see spec: Assistant & tools)."""
    messages = [*recent_messages, {"role": "user", "content": message_text}]

    for _ in range(MAX_TOOL_TURNS):
        try:
            response = await client.create_message(
                system=SYSTEM_PROMPT, messages=messages, tools=TOOL_SCHEMAS
            )
        except Exception:  # noqa: BLE001 - never let an LLM outage break the webhook
            logger.warning("assistant: LLM call failed for user_id=%s", user.id, exc_info=True)
            return "Sorry, I'm having trouble right now — try again shortly."

        try:
            content = response.get("content", [])
            tool_uses = [b for b in content if b.get("type") == "tool_use"]

            if not tool_uses:
                text = _extract_text(content)
                return text[:320] if text else "Got it."

            messages.append({"role": "assistant", "content": content})
            tool_results = []
            for tool_use in tool_uses:
                result = await _execute_tool(
                    session, user, tool_use.get("name", ""), tool_use.get("input", {})
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.get("id", ""),
                        "content": json.dumps(result, default=str),
                    }
                )
            messages.append({"role": "user", "content": tool_results})
        except Exception:  # noqa: BLE001 - an unexpected response shape must not 500 the webhook
            logger.warning("assistant: malformed LLM response for user_id=%s", user.id, exc_info=True)
            return "Sorry, I'm having trouble right now — try again shortly."

    return "Sorry, that took too long to process — try rephrasing?"
