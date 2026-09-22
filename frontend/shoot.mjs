// One-shot visual verification: loads the app, screenshots initial state,
// clicks the canvas center, screenshots again, and dumps console errors.
// Usage: node shoot.mjs [outdir]
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const outdir = process.argv[2] ?? '/tmp/shots';
mkdirSync(outdir, { recursive: true });

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on('console', (msg) => {
  if (msg.type() === 'error' || msg.type() === 'warning') {
    errors.push(`[${msg.type()}] ${msg.text().slice(0, 300)}`);
  }
});
page.on('pageerror', (err) => errors.push(`[pageerror] ${String(err).slice(0, 300)}`));

await page.goto('http://localhost:5173/', { waitUntil: 'networkidle', timeout: 60000 });
await page.waitForTimeout(8000);
await page.screenshot({ path: path.join(outdir, '01-initial.png') });

// Click the canvas center (likely a node or empty space — either is signal).
await page.mouse.click(720, 450);
await page.waitForTimeout(2500);
await page.screenshot({ path: path.join(outdir, '02-after-click.png') });

// Zoom out hard, then back in.
await page.mouse.move(720, 450);
for (let i = 0; i < 5; i++) {
  await page.mouse.wheel(0, 400);
  await page.waitForTimeout(200);
}
await page.waitForTimeout(1500);
await page.screenshot({ path: path.join(outdir, '03-zoomed-out.png') });

console.log('shots in', outdir);
console.log(errors.length ? `console issues (${errors.length}):\n` + errors.slice(0, 20).join('\n') : 'no console errors/warnings');
await browser.close();
