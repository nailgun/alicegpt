# AliceGPT ChatGPT UI bridge — local PoC

This is a small, local feasibility test for asking **consumer chatgpt.com** through its visible UI. It launches the **locally installed Google Chrome** with a dedicated persistent AliceGPT profile. Playwright is used only to automate that Chrome instance; it does not download or launch bundled Chromium, use CDP, the OpenAI Platform API, or API keys.

> Important: this is **not an official ChatGPT API**. The UI can change at any time, and consumer Terms may restrict automated retrieval of output. Do not treat it as production-safe or as a public integration. Yandex Alice, servers, webhooks, and the existing Slack experiment are explicitly out of scope.

## Install

Requires Node.js 20 or newer and a locally installed stable Google Chrome.

```bash
cd /Users/nailgun/src/alicegpt
npm install
npm link                 # optional: makes `alicegpt-chat` available in this shell
```

Without `npm link`, replace `alicegpt-chat` below with `node bin/alicegpt-chat.js`.

## Use

The default is a visible, headed browser. `--headless` exists for experimentation only; ChatGPT may present challenges or reject it.

```bash
# Opens ChatGPT and waits while you sign in manually. No credentials are stored in code.
alicegpt-chat login

# Every call deliberately creates a brand-new Temporary Chat through the UI.
alicegpt-chat ask 'Привет'

# Interactive loop; each entered prompt uses a separate new Temporary Chat.
alicegpt-chat shell
```

Options may come before the command:

```bash
alicegpt-chat --headless --timeout 180 ask 'Кратко объясни HTTP/2'
alicegpt-chat --profile /secure/location/chatgpt-profile ask 'Привет'
```

The default persistent profile is `~/.alicegpt/chatgpt-profile`. The tool creates it with owner-only permissions where the OS permits. It contains the durable browser session state, is ignored by Git, and must not be copied, committed, or shared. The tool neither logs nor exports cookies, auth tokens, or local storage.

If authentication expires, `ask`/`shell` open the browser and exit with a re-login instruction. Run `alicegpt-chat login` again. To reset the session, close all tool browser windows and delete **only** `~/.alicegpt/chatgpt-profile`; the next `login` creates it afresh.

## Waiting and output

`ask` detects an existing signed-in UI, creates Temporary Chat, sends the prompt, and observes the Stop-to-Send transition plus stabilized assistant text. It does not merely wait a fixed response delay. Final assistant text goes to standard output; status and failures go to standard error. UI, login, and timeout failures return a nonzero status.

`shell` intentionally does **not** preserve conversation context in this baseline: every input creates a new Temporary Chat. This keeps the privacy/scope behavior easy to verify.

## Debugging and selector calibration

The UI changes often. Use debug mode to collect a screenshot and a limited, sanitized list of visible buttons/textboxes; it does not dump storage or cookies:

```bash
alicegpt-chat --debug ask 'Проверка'
```

Artifacts appear under `artifacts/chatgpt-debug/` (Git-ignored). Review the screenshot and `.txt` UI summary, then update the centralized strategies in [`src/ui.js`](src/ui.js). Do not add broad DOM exports or credential/session diagnostics. Useful manual calibration commands are:

```bash
alicegpt-chat --debug login
alicegpt-chat --debug ask 'Проверка после изменения селекторов'
npm test
```

## Manual happy-path checklist

1. Run `alicegpt-chat login` and complete authentication yourself in the visible Google Chrome window.
2. Confirm the command reports that the authenticated UI was detected.
3. Run `alicegpt-chat ask 'Привет'`; verify a new ChatGPT Temporary Chat is visible and the final text prints in the terminal.
4. Run `alicegpt-chat shell`, enter two prompts, and verify each appears in a separate Temporary Chat.
5. Sign out in ChatGPT (or reset the profile) and confirm `ask` tells you to log in again.
6. If any control is not found, retry with `--debug`, inspect only the sanitized artifacts, and calibrate `src/ui.js`.

## Local checks

```bash
npm test
npm run smoke
```

Tests do not open ChatGPT, require credentials, or run live browser automation in CI.
