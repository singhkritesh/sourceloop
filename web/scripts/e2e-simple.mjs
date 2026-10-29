// First-run journey for a non-technical user against the Docker stack (http://localhost:3010):
// login hint -> demo banner -> "Run a sample project" -> buyer approves -> page says who is next -> Help page.
//   node scripts/e2e-simple.mjs      (reads ../data/demo-creds.txt; screenshots to ../data/ui/simple-*.png)
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
const BASE = process.env.WEB ?? 'http://localhost:3010';
const CHROME = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const creds = Object.fromEntries(fs.readFileSync('../data/demo-creds.txt', 'utf8').split('\n').map((l) => l.trim().split(/\s+/)).filter((c) => c[0]?.includes('@')).map((c) => [c[0], c[2]]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let bad = 0; const check = (n, ok, x = '') => { if (!ok) bad++; console.log(ok ? 'PASS' : 'FAIL', n, x); };
const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] });
const p = await b.newPage(); await p.setViewport({ width: 1280, height: 900 });
const text = () => p.evaluate(() => document.body.innerText);
const click = (label) => p.evaluate((l) => { const e = [...document.querySelectorAll('button,a')].find((x) => x.textContent.trim().includes(l)); if (e) e.click(); return !!e; }, label);
const shot = (n) => p.screenshot({ path: `../data/ui/simple-${n}.png` });
try {
  await p.goto(BASE + '/login'); await p.waitForSelector('#em');
  await p.evaluate(() => document.querySelector('details')?.setAttribute('open', ''));
  check('login explains which account to use', /Which account should I use/i.test(await text()) && /cat data\/demo-creds\.txt/.test(await text()));
  await shot('login');
  await p.type('#em', 'buyer@sourceloop.test'); await p.type('#pw', creds['buyer@sourceloop.test']); await p.keyboard.press('Enter');
  await p.waitForFunction(() => /Sourcing projects/.test(document.body.innerText), { timeout: 15000 });
  await p.waitForFunction(() => /Demo mode/i.test(document.body.innerText), { timeout: 8000 }).catch(() => {});
  let t = await text();
  check('demo-mode banner on projects page', /Demo mode/i.test(t));
  check('sample-project button present', /Run a sample project/.test(t));
  await click('New sourcing project'); await sleep(400); t = await text();
  check('new-project form is just name + need up front', /What do you need to buy/i.test(t) && !(await p.evaluate(() => document.querySelector('.scenario-grid')?.closest('details')?.open === true)));
  await shot('newproject'); await p.keyboard.press('Escape'); await sleep(300);
  await click('Run a sample project');
  await p.waitForFunction(() => /\/projects\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 15000 });
  await p.waitForFunction(() => /Needs a decision|Deal reached/i.test(document.body.innerText), { timeout: 120000 });
  check('sample project reaches a decision or a deal', true); await shot('decision');
  const projectUrl = p.url();
  await p.goto(BASE + '/approvals'); await sleep(1500);
  await p.evaluate(() => document.querySelector('button.appr')?.click()); await sleep(900);
  check('buyer can approve the need', await click('Approve')); await sleep(2500);
  await p.goto(projectUrl);
  await p.waitForFunction(() => /Waiting for Compliance/i.test(document.body.innerText), { timeout: 120000 }).catch(() => {});
  t = await text(); await shot('waiting');
  check('page names who it waits on and where to sign in', /Waiting for Compliance/i.test(t) && /compliance@sourceloop\.test/.test(t));
  await p.goto(BASE + '/help'); await sleep(800); t = await text();
  check('help page has glossary', /Glossary/i.test(t) && /Orchestrator/.test(t)); await shot('help');
} finally { await b.close(); }
process.exit(bad ? 1 : 0);
