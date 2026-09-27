import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';
import { CHATGPT_URL, findVisible, ui } from './ui.js';
import { writeDebugArtifacts } from './diagnostics.js';

export class UiError extends Error {}
export class AuthenticationError extends UiError {}
export class ResponseTimeoutError extends UiError {}

export function defaultProfileDir() {
  return path.join(os.homedir(), '.alicegpt', 'chatgpt-profile');
}

export async function prepareProfile(directory) {
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  await fs.chmod(directory, 0o700).catch(() => {});
}

export async function launch({ profileDir, headless, debug, log }) {
  await prepareProfile(profileDir);
  // This is intentionally Playwright's bundled browser, never system Chrome/CDP.
  const context = await chromium.launchPersistentContext(profileDir, {
    headless,
    viewport: { width: 1440, height: 1000 },
  });
  const page = context.pages()[0] || await context.newPage();
  return { context, page, debug, log };
}

export async function openChatGPT(page) {
  await page.goto(CHATGPT_URL, { waitUntil: 'domcontentloaded' });
}

export async function sessionState(page, timeoutMs = 1500) {
  return classifySessionUi({
    authenticatedVisible: Boolean(await findVisible(page, ui.authenticated, timeoutMs)),
    signInVisible: Boolean(await findVisible(page, ui.signIn, 300)),
  });
}

export function classifySessionUi({ authenticatedVisible, signInVisible }) {
  if (authenticatedVisible) return 'authenticated';
  if (signInVisible) return 'signed-out';
  return 'unknown';
}

export async function requireAuthenticated(session, timeoutMs) {
  const state = await sessionState(session.page, Math.min(timeoutMs, 3000));
  if (state !== 'authenticated') {
    throw new AuthenticationError('ChatGPT is not authenticated. Run `alicegpt-chat login` in headed mode and complete sign-in in the browser.');
  }
}

async function clickRequired(page, strategies, timeoutMs, description) {
  const target = await findVisible(page, strategies, timeoutMs);
  if (!target) throw new UiError(`Could not find ${description}; ChatGPT's UI may have changed.`);
  await target.click();
}

export async function createTemporaryChat(session, timeoutMs) {
  const { page } = session;
  await clickRequired(page, ui.newChat, timeoutMs, 'the New chat control');
  await clickRequired(page, ui.temporaryChat, timeoutMs, 'the Temporary Chat control');
  const promptBox = await findVisible(page, ui.promptBox, timeoutMs);
  if (!promptBox) throw new UiError('Could not find the message box after creating Temporary Chat.');
  return promptBox;
}

async function lastAssistantText(page) {
  for (const strategy of ui.assistantMessages) {
    const messages = page.locator(strategy.css);
    const count = await messages.count();
    if (count) return (await messages.nth(count - 1).innerText()).trim();
  }
  return '';
}

/**
 * Observe transition from Stop back to Send, then require the answer text to
 * remain stable briefly. No arbitrary response-duration sleep is used.
 */
export async function waitForFinalAnswer(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let sawStreaming = false;
  let previous = '';
  let stableSince = 0;
  while (Date.now() < deadline) {
    const stopVisible = Boolean(await findVisible(page, ui.stop, 80));
    sawStreaming ||= stopVisible;
    const answer = await lastAssistantText(page);
    const sendVisible = Boolean(await findVisible(page, ui.send, 80));
    if (answer && sendVisible && !stopVisible) {
      if (answer === previous) {
        if (!stableSince) stableSince = Date.now();
        // A caught Stop → Send transition needs less confirmation. If an
        // extremely fast answer skipped our observation of Stop, use DOM
        // stabilization as the independent completion signal instead.
        const requiredStableMs = sawStreaming ? 500 : 900;
        if (Date.now() - stableSince >= requiredStableMs) return answer;
      } else {
        previous = answer;
        stableSince = 0;
      }
    }
    await page.waitForTimeout(120);
  }
  throw new ResponseTimeoutError(`Timed out waiting for ChatGPT to finish after ${Math.round(timeoutMs / 1000)} seconds.`);
}

export async function ask(session, prompt, timeoutMs) {
  if (session.debug) {
    await writeDebugArtifacts(session.page, session.debugDir, `ask-start-${Date.now()}`, session.log);
  }
  await requireAuthenticated(session, timeoutMs);
  const promptBox = await createTemporaryChat(session, timeoutMs);
  await promptBox.fill(prompt);
  await clickRequired(session.page, ui.send, timeoutMs, 'the Send button');
  return waitForFinalAnswer(session.page, timeoutMs);
}

export async function login(session, timeoutMs) {
  await openChatGPT(session.page);
  if (session.debug) {
    await writeDebugArtifacts(session.page, session.debugDir, `login-start-${Date.now()}`, session.log);
  }
  session.log('Complete ChatGPT sign-in manually in the opened browser. Waiting for the UI to show an authenticated session…');
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await sessionState(session.page, 500) === 'authenticated') {
      session.log('Authenticated session detected. The durable browser state remains in the profile directory.');
      return;
    }
    await session.page.waitForTimeout(250);
  }
  throw new AuthenticationError(`Login was not detected within ${Math.round(timeoutMs / 1000)} seconds.`);
}

export async function withSession(options, action) {
  const session = await launch(options);
  try {
    return await action(session);
  } catch (error) {
    if (options.debug) {
      await writeDebugArtifacts(session.page, options.debugDir, `failure-${Date.now()}`, options.log);
    }
    throw error;
  } finally {
    await session.context.close();
  }
}
