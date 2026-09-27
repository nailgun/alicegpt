import test from 'node:test';
import assert from 'node:assert/strict';
import { classifySessionUi, defaultProfileDir, prepareProfile } from '../src/bridge.js';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

test('default profile is outside the repository', () => {
  assert.match(defaultProfileDir(), /\.alicegpt[\\/]chatgpt-profile$/);
});

test('session state prefers positive authenticated evidence and handles sign-in UI', () => {
  assert.equal(classifySessionUi({ authenticatedVisible: true, signInVisible: true }), 'authenticated');
  assert.equal(classifySessionUi({ authenticatedVisible: false, signInVisible: true }), 'signed-out');
  assert.equal(classifySessionUi({ authenticatedVisible: false, signInVisible: false }), 'unknown');
});

test('prepareProfile creates an owner-only directory where supported', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'alicegpt-chat-test-'));
  const profile = path.join(directory, 'profile');
  await prepareProfile(profile);
  const mode = (await fs.stat(profile)).mode & 0o777;
  assert.equal(mode & 0o077, 0);
  await fs.rm(directory, { recursive: true, force: true });
});
