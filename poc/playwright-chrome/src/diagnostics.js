import fs from 'node:fs/promises';
import path from 'node:path';
import { ui } from './ui.js';

function redact(value) {
  return value
    .replace(/(token|authorization|cookie|session|localstorage)\s*[=:]\s*[^\s"'<>]+/gi, '$1=[redacted]')
    .replace(/value\s*=\s*(["'])[^"']*\1/gi, 'value="[redacted]"');
}

/** Capture only a screenshot and a deliberately limited, sanitized UI summary. */
export async function writeDebugArtifacts(page, directory, label, log) {
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  await fs.chmod(directory, 0o700).catch(() => {});
  const safeLabel = label.replace(/[^a-z0-9_-]/gi, '-');
  const imagePath = path.join(directory, `${safeLabel}.png`);
  const textPath = path.join(directory, `${safeLabel}.txt`);
  await page.screenshot({ path: imagePath, fullPage: false }).catch((error) => log(`debug screenshot failed: ${error.message}`));
  const summary = await page.evaluate((selector) => {
    const nodes = [...document.querySelectorAll(selector)];
    return nodes.slice(0, 100).map((node) => ({
      tag: node.tagName,
      role: node.getAttribute('role'),
      ariaLabel: node.getAttribute('aria-label'),
      testId: node.getAttribute('data-testid'),
      text: (node.textContent || '').trim().slice(0, 160),
    }));
  }, ui.diagnosticControls).catch((error) => [{ error: error.message }]);
  await fs.writeFile(textPath, redact(JSON.stringify(summary, null, 2)), { mode: 0o600 });
  await fs.chmod(textPath, 0o600).catch(() => {});
  log(`debug artifacts: ${imagePath} and ${textPath} (UI summary is sanitized)`);
}
