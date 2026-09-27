from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any


def setup_logging() -> None:
    log_path = Path(os.environ.get("ALICEGPT_LOG_FILE", ".alicegpt/agent.log"))
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[stream, file_handler])


def event(name: str, **fields: Any) -> None:
    """Structured lifecycle logging; callers must never pass prompts or raw IDs."""
    logging.getLogger("alicegpt").info("%s %s", name, json.dumps(fields, ensure_ascii=False, sort_keys=True))
