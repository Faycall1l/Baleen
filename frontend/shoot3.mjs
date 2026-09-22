import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const outdir = '/tmp/shots3';
mkdirSync(outdir, { recursive: true });

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', (err) => errors.push(`[pageerror] ${String(err).slice(0, 250)}`));

await page.goto('http://localhost:5173/', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(10000);
await page.getByRole('tab', { name: 'Schema' }).click();
await page.waitForTimeout(3000);
await page.screenshot({ path: path.join(outdir, '06-schema-after-audit.png') });
const ux = await page.evaluate(() => (window.__uxLog ?? []).map((e) => `${e.event}: ${e.detail}`).join('\n'));
console.log('--- ux log ---\n' + ux);
console.log('done');
console.log(errors.length ? `PAGE ERRORS:\n${errors.join('\n')}` : 'no page errors');
await browser.close();
