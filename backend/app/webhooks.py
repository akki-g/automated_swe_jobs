from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.assistant.agent import AnthropicAgentClient, run_assistant
from app.config import settings
from app.db.models import Criteria as CriteriaRow
from app.db.models import Message as MessageRow
from app.db.models import User
from app.db.session import session_scope
from app.notify.sms.signalwire import verify_signature

logger = logging.getLogger(__name__)

router = APIRouter()

HELP_TEXT = (
    "Job alert assistant. Reply STOP to unsubscribe, START to resubscribe, "
    "or ask about your criteria (e.g. 'add remote to my locations')."
)
RECENT_MESSAGE_LIMIT = 6


def _laml_response(body: str) -> Response:
    escaped = (
        body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    xml = f"<?xml version='1.0' encoding='UTF-8'?><Response><Message>{escaped}</Message></Response>"
    return Response(content=xml, media_type="application/xml")


async def _load_user_by_phone(session, phone: str) -> User | None:
    return (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()


async def _recent_messages_for(session, user_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(MessageRow)
            .where(MessageRow.user_id == user_id)
            .order_by(MessageRow.created_at.desc())
            .limit(RECENT_MESSAGE_LIMIT)
        )
    ).scalars().all()
    ordered = list(reversed(rows))
    return [
        {"role": "user" if row.direction == "inbound" else "assistant", "content": row.body}
        for row in ordered
    ]


async def _log_message(session, user_id: int, direction: str, body: str) -> None:
    session.add(
        MessageRow(
            user_id=user_id,
            match_id=None,
            direction=direction,
            channel="sms",
            provider="signalwire",
            body=body,
            status="received" if direction == "inbound" else "sent",
            created_at=datetime.now(UTC),
        )
    )


@router.post("/api/v1/webhooks/signalwire")
async def signalwire_webhook(request: Request) -> Response:
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    body_text = params.get("Body", "").strip()
    from_number = params.get("From", "")

    if not settings.signalwire_allow_unsigned_webhooks:
        signature = request.headers.get("X-Twilio-Signature") or request.headers.get(
            "X-SignalWire-Signature", ""
        )
        if not verify_signature(str(request.url), params, signature, settings.signalwire_api_token):
            logger.warning("signalwire webhook: signature verification failed")
            return Response(status_code=403)

    command = body_text.upper()

    async with session_scope() as session:
        user = await _load_user_by_phone(session, from_number)
        if user is None:
            logger.info("signalwire webhook: unknown number %s", from_number)
            return _laml_response("We don't recognize this number.")

        if command == "STOP":
            user.opted_out = True
            reply = "You've been unsubscribed from job alerts. Reply START to resume."
        elif command == "START":
            user.opted_out = False
            reply = "You're resubscribed to job alerts."
        elif command == "HELP":
            reply = HELP_TEXT
        else:
            recent_messages = await _recent_messages_for(session, user.id)
            client = AnthropicAgentClient()
            try:
                reply = await run_assistant(session, user, body_text, recent_messages, client)
            except Exception:  # noqa: BLE001 - belt-and-suspenders: run_assistant already
                # catches LLM/tool failures internally, but a reply must go out no matter what.
                logger.exception("webhook: run_assistant raised unexpectedly for user_id=%s", user.id)
                reply = "Sorry, I'm having trouble right now — try again shortly."

        await _log_message(session, user.id, "inbound", body_text)
        await _log_message(session, user.id, "outbound", reply)
        await session.commit()

    return _laml_response(reply)
