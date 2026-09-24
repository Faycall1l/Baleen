// D2: traced schema-click repro — Schema tab switch (and back) with
// Playwright tracing, __uxLog assertions, and screenshots.
// Usage: node repro-schema-click.mjs [outdir]
// Green = tabs switch modes, telemetry shows view-ok per mode, no page errors.
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const outdir = process.argv[2] ?? '/tmp/d2-schema-click';
mkdirSync(outdir, { recursive: true });

const errors = [];
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.tracing.start({ screenshots: true, snapshots: true });
const page = await context.newPage();
page.on('pageerror', (err) => errors.push(`[pageerror] ${String(err).slice(0, 300)}`));

const ux = (pred) =>
  page.evaluate(
    (p) => (window.__uxLog ?? []).filter((e) => e.event.includes(p)).map((e) => `${e.event}: ${e.detail}`),
    pred,
  );
const waitUx = async (pred, timeout = 30000) => {
  await page.waitForFunction((p) => (window.__uxLog ?? []).some((e) => e.event.includes(p)), pred, { timeout });
};

let failed = '';
await page.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded', timeout: 60000 });
await waitUx('layout-done', 60000);
await page.waitForTimeout(2000);
await page.screenshot({ path: path.join(outdir, '01-instances.png') });

// Schema tab click.
await page.getByRole('tab', { name: 'Schema' }).click();
await page.waitForTimeout(2500);
const schemaSelected = await page.getByRole('tab', { name: 'Schema' }).getAttribute('aria-selected');
const schemaViews = await ux('view-ok');
await page.screenshot({ path: path.join(outdir, '02-schema.png') });
if (schemaSelected !== 'true') failed = 'schema tab not selected after click';
if (!schemaViews.some((l) => l.includes('mode=schema'))) failed = 'no view-ok for mode=schema';

// Back to Instances.
await page.getByRole('tab', { name: 'Instances' }).click();
await page.waitForTimeout(2500);
const instSelected = await page.getByRole('tab', { name: 'Instances' }).getAttribute('aria-selected');
const instViews = await ux('view-ok');
await page.screenshot({ path: path.join(outdir, '03-instances.png') });
if (instSelected !== 'true') failed = failed || 'instances tab not selected after click';
if (!instViews.some((l) => l.includes('mode=instances'))) failed = failed || 'no view-ok for mode=instances';

const log = await page.evaluate(() => (window.__uxLog ?? []).map((e) => `${e.event}: ${e.detail}`).join('\n'));
await context.tracing.stop({ path: path.join(outdir, 'trace.zip') });

console.log('--- ux log (tail) ---\n' + log.split('\n').slice(-12).join('\n'));
console.log(errors.length ? `PAGE ERRORS:\n${errors.join('\n')}` : 'no page errors');
if (errors.length) failed = failed || 'page errors present';
console.log(failed ? `RED: ${failed}` : 'GREEN: schema-click repro passed');
await browser.close();
process.exit(failed ? 1 : 0);
