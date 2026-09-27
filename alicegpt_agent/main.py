from __future__ import annotations

import os
import threading
from .codex_adapter import CodexAdapter
from .config import Config
from .http_server import serve
from .observability import event, setup_logging
from .router import Router
from .thread_store import ThreadStore


def main() -> int:
    config = Config.from_env(); setup_logging()
    salt = os.environ.get("ALICEGPT_HASH_SALT")
    if not salt: raise SystemExit("ALICEGPT_HASH_SALT is required; keep it outside the repository.")
    store = ThreadStore(config.database)
    # The MVP deliberately starts ephemeral threads.  A stored mapping from a
    # previous daemon would point to a thread that no longer exists.
    store.forget_all_routes()
    adapter = CodexAdapter(config.codex, config.cwd, config.model, config.effort, config.ephemeral)
    router = Router(store, adapter, salt, config.codex_timeout, config.response_chars,
                    config.session_idle_seconds, config.quick_ack_seconds)
    server = serve(config, router)
    event("agent.started", host=config.host, port=config.port)
    threading.Thread(target=router.warm_up, name="codex-warmup", daemon=True).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close(); adapter.close(); store.close(); event("agent.stopped")
    return 0


if __name__ == "__main__": raise SystemExit(main())
