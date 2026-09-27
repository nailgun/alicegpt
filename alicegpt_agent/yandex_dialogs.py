from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class InvalidRequest(ValueError):
    pass


@dataclass(frozen=True)
class DialogRequest:
    session_id: str
    message_id: int | str
    user_id: str | None
    application_id: str | None
    text: str
    is_new: bool


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidRequest(f"missing {name}")
    return value


def parse(payload: Any, max_text_chars: int) -> DialogRequest:
    """Validate the stable core of the Yandex Dialogs webhook shape.

    The platform has historically used ``session.message_id`` and
    ``request.original_utterance``. Unknown request types are deliberately not
    treated as conversational input.
    """
    if not isinstance(payload, dict):
        raise InvalidRequest("JSON object expected")
    meta, session, request = payload.get("meta"), payload.get("session"), payload.get("request")
    if not all(isinstance(x, dict) for x in (meta, session, request)):
        raise InvalidRequest("meta, session and request are required objects")
    # Protocol version is a top-level property; ``meta`` describes the client.
    if payload.get("version") != "1.0":
        raise InvalidRequest("unsupported protocol version")
    if request.get("type", "SimpleUtterance") != "SimpleUtterance":
        raise InvalidRequest("unsupported request.type")
    message_id = session.get("message_id")
    if not isinstance(message_id, (str, int)):
        raise InvalidRequest("missing session.message_id")
    text = request.get("original_utterance", request.get("command", ""))
    if not isinstance(text, str):
        raise InvalidRequest("request utterance must be a string")
    text = text.strip()
    if len(text) > max_text_chars:
        raise InvalidRequest("utterance too long")
    user = session.get("user") if isinstance(session.get("user"), dict) else {}
    application = session.get("application") if isinstance(session.get("application"), dict) else {}
    return DialogRequest(_string(session.get("session_id"), "session.session_id"), message_id,
                         user.get("user_id") if isinstance(user.get("user_id"), str) else None,
                         application.get("application_id") if isinstance(application.get("application_id"), str) else None,
                         text, bool(session.get("new")))


def response(text: str, end_session: bool = False) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", text.replace("`", "")).strip()
    return {"response": {"text": text or "Повторите, пожалуйста.", "tts": text or "Повторите, пожалуйста.", "end_session": end_session}, "version": "1.0"}


def error_response() -> dict[str, Any]:
    return response("Не удалось обработать запрос. Попробуйте ещё раз.")
