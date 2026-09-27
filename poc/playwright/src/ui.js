/**
 * All ChatGPT-specific UI knowledge lives here. Prefer accessible roles and
 * visible labels; the few CSS selectors are documented last-resort fallbacks.
 */

export const CHATGPT_URL = 'https://chatgpt.com/';

export const ui = {
  // A signed-in page normally exposes one of these controls. This deliberately
  // avoids inspecting cookies, localStorage, or API responses.
  authenticated: [
    { role: 'button', name: /open user menu|account menu|profile/i },
    { role: 'button', name: /upgrade plan|upgrade/i },
    // Narrow fallback for layouts that expose the signed-in menu by test id.
    { css: '[data-testid="profile-button"]' },
  ],
  signIn: [
    { role: 'button', name: /log in|sign in/i },
    { role: 'link', name: /log in|sign in/i },
  ],
  newChat: [
    { role: 'link', name: /new chat/i },
    { role: 'button', name: /new chat/i },
    // Fallback for a known layout where the accessible label is stable but no
    // semantic role is exposed by the application shell.
    { css: '[aria-label="New chat"]' },
  ],
  temporaryChat: [
    { role: 'button', name: /temporary chat|temporary/i },
    { role: 'menuitem', name: /temporary chat|temporary/i },
    { text: /temporary chat/i },
    // Narrow fallback for historical layouts. Keep here, not in bridge code.
    { css: '[data-testid="temporary-chat-button"]' },
  ],
  promptBox: [
    { role: 'textbox', name: /message|ask anything|prompt/i },
    { css: '#prompt-textarea' },
    { css: 'textarea[placeholder*="Message"]' },
  ],
  send: [
    { role: 'button', name: /^send prompt$|^send$/i },
    { css: '[data-testid="send-button"]' },
  ],
  stop: [
    { role: 'button', name: /stop generating|stop streaming|stop/i },
    { css: '[data-testid="stop-button"]' },
  ],
  assistantMessages: [
    { css: '[data-message-author-role="assistant"]' },
    { css: '[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]' },
  ],
  // Debug-only, generic control inventory. It deliberately excludes messages
  // and storage-bearing nodes so calibration does not export a conversation.
  diagnosticControls: 'button, [role="button"], [role="menuitem"], textarea, [role="textbox"]',
};

export function locatorFor(page, strategy) {
  if (strategy.role) return page.getByRole(strategy.role, { name: strategy.name });
  if (strategy.text) return page.getByText(strategy.text, { exact: false });
  return page.locator(strategy.css);
}

export function firstVisibleLocator(page, strategies) {
  // Return locators lazily: callers use this with Playwright's polling APIs.
  return strategies.map((strategy) => locatorFor(page, strategy));
}

export async function findVisible(page, strategies, timeoutMs) {
  const candidates = firstVisibleLocator(page, strategies);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const candidate of candidates) {
      if (await candidate.first().isVisible().catch(() => false)) return candidate.first();
    }
    await page.waitForTimeout(100);
  }
  return null;
}
