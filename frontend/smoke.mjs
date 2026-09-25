// D3: Playwright smoke pass — load / search-select / stage-clear / schema.
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

await step('load: canvas + first layout', async () => {
  await page.waitForFunction(
    () => (window.__uxLog ?? []).some((e) => e.event === 'layout-done'),
    null,
    { timeout: 60000 },
  );
  await page.locator('.graph-canvas canvas').first().waitFor({ timeout: 15000 });
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

await step('stage-click clears inspector', async () => {
  await page.mouse.click(60, 800); // left gutter, off-canvas chrome -> stage area is canvas; use far corner of canvas
  await page.waitForTimeout(1200);
  // Fallback: click true canvas center-top where no node sits (empty space clears too).
  if (await page.locator('.node-detail').count()) {
    await page.mouse.click(720, 120);
    await page.waitForTimeout(1200);
  }
  if (await page.locator('.node-detail').count()) throw new Error('inspector still open');
});

await step('schema tab switches mode', async () => {
  await page.getByRole('tab', { name: 'Schema' }).click();
  await page.waitForTimeout(2500);
  const sel = await page.getByRole('tab', { name: 'Schema' }).getAttribute('aria-selected');
  if (sel !== 'true') throw new Error('schema tab not selected');
  await page.screenshot({ path: path.join(outdir, '03-schema.png') });
});

await step('zoom out/in keeps canvas error-free', async () => {
  await page.getByRole('tab', { name: 'Instances' }).click();
  await page.waitForTimeout(1500);
  await page.mouse.move(720, 450);
  for (let i = 0; i < 5; i++) {
    await page.mouse.wheel(0, 400);
    await page.waitForTimeout(200);
  }
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(outdir, '04-zoomed-out.png') });
});

console.log(errors.length ? `PAGE ERRORS:\n${errors.join('\n')}` : 'no page errors');
if (errors.length) failed = failed || 'page errors present';
console.log(failed ? `RED: ${failed}` : 'GREEN: smoke pass');
await browser.close();
process.exit(failed ? 1 : 0);
