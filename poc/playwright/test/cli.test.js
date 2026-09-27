import test from 'node:test';
import assert from 'node:assert/strict';
import { parseArgs, main } from '../src/cli.js';

test('parseArgs defaults to headed and accepts an ask prompt', () => {
  const parsed = parseArgs(['ask', 'Привет']);
  assert.equal(parsed.command, 'ask');
  assert.equal(parsed.prompt, 'Привет');
  assert.equal(parsed.options.headless, false);
});

test('parseArgs supports profile, headless, debug and seconds timeout', () => {
  const parsed = parseArgs(['--headless', '--debug', '--profile', '/tmp/profile', '--timeout', '9', 'ask', 'hi']);
  assert.equal(parsed.options.headless, true);
  assert.equal(parsed.options.debug, true);
  assert.equal(parsed.options.profileDir, '/tmp/profile');
  assert.equal(parsed.options.timeout, 9000);
});

test('ask opens the site then prints the returned plain text', async () => {
  const output = [];
  const originalLog = console.log;
  console.log = (line) => output.push(line);
  const calls = [];
  try {
    const code = await main(['ask', 'hello'], {
      withSession: async (_options, action) => action({ page: {} }),
      openChatGPT: async () => calls.push('open'),
      ask: async (_session, prompt) => { calls.push(prompt); return 'answer'; },
      login: async () => {},
    });
    assert.equal(code, 0);
    assert.deepEqual(calls, ['open', 'hello']);
    assert.deepEqual(output, ['answer']);
  } finally { console.log = originalLog; }
});
