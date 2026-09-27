#!/usr/bin/env python3
"""Tiny stdio client for the locally installed `codex app-server`.

This deliberately uses only Python's standard library. It keeps one
ephemeral Codex thread alive while it reads requests from stdin.
"""

from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class AppServerError(RuntimeError):
    """A protocol, startup, or turn failure reported to the CLI user."""


def log_event(event: str, **details: Any) -> None:
    """Write structured, human-readable lifecycle logs without polluting stdout."""
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    milliseconds = int((time.time() % 1) * 1000)
    suffix = " ".join(f"{name}={json.dumps(value, ensure_ascii=False)}" for name, value in details.items())
    print(f"{timestamp}.{milliseconds:03d} {event}" + (f" {suffix}" if suffix else ""), file=sys.stderr, flush=True)


def message(method: str, request_id: int | None = None, **params: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"method": method, "params": params}
    if request_id is not None:
        result["id"] = request_id
    return result


@dataclass
class AppServer:
    command: str
    cwd: Path
    timeout: float
    verbose: bool = False
    process: subprocess.Popen[str] | None = None
    inbox: queue.Queue[dict[str, Any] | BaseException | None] = field(default_factory=queue.Queue)
    deferred: list[dict[str, Any]] = field(default_factory=list)
    _reader: threading.Thread | None = None
    _stderr: threading.Thread | None = None
    _stderr_lines: list[str] = field(default_factory=list)

    def start(self) -> None:
        executable = shutil.which(self.command) or self.command
        log_event("app_server.starting", executable=executable, cwd=str(self.cwd))
        try:
            self.process = subprocess.Popen(
                [executable, "app-server", "--stdio"],
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise AppServerError(
                f"Cannot find {self.command!r}. Install Codex and ensure `codex` is on PATH."
            ) from exc
        assert self.process.stdout and self.process.stderr
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr.start()
        log_event("app_server.started", pid=self.process.pid)

    def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            for raw in self.process.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    self.inbox.put(json.loads(line))
                except json.JSONDecodeError as exc:
                    self.inbox.put(AppServerError(f"Invalid JSONL from app-server: {line[:300]!r} ({exc})"))
        finally:
            self.inbox.put(None)

    def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        for raw in self.process.stderr:
            line = raw.rstrip()
            if line:
                self._stderr_lines.append(line)
                if self.verbose:
                    log_event("app_server.stderr", line=line)

    def send(self, payload: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise AppServerError("app-server was not started")
        if self.process.poll() is not None:
            raise AppServerError(self._exit_error("app-server exited before accepting a request"))
        if self.verbose:
            log_event("jsonrpc.send", payload=payload)
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except BrokenPipeError as exc:
            raise AppServerError(self._exit_error("app-server closed its input")) from exc

    def _recv_wire(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            incoming = self.inbox.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if isinstance(incoming, BaseException):
            raise incoming
        if incoming is None:
            raise AppServerError(self._exit_error("app-server closed stdout"))
        if self.verbose:
            log_event("jsonrpc.receive", payload=incoming)
        return incoming

    def recv(self, deadline: float) -> dict[str, Any]:
        if self.deferred:
            return self.deferred.pop(0)
        return self._recv_wire(deadline)

    def request(self, payload: dict[str, Any], deadline: float) -> dict[str, Any]:
        request_id = payload["id"]
        self.send(payload)
        while True:
            # Read directly from the wire: returning a notification to `recv()`
            # here would immediately yield that same notification forever.
            incoming = self._recv_wire(deadline)
            if incoming.get("id") != request_id:
                # Notifications may arrive before their corresponding response.
                self.deferred.append(incoming)
                continue
            if "error" in incoming:
                error = incoming["error"]
                raise AppServerError(f"{payload['method']} failed ({error.get('code', '?')}): {error.get('message', error)}")
            return incoming.get("result", {})

    def _exit_error(self, prefix: str) -> str:
        code = None if not self.process else self.process.poll()
        tail = " | ".join(self._stderr_lines[-4:])
        return f"{prefix} (exit={code})." + (f" stderr: {tail}" if tail else "")

    def close(self) -> None:
        if not self.process:
            return
        log_event("app_server.stopping", pid=self.process.pid)
        if self.process.stdin:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        log_event("app_server.stopped", returncode=self.process.returncode)


def text_from_item(item: Any) -> str | None:
    """Read agent-message content from current or older protocol representations."""
    if not isinstance(item, dict) or item.get("type") != "agentMessage":
        return None
    text = item.get("text")
    if isinstance(text, str):
        return text
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "".join(part for part in parts if isinstance(part, str))
    return None


def complete_items_text(params: dict[str, Any]) -> str | None:
    turn = params.get("turn", {})
    texts = [text_from_item(item) for item in turn.get("items", [])]
    # A turn can contain progress-oriented agent messages as well as its final
    # reply.  The final item is the user-facing result expected on stdout.
    final = next((text for text in reversed(texts) if text), None)
    return final or None


def initialize(server: AppServer, deadline: float) -> dict[str, Any]:
    result = server.request(
        message(
            "initialize",
            1,
            clientInfo={"name": "alicegpt_app_server_poc", "title": "AliceGPT app-server PoC", "version": "0.1.0"},
        ),
        deadline,
    )
    server.send(message("initialized"))
    log_event("protocol.initialized", user_agent=result.get("userAgent"), server_info=result.get("serverInfo"))
    return result


def model_list(server: AppServer, deadline: float) -> dict[str, Any]:
    return server.request(message("model/list", 2), deadline)


def start_thread(
    server: AppServer, deadline: float, model: str | None, ephemeral: bool
) -> tuple[str, str, str | None]:
    params: dict[str, Any] = {"ephemeral": ephemeral}
    if model:
        params["model"] = model
    result = server.request({"method": "thread/start", "id": 3, "params": params}, deadline)
    thread_id = result.get("thread", {}).get("id")
    if not isinstance(thread_id, str):
        raise AppServerError(f"thread/start response did not contain thread.id: {result!r}")
    selected_model = result.get("model") or model or "unknown"
    if not isinstance(selected_model, str):
        selected_model = str(selected_model)
    current_effort = result.get("reasoningEffort")
    if current_effort is not None and not isinstance(current_effort, str):
        current_effort = str(current_effort)
    log_event("thread.started", thread_id=thread_id, ephemeral=ephemeral, model=selected_model, reasoning_effort=current_effort)
    return thread_id, selected_model, current_effort


def validate_effort(server: AppServer, deadline: float, model: str, effort: str) -> None:
    """Fail early rather than letting an unsupported model/effort turn hang."""
    catalog = model_list(server, deadline).get("data", [])
    entry = next(
        (item for item in catalog if isinstance(item, dict) and model in {item.get("id"), item.get("model")}),
        None,
    )
    if not isinstance(entry, dict):
        log_event("effort.validation_skipped", model=model, effort=effort, reason="model_not_in_catalog")
        return
    supported = [
        option.get("reasoningEffort")
        for option in entry.get("supportedReasoningEfforts", [])
        if isinstance(option, dict) and isinstance(option.get("reasoningEffort"), str)
    ]
    if effort not in supported:
        raise AppServerError(
            f"Effort {effort!r} is not supported by model {model!r}. Available: {', '.join(supported) or 'none reported'}."
        )
    log_event("effort.validated", model=model, effort=effort)


def run_turn(
    server: AppServer, deadline: float, request_id: int, thread_id: str, prompt: str, effort: str | None
) -> str:
    log_event("turn.starting", request_id=request_id, thread_id=thread_id, characters=len(prompt), effort=effort)
    params: dict[str, Any] = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]}
    if effort:
        params["effort"] = effort
    server.send({"method": "turn/start", "id": request_id, "params": params})
    deltas: list[str] = []
    completed_text: str | None = None
    while True:
        try:
            incoming = server.recv(deadline)
        except TimeoutError as exc:
            raise AppServerError(f"Timed out after waiting {server.timeout:g}s for turn/completed.") from exc
        if incoming.get("id") == request_id:
            if "error" in incoming:
                error = incoming["error"]
                raise AppServerError(f"turn/start failed ({error.get('code', '?')}): {error.get('message', error)}")
            log_event("turn.accepted", request_id=request_id, thread_id=thread_id)
            continue
        method = incoming.get("method")
        params = incoming.get("params", {})
        if params.get("threadId") != thread_id:
            continue
        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                deltas.append(delta)
                log_event("turn.delta", request_id=request_id, thread_id=thread_id, characters=len(delta))
        elif method == "turn/completed":
            completed_text = complete_items_text(params)
            status = params.get("turn", {}).get("status")
            if status != "completed":
                raise AppServerError(f"Turn ended with status {status!r}: {params.get('turn', {})!r}")
            answer = completed_text or "".join(deltas)
            log_event("turn.completed", request_id=request_id, thread_id=thread_id, characters=len(answer))
            return answer
        elif method:
            log_event("turn.notification", request_id=request_id, thread_id=thread_id, method=method)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one ephemeral Codex chat; read one user request per stdin line.")
    parser.add_argument("--codex", default="codex", help="Codex executable (default: codex).")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="Working directory exposed to the Codex thread.")
    parser.add_argument("--model", help="Optional model id. Omit to use the logged-in account's default.")
    parser.add_argument("--effort", help="Reasoning effort for every turn, such as low, medium, high, xhigh, max, or ultra.")
    parser.add_argument("--timeout", type=float, default=120, help="Startup and per-turn timeout in seconds (default: 120).")
    parser.add_argument("--persistent", action="store_true", help="Create a stored thread instead of the default ephemeral thread.")
    parser.add_argument("--list-models", action="store_true", help="Print model/list JSON and exit.")
    parser.add_argument("--print-thread-id", action="store_true", help="Write the ephemeral thread id to stderr.")
    parser.add_argument("--verbose", action="store_true", help="Also log complete JSON-RPC messages and app-server stderr.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.timeout <= 0:
        print("error: --timeout must be positive", file=sys.stderr)
        return 2
    cwd = args.cwd.expanduser().resolve()
    if not cwd.is_dir():
        print(f"error: --cwd is not a directory: {cwd}", file=sys.stderr)
        return 2
    server = AppServer(args.codex, cwd, args.timeout, args.verbose)
    try:
        server.start()
        initialize(server, time.monotonic() + args.timeout)
        if args.list_models:
            print(json.dumps(model_list(server, time.monotonic() + args.timeout), ensure_ascii=False, indent=2))
            return 0
        ephemeral = not args.persistent
        thread_id, selected_model, current_effort = start_thread(
            server, time.monotonic() + args.timeout, args.model, ephemeral
        )
        if args.effort:
            validate_effort(server, time.monotonic() + args.timeout, selected_model, args.effort)
        announced_effort = args.effort or current_effort or "default"
        if args.print_thread_id:
            log_event("thread.id", thread_id=thread_id, ephemeral=ephemeral)
        print("READY", flush=True)
        print(f"MODEL {selected_model}", flush=True)
        print(f"EFFORT {announced_effort}", flush=True)
        log_event("chat.ready", thread_id=thread_id, ephemeral=ephemeral, model=selected_model, effort=announced_effort)
        request_id = 10
        for raw_line in sys.stdin:
            prompt = raw_line.rstrip("\r\n")
            if not prompt:
                log_event("stdin.empty_line_ignored")
                continue
            log_event("stdin.request", request_id=request_id, characters=len(prompt))
            try:
                answer = run_turn(server, time.monotonic() + args.timeout, request_id, thread_id, prompt, args.effort)
            except AppServerError as exc:
                log_event("turn.failed", request_id=request_id, error=str(exc))
                print(f"ERROR {exc}", flush=True)
            else:
                print(answer, flush=True)
            request_id += 1
        log_event("stdin.eof")
        return 0
    except (AppServerError, TimeoutError) as exc:
        log_event("startup.failed", error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        log_event("chat.interrupted")
        return 130
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
