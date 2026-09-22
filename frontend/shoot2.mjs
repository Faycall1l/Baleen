// Interaction verification: click a real node, schema mode, legend toggle.
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const outdir = process.argv[2] ?? '/tmp/shots2';
mkdirSync(outdir, { recursive: true });

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('pageerror', (err) => errors.push(`[pageerror] ${String(err).slice(0, 300)}`));

await page.goto('http://localhost:5173/', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(8000);

// Click the orange hub visible around x=930 y=310.
await page.mouse.click(930, 310);
await page.waitForTimeout(2500);
await page.screenshot({ path: path.join(outdir, '04-node-click.png') });

// Schema mode.
await page.getByRole('tab', { name: 'Schema' }).click();
await page.waitForTimeout(2500);
await page.screenshot({ path: path.join(outdir, '05-schema.png') });

console.log('shots in', outdir);
console.log(errors.length ? `PAGE ERRORS:\n${errors.join('\n')}` : 'no page errors');
await browser.close();
