// D2: traced schema-click repro — Schema tab switch (and back) with
// Playwright tracing, DOM assertions, and screenshots.
// Usage: node repro-schema-click.mjs [outdir]
// Green = tabs switch modes (asserted via aria-selected + mode-specific UI),
// no page errors.
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

let failed = '';
await page.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForFunction(
  () => /[0-9]+ nodes/.test(document.querySelector('.graph-status')?.textContent ?? ''),
  null,
  { timeout: 180000 },
);
await page.waitForTimeout(2000);
await page.screenshot({ path: path.join(outdir, '01-instances.png') });

// Schema tab click: tab selects AND the schema hint replaces the search box.
await page.getByRole('tab', { name: 'Schema' }).click();
await page.waitForTimeout(2500);
const schemaSelected = await page.getByRole('tab', { name: 'Schema' }).getAttribute('aria-selected');
await page.screenshot({ path: path.join(outdir, '02-schema.png') });
if (schemaSelected !== 'true') failed = 'schema tab not selected after click';
if (!(await page.getByText('Click a type to inspect its instances').count())) {
  failed = failed || 'schema hint missing after switch';
}

// Back to Instances: search box returns.
await page.getByRole('tab', { name: 'Instances' }).click();
await page.waitForTimeout(2500);
const instSelected = await page.getByRole('tab', { name: 'Instances' }).getAttribute('aria-selected');
await page.screenshot({ path: path.join(outdir, '03-instances.png') });
if (instSelected !== 'true') failed = failed || 'instances tab not selected after click';
if (!(await page.getByLabel('Find a node').count())) {
  failed = failed || 'search box missing after switch back';
}

await context.tracing.stop({ path: path.join(outdir, 'trace.zip') });

console.log(errors.length ? `PAGE ERRORS:\n${errors.join('\n')}` : 'no page errors');
if (errors.length) failed = failed || 'page errors present';
console.log(failed ? `RED: ${failed}` : 'GREEN: schema-click repro passed');
await browser.close();
process.exit(failed ? 1 : 0);
