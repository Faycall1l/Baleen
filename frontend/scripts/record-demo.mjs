// Records the README/docs GIFs from a running stack.
//
//   docker compose up -d && (seed + ingest, see docs/DEMO.md)
//   node scripts/record-demo.mjs [baseUrl]      # from frontend/
//
// Each scene is recorded as a WebM by Playwright, then converted to an
// optimized GIF in docs/media/ with ffmpeg (two-pass palette).

import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const BASE = process.argv[2] ?? 'http://localhost:5173';
const OUT = resolve(import.meta.dirname, '../../docs/media');
const SIZE = { width: 1440, height: 900 };
const pause = (page, ms) => page.waitForTimeout(ms);

// Spans (seconds since recording start) to splice out of the GIF, e.g. a
// slow LLM round-trip. Scenes call cutStart()/cutEnd() around the wait.
let t0 = 0;
let cuts = [];
const now = () => (Date.now() - t0) / 1000;
const cutStart = () => cuts.push([now(), null]);
const cutEnd = () => { cuts.at(-1)[1] = now(); };

async function rail(page, label) {
  await page.locator('.rail-btn', { hasText: label }).first().click();
  await pause(page, 1200);
}

async function glide(page, selector, nth = 0) {
  const box = await page.locator(selector).nth(nth).boundingBox();
  if (!box) return;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 });
  await pause(page, 400);
}

const scenes = {
  // Hero tour: insights, then ranked leads, then a ticker's evidence.
  async overview(page) {
    await pause(page, 800);
    await glide(page, '.bar-row', 0);
    await glide(page, '.bar-row', 4);
    await pause(page, 800);
    await rail(page, 'Leads');
    await rail(page, 'Insights'); // close it: board + leads side by side
    await glide(page, '.signal-row .node-link', 0);
    await page.locator('.signal-row .node-link').first().click().catch(() => {});
    await pause(page, 2800);
    await rail(page, 'Media');
    await glide(page, '.media-item', 1);
    await pause(page, 2000);
  },

  // Ranked story leads, opened in the inspector.
  async leads(page) {
    await rail(page, 'Leads');
    await rail(page, 'Insights');
    await glide(page, '.signal-row .node-link', 0);
    await page.locator('.signal-row .node-link').first().click().catch(() => {});
    await pause(page, 2500);
    await glide(page, '.signal-row .node-link', 3);
    await page.locator('.signal-row .node-link').nth(3).click().catch(() => {});
    await pause(page, 2500);
  },

  // Natural language to validated Cypher.
  async ask(page) {
    const input = page.getByPlaceholder(/Ask Baleen/);
    await input.click();
    await pause(page, 600);
    await input.pressSequentially('Which tickers did congressional traders buy most recently?', { delay: 55 });
    await pause(page, 500);
    await input.press('Enter');
    await pause(page, 1200);
    cutStart();
    await page.locator('.answer-panel table, .answer-panel pre, .answer-panel .error').first()
      .waitFor({ timeout: 90_000 }).catch(() => {});
    await pause(page, 300);
    cutEnd();
    await pause(page, 1500);
    await glide(page, '.answer-table tbody tr', 0);
    await pause(page, 2000);
    await page.mouse.wheel(0, 140);
    await pause(page, 3000);
  },

  // Media watch: headlines with deterministic mentions.
  async media(page) {
    await rail(page, 'Media');
    await rail(page, 'Insights');
    await glide(page, '.media-item', 0);
    await page.mouse.wheel(0, 350);
    await pause(page, 1500);
    const chip = page.locator('.media-chip').first();
    if (await chip.count()) {
      await chip.scrollIntoViewIfNeeded();
      await glide(page, '.media-chip', 0);
      await chip.click();
      await pause(page, 2500);
    } else {
      await page.mouse.wheel(0, 350);
      await pause(page, 1500);
    }
  },
};

function toGif(webm, gif, start = 0, fps = 12, width = 1200) {
  const palette = `${gif}.palette.png`;
  const keep = [`gte(t,${start})`, ...cuts.map(([a, b]) => `not(between(t,${a},${b}))`)].join('*');
  const vf = `select='${keep}',setpts=N/FRAME_RATE/TB,fps=${fps},scale=${width}:-1:flags=lanczos`;
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-vf', `${vf},palettegen=stats_mode=diff`, palette]);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-i', palette,
    '-lavfi', `${vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle`, gif]);
  rmSync(palette);
}

mkdirSync(OUT, { recursive: true });
const only = process.argv.slice(3);
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
for (const [name, run] of Object.entries(scenes)) {
  if (only.length && !only.includes(name)) continue;
  const dir = mkdtempSync(join(tmpdir(), `baleen-${name}-`));
  const ctx = await browser.newContext({
    viewport: SIZE, deviceScaleFactor: 1, colorScheme: 'dark',
    recordVideo: { dir, size: SIZE },
  });
  // Skip the first-run tour so it does not cover the recording.
  await ctx.addInitScript(() => localStorage.setItem('baleen.onboarded', '1'));
  t0 = Date.now(); // the video starts with the page
  const page = await ctx.newPage();
  cuts = [];
  await page.goto(BASE, { waitUntil: 'networkidle' });
  // Let first paint, data fetches and loading badges settle, then cut them.
  await page.getByText(/full window loading/i).first().waitFor({ state: 'hidden', timeout: 20_000 }).catch(() => {});
  await pause(page, 1500);
  const start = now();
  await run(page);
  await ctx.close();
  const webm = join(dir, readdirSync(dir).find((f) => f.endsWith('.webm')));
  toGif(webm, join(OUT, `${name}.gif`), start);
  rmSync(dir, { recursive: true });
  console.log(`docs/media/${name}.gif`);
}
await browser.close();
