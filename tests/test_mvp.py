import json
import tempfile
import time
import unittest
from pathlib import Path

from alicegpt_agent.codex_adapter import CodexAdapter, CodexTimeout
from alicegpt_agent.prompts import VOICE_RESPONSE_INSTRUCTIONS
from alicegpt_agent.router import ACKNOWLEDGEMENT, PENDING_STATUS, Router
from alicegpt_agent.thread_store import ThreadStore
from alicegpt_agent.yandex_dialogs import InvalidRequest, parse


def fixture(message=1, text="привет"):
    return {"version": "1.0", "meta": {"locale": "ru-RU"}, "session": {"session_id": "s1", "message_id": message, "user": {"user_id": "u1"}, "application": {"application_id": "a1"}}, "request": {"type": "SimpleUtterance", "original_utterance": text}}


class FakeAdapter:
    def __init__(self): self.turns = 0
    def start(self, timeout): pass
    def start_thread(self, timeout): return "thread-1"
    def run_turn(self, thread, prompt, timeout): self.turns += 1; return "Ответ Codex"


class SlowAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        import threading
        self.started = threading.Event()
        self.release = threading.Event()

    def run_turn(self, thread, prompt, timeout):
        self.turns += 1; self.started.set(); self.release.wait(1); return "Готовый ответ"


class MVPTests(unittest.TestCase):
    def test_voice_instruction_is_present(self):
        self.assertIn("700–800", VOICE_RESPONSE_INSTRUCTIONS)
        self.assertIn("устной речи", VOICE_RESPONSE_INSTRUCTIONS)

    def test_adapter_honors_deadline_even_when_notification_is_queued(self):
        adapter = CodexAdapter("codex", Path.cwd())
        self.assertFalse(adapter.ephemeral)
        adapter.inbox.put({"method": "item/agentMessage/delta", "params": {}})
        with self.assertRaises(CodexTimeout):
            adapter._next(time.monotonic() - 0.01)

    def test_request_reads_past_notifications_to_its_response(self):
        adapter = CodexAdapter("codex", Path.cwd())
        adapter._send = lambda payload: None  # type: ignore[method-assign]
        adapter.inbox.put({"method": "mcpServer/startupStatus/updated", "params": {}})
        adapter.inbox.put({"id": 11, "result": {"ok": True}})
        self.assertEqual(adapter._request("initialize", {}, 1), {"ok": True})
        self.assertEqual(adapter.deferred[0]["method"], "mcpServer/startupStatus/updated")

    def test_thread_starts_without_a_project(self):
        adapter = CodexAdapter("codex", Path.cwd())
        captured = {}
        adapter._request = lambda method, params, timeout: (captured.update(method=method, params=params) or {"thread": {"id": "thread-1"}})  # type: ignore[method-assign]
        self.assertEqual(adapter.start_thread(1), "thread-1")
        self.assertEqual(captured["method"], "thread/start")
        self.assertEqual(captured["params"]["cwd"], str(Path.cwd()))

    def test_rejects_bad_shape(self):
        with self.assertRaises(InvalidRequest): parse({"meta": {}}, 100)

    def test_every_request_runs_and_new_conversation_clears_shared_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter(); store = ThreadStore(Path(directory) / "state.sqlite")
            router = Router(store, adapter, "test-salt", 1, 100)
            request = parse(fixture(), 100)
            self.assertEqual(router.handle(request)["response"]["text"], "Ответ Codex")
            self.assertEqual(router.handle(request)["response"]["text"], "Ответ Codex")
            self.assertEqual(adapter.turns, 2)
            router.handle(parse(fixture(2, "новый разговор"), 100))
            self.assertIsNone(store.active_thread(router.route_key(), 3600))

    def test_all_requests_share_one_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = FakeAdapter(); store = ThreadStore(Path(directory) / "state.sqlite")
            router = Router(store, adapter, "test-salt", 1, 100)
            first = parse(fixture(1, "первая фраза"), 100)
            second_payload = fixture(1, "вторая фраза")
            second_payload["session"]["session_id"] = "s2"
            second = parse(second_payload, 100)
            router.handle(first)
            router.handle(second)
            self.assertEqual(adapter.turns, 2)
            self.assertEqual(store.active_thread(router.route_key(), 3600), "thread-1")

    def test_idle_thread_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ThreadStore(Path(directory) / "state.sqlite")
            store.set_thread("shared", "thread-1")
            with store.connection:
                store.connection.execute("UPDATE routes SET updated_at=0 WHERE route_key='shared'")
            self.assertIsNone(store.active_thread("shared", 3600))

    def test_slow_turn_acknowledges_then_status_returns_completed_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = SlowAdapter(); store = ThreadStore(Path(directory) / "state.sqlite")
            router = Router(store, adapter, "test-salt", 1, 100, quick_ack_seconds=0.01)
            first = router.handle(parse(fixture(1, "долгая задача"), 100))
            self.assertEqual(first["response"]["text"], ACKNOWLEDGEMENT)
            self.assertTrue(adapter.started.is_set())
            waiting = router.handle(parse(fixture(2, "Ну что?"), 100))
            self.assertEqual(waiting["response"]["text"], PENDING_STATUS)
            adapter.release.set()
            self.assertTrue(router.pending[router.route_key()].done.wait(1))
            ready = router.handle(parse(fixture(3, "Ну что?"), 100))
            self.assertEqual(ready["response"]["text"], "Готовый ответ")
