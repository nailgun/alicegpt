# AliceGPT → local Codex app-server PoC

This is a minimal Python CLI that talks directly to the **locally installed** `codex app-server` over its default stdio JSONL transport. It uses the existing ChatGPT/Codex OAuth session owned by the Codex CLI — there is no OpenAI Platform API key, browser automation, or Accessibility API.

It is a feasibility test, not a production service. The app-server protocol is versioned with the installed Codex CLI, so this PoC deliberately has no third-party dependencies and checks the real local server at runtime.

## Prerequisites

- Python 3.9+ (standard library only).
- A current `codex` CLI on `PATH` (tested here with `codex-cli 0.157.1`).
- A completed Codex login in the same OS user account:

  ```bash
  codex login
  ```

  Complete the interactive login once. The script does not and cannot perform login for you. If the run reports an authentication error, repeat `codex login`, then retry.

## Run

From the repository root, start an interactive ephemeral chat:

```bash
python3 poc/app-server/codex_app_server.py
```

After the handshake and creation of the ephemeral thread, stdout prints:

```text
READY
MODEL gpt-6-astra
EFFORT low
```

(`MODEL` and the default `EFFORT` are resolved by app-server; they may differ for another account or configuration.) Then enter one request per line. Each line starts a new turn in the **same** ephemeral chat, so context continues until EOF (`Ctrl-D`) or process termination. The final assistant answer for each turn goes to stdout. Timestamped lifecycle logs go to stderr; `--verbose` also logs full JSON-RPC frames and app-server stderr.

Useful variations:

```bash
# See exactly the model catalog available to the current Codex account.
python3 poc/app-server/codex_app_server.py --list-models

# Use the account default model in an explicit repository directory.
python3 poc/app-server/codex_app_server.py --cwd /Users/nailgun/src/alicegpt

# Select a supported reasoning effort. It is validated against model/list,
# printed as EFFORT after READY, and sent with every turn.
python3 poc/app-server/codex_app_server.py --model gpt-6-sol --effort high

# Show complete protocol frames in addition to the normal timestamped logs.
python3 poc/app-server/codex_app_server.py --verbose
```

By default, `thread/start` sends `ephemeral: true`: the thread is in memory only, disappears when the app-server process ends, and is not added to stored thread listings. The chat context is therefore preserved only during one CLI process lifetime.

Pass `--persistent` to create a stored Codex thread instead:

```bash
python3 poc/app-server/codex_app_server.py --persistent --print-thread-id
```

The thread id is written to stderr. Persistent threads remain in local Codex history after the CLI exits and can later be continued through app-server `thread/resume`; this minimal CLI still always creates a **new** thread on each launch.

## What the CLI actually sends

The current protocol requires a connection handshake before other requests:

```text
initialize → initialized → thread/start {ephemeral:true} → turn/start → notifications … → turn/completed
```

`model/list` is available on this server and is exposed by `--list-models`; the CLI does not hard-code a model and uses the account default unless `--model` is passed. During a turn it reads all JSONL notifications, collects `item/agentMessage/delta`, then waits specifically for `turn/completed`. On completion it uses the last completed-turn `agentMessage` item (the final assistant text), falling back to streamed deltas. Tool progress, reasoning, JSON-RPC traffic, and Codex stderr never go to stdout.

## Smoke test

This has no unit-test dependencies. The live smoke test consumes the logged-in account's Codex capacity:

```bash
printf '%s\n' 'Запомни: лимон.' 'Что я попросил запомнить? Ответь одним словом.' | \
  python3 poc/app-server/codex_app_server.py --timeout 120 --print-thread-id
```

Expected stdout begins with `READY` and `MODEL ...`, then contains a confirmation and `лимон`. Stderr shows timestamped state changes and the generated thread id.

## Observed protocol differences and limits

- The installed CLI is `0.157.1`; its `codex app-server generate-json-schema --out DIR` command was used to inspect the exact schemas. This matters because app-server evolves independently of old examples.
- The official docs use JSON-RPC 2.0 semantics but omit the `jsonrpc: "2.0"` header on the wire. Stdio is newline-delimited JSON, not HTTP or the Platform Responses API.
- `thread/start` supports `ephemeral`; `thread/fork` can also create ephemeral forks. Ephemeral threads cannot be listed or resumed after this child process exits. Live multi-turn continuation works because the thread remains loaded in that process.
- `turn/start.input` is an array of content items, so this PoC sends `[{"type":"text","text": ...}]`, not a bare prompt string found in some older integrations.
- In this protocol version, `effort` belongs to `turn/start`, not `thread/start`. `--effort` validates the chosen value against the selected model's `supportedReasoningEfforts`, then includes it in every turn; app-server defines it as an override for that and subsequent turns.
- A turn can request user approval for tool actions. This text-only PoC does not implement server-initiated approval handling; it times out with a useful error rather than silently approving anything. Keep prompts simple for smoke tests.
- The timeout applies separately to startup and each turn, so an idle interactive chat does not expire. A failed turn emits `ERROR ...` to stdout and the process stays ready for the next input line. On EOF or fatal startup failure the child process is closed, then terminated/killed only if it does not exit promptly.

## Sources

- [Official Codex App Server documentation](https://developers.openai.com/codex/app-server/) documents JSONL stdio, the handshake, `thread/start`, `turn/start`, streaming notifications, and `turn/completed`.
- [OpenAI Codex source: app-server protocol](https://github.com/openai/codex/tree/main/codex-rs/app-server-protocol) is the upstream implementation. Generate local schemas with `codex app-server generate-json-schema --out /tmp/codex-schemas` to match your installed version.
