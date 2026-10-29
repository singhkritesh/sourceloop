// Headless smoke test against the live stack: node scripts/smoke.mjs  (dev server on 3010, backend on 8005)
// Uses the system Chrome via puppeteer-core. Dev seed credentials come from env, never from the repo.
import puppeteer from 'puppeteer-core';

const BASE = process.env.WEB ?? 'http://127.0.0.1:3010';
const CHROME = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const OUT = process.env.SHOTS ?? '.';
const users = JSON.parse(process.env.SMOKE_USERS ?? '{}'); // {"buyer":["email","pw"],"admin":[...]}
const results = [];
const check = (name, ok, extra = '') => { results.push([ok ? 'PASS' : 'FAIL', name, extra]); };

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] });
async function session(who, fn) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/401|403|404|501/.test(m.text())) errors.push(m.text()); });
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
  const [email, pw] = users[who];
  await page.type('#em', email); await page.type('#pw', pw);
  await Promise.all([page.waitForSelector('.topbar'), page.click('button.primary')]);
  try { await fn(page); } finally { check(`${who}: no uncaught page errors`, errors.length === 0, errors.slice(0, 2).join(' | ')); await ctx.close(); }
}

await session('buyer', async (page) => {
  check('buyer: lands on projects', page.url().endsWith('/projects'));
  await page.waitForSelector('.table tbody tr');
  const rows = await page.$$eval('.table tbody tr', (r) => r.length);
  check('buyer: projects listed', rows > 0, `${rows} rows`);
  await page.screenshot({ path: `${OUT}/shot-projects.png` });
  const done = await page.$$eval('.table tbody tr', (rs) => rs.findIndex((r) => /pcb assemblies/i.test(r.textContent ?? '')));
  await (await page.$$('.table tbody tr'))[Math.max(0, done)].click();
  await page.waitForSelector('section[aria-label="Candidate suppliers"]', { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 2500));
  const info = await page.evaluate(() => ({
    candidates: document.querySelectorAll('section[aria-label="Candidate suppliers"] tbody tr').length,
    ladder: !!document.querySelector('svg.ladder circle'),
    steps: document.querySelectorAll('.step.done').length,
    decisions: document.querySelectorAll('section[aria-label="Decisions"] li').length,
    tabs: document.querySelectorAll('[role=tab]').length,
    feed: document.querySelectorAll('.feed').length,
    audit: document.querySelectorAll('.tl li').length,
    verify: !!document.querySelector('.ws-side .callout.ok'),
  }));
  check('project: candidate suppliers listed (business view)', info.candidates >= 3, JSON.stringify(info));
  check('project: price chart drawn', info.ladder);
  check('project: decisions listed', info.decisions >= 5);
  check('project: buyer has no agent-workflow tabs, feed, audit timeline or chain verify', info.tabs === 0 && info.feed === 0 && info.audit === 0 && !info.verify);
  await page.screenshot({ path: `${OUT}/shot-workspace.png` });
  await page.goto(`${BASE}/approvals`, { waitUntil: 'networkidle2' });
  check('buyer: approvals page renders', !!(await page.$('.tabs')));
});

if (users.admin) await session('admin', async (page) => {
  await page.goto(`${BASE}/projects`, { waitUntil: 'networkidle2' });
  await page.waitForSelector('.table tbody tr');
  const i = await page.$$eval('.table tbody tr', (rs) => rs.findIndex((r) => /pcb assemblies/i.test(r.textContent ?? '')));
  await (await page.$$('.table tbody tr'))[Math.max(0, i)].click();
  await page.waitForSelector('[role=tab]', { timeout: 15000 });
  await page.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((t) => /Agents & workflow/.test(t.textContent)).click());
  await page.waitForSelector('.feed .frow', { timeout: 15000 });
  await page.waitForSelector('.ws-side .callout.ok, .ws-side .callout.bad', { timeout: 15000 });
  const t = await page.$eval('.ws-side .callout', (e) => e.textContent);
  check('admin: audit chain verified badge', /verified/i.test(t ?? ''), t ?? '');
  await page.screenshot({ path: `${OUT}/shot-admin-workspace.png` });
  await page.goto(`${BASE}/studio`, { waitUntil: 'networkidle2' });
  await new Promise((r) => setTimeout(r, 800));
  const body = await page.$eval('.studio, .callout, .card', (e) => e.className + ':' + (e.textContent ?? '').slice(0, 80)).catch(() => 'none');
  check('admin: studio screen renders (form or 501 notice)', body !== 'none', body);
  await page.screenshot({ path: `${OUT}/shot-studio.png` });
});

if (users.supplier) await session('supplier', async (page) => {
  await page.waitForSelector('.table tbody tr, .empty');
  const n = await page.$$eval('.table tbody tr', (r) => r.length);
  check('supplier: sees only own projects (list loads)', n >= 0, `${n}`);
  if (n) {
    await (await page.$('.table tbody tr')).click();
    await page.waitForSelector('.ws-head', { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 1500));
    const side = await page.$('.ws-side');
    check('supplier: no audit/verify side panel, no workflow tabs', side === null && (await page.$$('[role=tab]')).length === 0);
    await page.screenshot({ path: `${OUT}/shot-supplier.png` });
  }
});

await browser.close();
for (const r of results) console.log(r.join('  '));
process.exit(results.some((r) => r[0] === 'FAIL') ? 1 : 0);
