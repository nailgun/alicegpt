#!/usr/bin/env python3
"""Minimal Slack Socket Mode gateway for the Alice → ChatGPT Work PoC."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

REQUEST_RE = re.compile(r"^\[REQ:([0-9a-fA-F-]{36})\](?:\r?\n|$)")
REQUIRED_ENV = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CHANNEL_ID")


def log(event: str, **fields: Any) -> None:
    """Emit one JSON object per line so the PoC can be inspected or parsed."""
    print(
        json.dumps(
            {"event": event, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields},
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )


def require_environment() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Copy .env.example, export real values in your shell, then retry."
        )
    return {name: os.environ[name] for name in REQUIRED_ENV}


@dataclass
class PendingRequest:
    request_id: str
    text: str
    sent_monotonic: float
    thread_ts: str | None = None
    reply_text: str | None = None
    reply_ts: str | None = None
    latency_ms: float | None = None


class Gateway:
    def __init__(self, bot_token: str, app_token: str, channel_id: str) -> None:
        # Keep --help and missing-env diagnostics useful before dependencies are installed.
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing dependency slack-bolt. Create a virtual environment and run "
                "'pip install -r requirements.txt'."
            ) from exc
        self.channel_id = channel_id
        self.app = App(token=bot_token)
        self.handler = SocketModeHandler(self.app, app_token)
        self.pending_by_id: dict[str, PendingRequest] = {}
        self.pending_by_thread: dict[str, PendingRequest] = {}
        self.reply_received = threading.Event()
        self.bot_user_id: str | None = None
        self.bot_id: str | None = None
        self.app.event("message")(self.handle_message)

    def identify_bot(self) -> None:
        identity = self.app.client.auth_test()
        self.bot_user_id = identity.get("user_id")
        self.bot_id = identity.get("bot_id")
        log("READY", channel_id=self.channel_id, bot_user_id=self.bot_user_id, bot_id=self.bot_id)

    def handle_message(self, event: dict[str, Any], logger: Any) -> None:
        """Accept only a correlated thread reply from someone other than this gateway bot."""
        if event.get("channel") != self.channel_id:
            return

        if event.get("subtype"):
            log("IGNORED_EVENT", reason="message_subtype", subtype=event.get("subtype"))
            return

        if event.get("user") == self.bot_user_id or (
            self.bot_id is not None and event.get("bot_id") == self.bot_id
        ):
            log("IGNORED_EVENT", reason="own_message", ts=event.get("ts"))
            return

        thread_ts = event.get("thread_ts")
        if not thread_ts:
            log("IGNORED_EVENT", reason="top_level_not_a_reply", ts=event.get("ts"))
            return

        text = event.get("text") or ""
        match = REQUEST_RE.match(text)
        if not match:
            log("IGNORED_EVENT", reason="reply_without_request_marker", thread_ts=thread_ts)
            return

        request_id = match.group(1).lower()
        request = self.pending_by_id.get(request_id)
        if request is None:
            log("UNKNOWN_REPLY", reason="unknown_request_id", request_id=request_id, thread_ts=thread_ts)
            return
        if request.thread_ts != thread_ts:
            log(
                "UNKNOWN_REPLY",
                reason="thread_mismatch",
                request_id=request_id,
                expected_thread_ts=request.thread_ts,
                received_thread_ts=thread_ts,
            )
            return
        if request.reply_text is not None:
            log("IGNORED_EVENT", reason="duplicate_reply", request_id=request_id, thread_ts=thread_ts)
            return

        request.reply_text = text
        request.reply_ts = event.get("ts")
        request.latency_ms = round((time.monotonic() - request.sent_monotonic) * 1000, 1)
        log(
            "REPLY",
            request_id=request_id,
            thread_ts=thread_ts,
            reply_ts=request.reply_ts,
            latency_ms=request.latency_ms,
            text=text,
        )
        self.reply_received.set()

    def send(self, text: str) -> PendingRequest:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Request text must not be empty.")
        request_id = str(uuid.uuid4())
        request = PendingRequest(request_id=request_id, text=clean_text, sent_monotonic=time.monotonic())
        self.pending_by_id[request_id] = request
        message = f"[REQ:{request_id}]\n{clean_text}"

        try:
            response = self.app.client.chat_postMessage(channel=self.channel_id, text=message)
        except Exception:
            self.pending_by_id.pop(request_id, None)
            raise

        request.thread_ts = response["ts"]
        self.pending_by_thread[request.thread_ts] = request
        log("SEND", request_id=request_id, channel_id=self.channel_id, thread_ts=request.thread_ts, text=clean_text)
        return request

    def connect(self) -> None:
        self.identify_bot()
        self.handler.connect()
        log("SOCKET_CONNECTED", channel_id=self.channel_id)

    def close(self) -> None:
        try:
            self.handler.close()
        except Exception as exc:  # best-effort cleanup after a test run
            log("CLOSE_WARNING", error=str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alice → ChatGPT Work Slack round-trip PoC")
    subcommands = parser.add_subparsers(dest="command", required=True)

    send = subcommands.add_parser("send", help="Post one request and wait for its correlated thread reply")
    send.add_argument("text", help="Russian-language user request to send")
    send.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="seconds to wait for a thread reply (default: 90)",
    )
    subcommands.add_parser("listen", help="Keep Socket Mode connected; stop with Ctrl-C")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        env = require_environment()
        gateway = Gateway(env["SLACK_BOT_TOKEN"], env["SLACK_APP_TOKEN"], env["SLACK_CHANNEL_ID"])
        gateway.connect()

        if args.command == "listen":
            log("LISTENING", channel_id=env["SLACK_CHANNEL_ID"])
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                log("STOPPED", reason="keyboard_interrupt")
            return 0

        request = gateway.send(args.text)
        if not gateway.reply_received.wait(timeout=args.timeout):
            log("TIMEOUT", request_id=request.request_id, thread_ts=request.thread_ts, timeout_seconds=args.timeout)
            return 2
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: Slack PoC failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if "gateway" in locals():
            gateway.close()


if __name__ == "__main__":
    raise SystemExit(main())
