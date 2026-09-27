from __future__ import annotations

import hashlib
import threading
from collections import defaultdict
from dataclasses import dataclass, field

from .codex_adapter import CodexAdapter, CodexError, CodexTimeout
from .observability import event
from .thread_store import ThreadStore
from .yandex_dialogs import DialogRequest, response


ACKNOWLEDGEMENT = "Задача принята. Спроси меня через несколько секунд."
PENDING_STATUS = "Я ещё работаю над предыдущим вопросом. Спроси меня через несколько секунд."
STATUS_PHRASES = {"ну что", "что там", "готово", "есть ответ", "ну как", "как там"}


@dataclass
class PendingTurn:
    done: threading.Event = field(default_factory=threading.Event)
    answer: str | None = None
    error: str | None = None
    cancelled: bool = False


class Router:
    def __init__(self, store: ThreadStore, adapter: CodexAdapter, salt: str, timeout: float,
                 response_chars: int, session_idle_seconds: float = 3600,
                 quick_ack_seconds: float = 3) -> None:
        self.store, self.adapter, self.salt = store, adapter, salt
        self.timeout, self.response_chars = timeout, response_chars
        self.session_idle_seconds, self.quick_ack_seconds = session_idle_seconds, quick_ack_seconds
        self.locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self.pending: dict[str, PendingTurn] = {}

    def route_key(self) -> str:
        return hashlib.sha256(f"{self.salt}:shared-session".encode()).hexdigest()

    def warm_up(self) -> None:
        route = self.route_key()
        with self.locks[route]:
            try:
                event("codex.warmup_started")
                self.adapter.start(self.timeout)
                if not self.store.active_thread(route, self.session_idle_seconds):
                    self.store.set_thread(route, self.adapter.start_thread(self.timeout))
                event("codex.warmup_completed")
            except CodexError:
                event("codex.warmup_failed")

    @staticmethod
    def _is_status_question(text: str) -> bool:
        normalized = " ".join("".join(char if char.isalnum() or char.isspace() else " " for char in text.lower()).split())
        return normalized in STATUS_PHRASES

    def _cancel_pending(self, route: str) -> None:
        pending = self.pending.pop(route, None)
        if pending and not pending.done.is_set():
            pending.cancelled = True
            self.adapter.close()
            event("webhook.pending_cancelled", route=route[:12])

    def _run_pending(self, route: str, pending: PendingTurn, thread: str, prompt: str) -> None:
        try:
            answer = self.adapter.run_turn(thread, prompt, self.timeout)
            self.store.touch(route)
            pending.answer = answer[:self.response_chars]
            event("webhook.pending_completed", route=route[:12], characters=len(pending.answer))
        except CodexTimeout:
            self.adapter.close()
            self.store.forget(route)
            pending.error = "timeout"
            event("webhook.pending_timeout", route=route[:12])
        except CodexError:
            self.store.forget(route)
            pending.error = "error"
            event("webhook.pending_error", route=route[:12])
        finally:
            pending.done.set()

    def _pending_response(self, route: str, pending: PendingTurn, status_question: bool) -> dict | None:
        if not pending.done.is_set():
            return response(PENDING_STATUS)
        self.pending.pop(route, None)
        if pending.cancelled:
            return None
        if pending.answer:
            event("webhook.pending_delivered", route=route[:12], status_question=status_question)
            return response(pending.answer)
        return response("Не удалось завершить предыдущий ответ. Спросите ещё раз.")

    def handle(self, request: DialogRequest) -> dict:
        route = self.route_key()
        with self.locks[route]:
            text = request.text.lower().strip()
            event("webhook.routing", route=route[:12], characters=len(request.text))
            if text in {"новый разговор", "начать заново"}:
                self._cancel_pending(route)
                self.store.forget(route)
                return response("Начинаем новый разговор.")
            if text in {"удали историю", "забудь разговор"}:
                self._cancel_pending(route)
                self.store.forget(route)
                return response("Локальная связь с разговором удалена.")
            if not request.text:
                return response("Что вы хотите спросить?")

            existing = self.pending.get(route)
            if existing:
                pending_result = self._pending_response(route, existing, self._is_status_question(request.text))
                if pending_result:
                    return pending_result

            try:
                self.adapter.start(self.timeout)
                thread = self.store.active_thread(route, self.session_idle_seconds)
                if not thread:
                    thread = self.adapter.start_thread(self.timeout)
                    self.store.set_thread(route, thread)
                pending = PendingTurn()
                self.pending[route] = pending
                threading.Thread(target=self._run_pending, args=(route, pending, thread, request.text), name="codex-turn", daemon=True).start()
                if pending.done.wait(self.quick_ack_seconds):
                    return self._pending_response(route, pending, False) or response("Спросите ещё раз.")
                event("webhook.quick_ack", route=route[:12], wait_seconds=self.quick_ack_seconds)
                return response(ACKNOWLEDGEMENT)
            except CodexError:
                event("turn.error", route=route[:12])
                self.store.forget(route)
                return response("Сейчас не могу связаться с Codex. Попробуйте ещё раз.")
