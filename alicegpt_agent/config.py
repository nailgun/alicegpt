from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8080
    database: Path = Path(".alicegpt/agent.sqlite3")
    codex: str = "codex"
    cwd: Path = Path("/private/tmp")
    webhook_timeout: float = 25.0
    codex_timeout: float = 30.0
    max_body_bytes: int = 64 * 1024
    max_text_chars: int = 4_000
    response_chars: int = 1_500
    session_idle_seconds: float = 3600.0
    quick_ack_seconds: float = 3.0
    ephemeral: bool = False
    shared_secret: str | None = None
    model: str | None = None
    effort: str | None = None
    webhook_path: str = "/alice/webhook"

    @classmethod
    def from_env(cls) -> "Config":
        get = os.environ.get
        def boolean(name: str, default: bool) -> bool:
            value = get(name, str(default)).strip().lower()
            if value in {"1", "true", "yes", "on"}:
                return True
            if value in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{name} must be true or false")
        database = Path(get("ALICEGPT_DATABASE", ".alicegpt/agent.sqlite3")).expanduser()
        cwd = Path(get("ALICEGPT_CWD", "/private/tmp")).expanduser().resolve()
        config = cls(
            port=int(get("ALICEGPT_PORT", "8080")), database=database, codex=get("ALICEGPT_CODEX", "codex"),
            cwd=cwd, webhook_timeout=float(get("ALICEGPT_WEBHOOK_TIMEOUT", "25")),
            codex_timeout=float(get("ALICEGPT_CODEX_TIMEOUT", "30")),
            max_body_bytes=int(get("ALICEGPT_MAX_BODY_BYTES", str(64 * 1024))),
            max_text_chars=int(get("ALICEGPT_MAX_TEXT_CHARS", "4000")),
            response_chars=int(get("ALICEGPT_RESPONSE_CHARS", "1500")),
            session_idle_seconds=float(get("ALICEGPT_SESSION_IDLE_SECONDS", "3600")),
            quick_ack_seconds=float(get("ALICEGPT_QUICK_ACK_SECONDS", "3")),
            ephemeral=boolean("ALICEGPT_EPHEMERAL", False),
            shared_secret=get("ALICEGPT_WEBHOOK_SECRET") or None, model=get("ALICEGPT_MODEL") or None,
            effort=get("ALICEGPT_EFFORT") or None, webhook_path=get("ALICEGPT_WEBHOOK_PATH", "/alice/webhook"),
        )
        if config.host != "127.0.0.1" or config.port < 1 or config.port > 65535 or not config.webhook_path.startswith("/"):
            raise ValueError("agent must listen on 127.0.0.1 with a valid port")
        if config.codex_timeout <= 0 or config.webhook_timeout <= 0 or config.codex_timeout < config.webhook_timeout or config.session_idle_seconds <= 0 or config.quick_ack_seconds <= 0:
            raise ValueError("timeouts must be positive and Codex timeout must be at least webhook timeout")
        return config
