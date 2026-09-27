from __future__ import annotations

import hmac
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config
from .router import Router
from .yandex_dialogs import InvalidRequest, parse


def make_handler(config: Config, router: Router):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None: pass
        def _json(self, status: HTTPStatus, data: dict) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store, no-transform"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_GET(self) -> None:
            if self.path == "/health": self._json(HTTPStatus.OK, {"status": "ok"})
            else: self.send_error(HTTPStatus.NOT_FOUND)
        def do_POST(self) -> None:
            from .observability import event
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
            event("webhook.received", path_matches=self.path == config.webhook_path,
                  content_type=content_type, content_length=self.headers.get("Content-Length"))
            if self.path != config.webhook_path: self.send_error(HTTPStatus.NOT_FOUND); return
            if content_type != "application/json": self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE); return
            if config.shared_secret and not hmac.compare_digest(self.headers.get("X-AliceGPT-Secret", ""), config.shared_secret): self.send_error(HTTPStatus.UNAUTHORIZED); return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0 or length > config.max_body_bytes: raise InvalidRequest("invalid body size")
                payload = json.loads(self.rfile.read(length)); request = parse(payload, config.max_text_chars)
            except (ValueError, json.JSONDecodeError, InvalidRequest) as exc:
                keys = sorted(payload.keys()) if "payload" in locals() and isinstance(payload, dict) else []
                event("webhook.invalid", reason=str(exc), top_level_keys=keys)
                self.send_error(HTTPStatus.BAD_REQUEST); return
            event("webhook.accepted", characters=len(request.text))
            result = router.handle(request)
            event("webhook.responding")
            self._json(HTTPStatus.OK, result)
    return Handler


def serve(config: Config, router: Router) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((config.host, config.port), make_handler(config, router))
