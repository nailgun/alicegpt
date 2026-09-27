from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .observability import event
from .prompts import VOICE_RESPONSE_INSTRUCTIONS


class CodexError(RuntimeError): pass
class CodexTimeout(CodexError): pass


class CodexAdapter:
    """Small JSONL client for ``codex app-server --stdio``; no PoC import."""
    def __init__(self, command: str, cwd: Path, model: str | None = None, effort: str | None = None,
                 ephemeral: bool = False) -> None:
        self.command, self.cwd, self.model, self.effort, self.ephemeral = command, cwd, model, effort, ephemeral
        self.process: subprocess.Popen[str] | None = None
        self.inbox: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self.deferred: list[dict[str, Any]] = []
        self.ids = 10
        self.lock = threading.RLock()

    def start(self, timeout: float) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                event("codex.reused", pid=self.process.pid)
                return
            executable = shutil.which(self.command) or self.command
            event("codex.starting", executable=executable, timeout_seconds=timeout)
            try:
                self.process = subprocess.Popen([executable, "app-server", "--stdio"], cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            except FileNotFoundError as exc: raise CodexError("Codex CLI не найден или не авторизован.") from exc
            threading.Thread(target=self._read, daemon=True).start()
            self._request("initialize", {"clientInfo": {"name": "alicegpt-agent", "version": "0.1.0"}}, timeout)
            self._send({"method": "initialized", "params": {}})
            event("codex.initialized", pid=self.process.pid)

    def _read(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            try:
                item = json.loads(line)
                event("codex.received", method=item.get("method"), has_id="id" in item,
                      has_error="error" in item)
                self.inbox.put(item)
            except json.JSONDecodeError as exc: self.inbox.put(CodexError("Некорректный ответ Codex."))
        event("codex.stdout_closed")
        self.inbox.put(None)

    def _send(self, value: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin or self.process.poll() is not None: raise CodexError("Codex app-server остановлен.")
        event("codex.sending", method=value.get("method"), has_id="id" in value)
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n"); self.process.stdin.flush()

    def _wire_next(self, deadline: float) -> dict[str, Any]:
        """Read a new wire frame, never replaying deferred notifications."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            event("codex.deadline_elapsed")
            raise CodexTimeout("Codex не успел ответить.")
        try: item = self.inbox.get(timeout=remaining)
        except queue.Empty as exc:
            event("codex.deadline_elapsed")
            raise CodexTimeout("Codex не успел ответить.") from exc
        if item is None: raise CodexError("Codex app-server завершился.")
        if isinstance(item, BaseException): raise item
        return item

    def _next(self, deadline: float) -> dict[str, Any]:
        """Read notifications deferred while waiting for another RPC response."""
        if time.monotonic() >= deadline:
            event("codex.deadline_elapsed")
            raise CodexTimeout("Codex не успел ответить.")
        if self.deferred:
            return self.deferred.pop(0)
        return self._wire_next(deadline)

    def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.ids += 1; request_id = self.ids; self._send({"method": method, "id": request_id, "params": params}); deadline = time.monotonic() + timeout
        event("codex.request_started", method=method, timeout_seconds=timeout)
        while True:
            # A request must keep consuming *new* frames.  Re-reading one
            # unrelated notification from ``deferred`` would append it again
            # forever and starve the actual JSON-RPC response.
            item = self._wire_next(deadline)
            if item.get("id") != request_id: self.deferred.append(item); continue
            if "error" in item: raise CodexError(str(item["error"].get("message", "Codex error")))
            event("codex.request_completed", method=method)
            return item.get("result", {})

    def start_thread(self, timeout: float) -> str:
        result = self._request(
            "thread/start",
            {
                # Use the agent's dedicated non-repository directory. App-server
                # owns project assignment; it has no client-settable projectId.
                "cwd": str(self.cwd),
                "ephemeral": self.ephemeral,
                "developerInstructions": VOICE_RESPONSE_INSTRUCTIONS,
                **({"model": self.model} if self.model else {}),
            },
            timeout,
        )
        thread_id = result.get("thread", {}).get("id")
        if not isinstance(thread_id, str): raise CodexError("Codex не вернул идентификатор разговора.")
        event("codex.thread_started")
        return thread_id

    def run_turn(self, thread_id: str, prompt: str, timeout: float) -> str:
        with self.lock:
            self.ids += 1; request_id = self.ids
            params: dict[str, Any] = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]}
            if self.effort: params["effort"] = self.effort
            self._send({"method": "turn/start", "id": request_id, "params": params}); deadline = time.monotonic() + timeout; deltas: list[str] = []
            event("codex.turn_started", timeout_seconds=timeout)
            while True:
                item = self._next(deadline)
                if item.get("id") == request_id:
                    if "error" in item: raise CodexError(str(item["error"].get("message", "Codex error")))
                    event("codex.turn_accepted")
                    continue
                params = item.get("params", {})
                if params.get("threadId") != thread_id:
                    event("codex.notification_ignored", method=item.get("method"), reason="other_thread_or_missing_thread")
                    continue
                if item.get("method") == "item/agentMessage/delta" and isinstance(params.get("delta"), str):
                    deltas.append(params["delta"]); event("codex.turn_delta", characters=len(params["delta"]))
                if item.get("method") == "turn/completed":
                    turn = params.get("turn", {})
                    if turn.get("status") != "completed": raise CodexError("Codex не завершил ответ.")
                    texts = [x.get("text") for x in turn.get("items", []) if isinstance(x, dict) and x.get("type") == "agentMessage" and isinstance(x.get("text"), str)]
                    answer = (texts[-1] if texts else "".join(deltas)).strip()
                    event("codex.turn_completed", characters=len(answer))
                    return answer

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            event("codex.stopping", pid=self.process.pid)
            self.process.terminate()
