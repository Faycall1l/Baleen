// Board smoke pass — load / search-select / esc-clear / filters / zoom.
// Usage: node smoke.mjs [outdir]
// Green = each step's DOM assertion holds and no page errors occur.
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const outdir = process.argv[2] ?? '/tmp/d3-smoke';
mkdirSync(outdir, { recursive: true });

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', (err) => errors.push(`[pageerror] ${String(err).slice(0, 300)}`));

let failed = '';
const step = async (name, fn) => {
  try {
    await fn();
    console.log(`ok: ${name}`);
  } catch (e) {
    failed = failed || `${name}: ${String(e).split('\n')[0].slice(0, 160)}`;
    console.log(`FAIL: ${name}`);
  }
};

await page.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded', timeout: 60000 });

// First-run tour (R11) overlays the shell on a fresh profile and would
// swallow every click below: dismiss it like a user would, if present.
await step('tour: dismiss onboarding overlay', async () => {
  const skip = page.getByRole('button', { name: 'Skip tour' });
  if (await skip.count()) {
    await skip.click({ timeout: 5000 });
  }
  await page.waitForTimeout(500);
});

await step('load: board lanes paint', async () => {
  // First paint is DOM-observable: the footer stats line renders node counts
  // once the snapshot lands, then the board renders one row per lane.
  await page.waitForFunction(
    () => /[0-9]+ nodes/.test(document.querySelector('.graph-status')?.textContent ?? ''),
    null,
    { timeout: 180000 },
  );
  await page.locator('.tl-body').first().waitFor({ timeout: 60000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(outdir, '01-load.png') });
});

await step('search-select opens inspector', async () => {
  // Data-driven term: first titled node from the live payload (hardcoded
  // tickers age out of the truncated window).
  const term = await page.evaluate(async () => {
    const body = await fetch('/api/graph?limit=50').then((r) => r.json());
    const node = body.nodes.find((n) => n.title && n.title.length >= 3);
    return node ? node.title.slice(0, Math.min(node.title.length, 12)) : null;
  });
  if (!term) throw new Error('no titled node in payload');
  await page.getByLabel('Find a node').fill(term);
  await page.waitForTimeout(800);
  await page.getByLabel('Find a node').press('Enter');
  await page.locator('.node-detail').waitFor({ timeout: 10000 });
  const text = await page.locator('.node-detail').innerText();
  if (!text.toLowerCase().includes(term.slice(0, 4).toLowerCase())) throw new Error(`inspector lacks ${term}`);
  await page.screenshot({ path: path.join(outdir, '02-selected.png') });
});

await step('esc clears inspector', async () => {
  await page.keyboard.press('Escape');
  await page.waitForTimeout(1200);
  if (await page.locator('.node-detail').count()) throw new Error('inspector still open');
});

await step('type filter pills hide and restore', async () => {
  // Hiding a type via the legend must surface as an explicit, reversible
  // pill — no silent filter state (R18).
  await page.getByText(/Event · \d+/).click({ timeout: 10000 });
  const pill = page.getByRole('button', { name: /Show Event nodes/ });
  await pill.waitFor({ timeout: 10000 });
  await page.screenshot({ path: path.join(outdir, '03-filtered.png') });
  await page.getByRole('button', { name: 'Show all' }).click();
  await page.waitForTimeout(1500);
  if (await pill.count()) throw new Error('pill still present after Show all');
});

await step('zoom presets refocus error-free', async () => {
  await page.getByRole('button', { name: '7d', exact: true }).click({ timeout: 10000 });
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: 'All', exact: true }).click({ timeout: 10000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(outdir, '04-zoomed.png') });
});

console.log(errors.length ? `PAGE ERRORS:\n${errors.join('\n')}` : 'no page errors');
if (errors.length) failed = failed || 'page errors present';
console.log(failed ? `RED: ${failed}` : 'GREEN: smoke pass');
await browser.close();
process.exit(failed ? 1 : 0);
