import path from 'node:path';
import process from 'node:process';
import readline from 'node:readline/promises';
import { ask, defaultProfileDir, login, openChatGPT, withSession } from './bridge.js';

export function parseArgs(argv) {
  const options = { headless: false, debug: false, timeout: 120_000, profileDir: defaultProfileDir() };
  const positional = [];
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--headed') options.headless = false;
    else if (value === '--headless') options.headless = true;
    else if (value === '--debug') options.debug = true;
    else if (value === '--profile') options.profileDir = argv[++index];
    else if (value === '--timeout') options.timeout = Number(argv[++index]) * 1000;
    else positional.push(value);
  }
  if (!Number.isFinite(options.timeout) || options.timeout <= 0) throw new Error('--timeout must be a positive number of seconds.');
  if (!options.profileDir) throw new Error('--profile needs a directory path.');
  return { command: positional[0], prompt: positional.slice(1).join(' '), options };
}

export function usage() {
  return `Usage: alicegpt-chat [--headed|--headless] [--profile DIR] [--timeout SECONDS] [--debug] <login|ask|shell> [prompt]

Default is headed mode. --headless is experimental: ChatGPT may challenge or reject it.
Commands: login, ask 'your prompt', shell`;
}

export async function main(argv, dependencies = { withSession, login, ask, openChatGPT }) {
  let parsed;
  try { parsed = parseArgs(argv); } catch (error) { console.error(`error: ${error.message}`); return 2; }
  if (!parsed.command || parsed.command === '--help' || parsed.command === '-h') { console.log(usage()); return 0; }
  if (!['login', 'ask', 'shell'].includes(parsed.command)) { console.error(`error: unknown command ${parsed.command}\n${usage()}`); return 2; }
  if (parsed.command === 'ask' && !parsed.prompt.trim()) { console.error('error: ask needs a non-empty prompt.'); return 2; }
  const log = (message) => console.error(`alicegpt-chat: ${message}`);
  const sessionOptions = { ...parsed.options, debugDir: path.resolve('artifacts/chatgpt-debug'), log };
  try {
    if (parsed.command === 'login') {
      // Login is always headed: the user must interact with the provider UI.
      await dependencies.withSession({ ...sessionOptions, headless: false }, (session) => dependencies.login(session, parsed.options.timeout));
      return 0;
    }
    if (parsed.command === 'ask') {
      const answer = await dependencies.withSession(sessionOptions, async (session) => {
        await dependencies.openChatGPT(session.page);
        return dependencies.ask(session, parsed.prompt.trim(), parsed.options.timeout);
      });
      console.log(answer);
      return 0;
    }
    return runShell(sessionOptions, parsed.options.timeout, dependencies);
  } catch (error) {
    log(error instanceof Error ? error.message : String(error));
    return 1;
  }
}

export async function runShell(sessionOptions, timeoutMs, dependencies) {
  const terminal = readline.createInterface({ input: process.stdin, output: process.stderr });
  console.error('Temporary Chat shell. Each prompt opens a new Temporary Chat. Type /exit to quit.');
  try {
    return await dependencies.withSession(sessionOptions, async (session) => {
      await dependencies.openChatGPT(session.page);
      while (true) {
        const prompt = (await terminal.question('you> ')).trim();
        if (!prompt || prompt === '/exit' || prompt === '/quit') return 0;
        const answer = await dependencies.ask(session, prompt, timeoutMs);
        console.log(answer);
      }
    });
  } finally { terminal.close(); }
}
