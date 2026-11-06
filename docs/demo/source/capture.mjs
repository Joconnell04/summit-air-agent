// Screenshots of the local owner view and bench for the demo videos (docs/demo/README.md).
// Signs in as the demo admin from scripts/seed_owner_day.py (development only) and saves each page
// at 1600 px wide, 2x, to docs/demo/source/shots/. Needs `make owner-demo` data served on BASE with
// the mock ServiceTitan beside it (docs/demo/source/serve.sh).
//
// Run: node docs/demo/source/capture.mjs   (PLAYWRIGHT_FROM=<folder with node_modules/playwright>)
import { createRequire } from 'module';
import { mkdirSync } from 'fs';
import path from 'path';

const from = process.env.PLAYWRIGHT_FROM || path.join(process.env.HOME, '.claude/skills/video-maker/scripts');
const { chromium } = createRequire(path.join(from, 'package.json'))('playwright');
const BASE = process.env.BASE || 'http://127.0.0.1:8120';
const OUT = 'docs/demo/source/shots';

// name, path, and what to do on the page before the picture.
const SHOTS = [
  ['today', '/owner'],
  ['today-leads', '/owner', (p) => p.click('#tab-leads')],
  ['today-away', '/owner', (p) => p.click('#tab-away')],
  ['call-c01', '/owner/calls/c01'],
  ['call-c01-tech', '/owner/calls/c01', (p) => p.click('[data-tech]')],
  ['call-c03', '/owner/calls/c03'],
  ['call-c05', '/owner/calls/c05'],
  ['call-c09', '/owner/calls/c09'],
  ['call-c10', '/owner/calls/c10'],
  ['call-c12', '/owner/calls/c12'],
  ['call-c19', '/owner/calls/c19'],
  ['call-out-2', '/owner/calls/out-2'],
  ['schedule', '/owner/schedule'],
  ['job-608', '/owner/jobs/608'],
  ['calls', '/owner/calls'],
  ['settings', '/owner/settings'],
  ['rules', '/bench/rules'],
];

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
const page = await context.newPage();
await page.goto(BASE + '/owner');
await page.fill('#username', 'owner');
await page.fill('#password', 'dev-owner-password');
await Promise.all([page.waitForNavigation(), page.press('#password', 'Enter')]);

for (const [name, url, act] of SHOTS) {
  // The owner view remembers toggles such as Technical details; every page starts from none.
  await page.evaluate(() => localStorage.clear());
  await page.goto(BASE + url);
  await page.evaluate(() => document.fonts.ready);
  if (act) {
    await act(page);
    await page.waitForTimeout(150);
  }
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
  console.log(name, size.join('x'));
}
await browser.close();
