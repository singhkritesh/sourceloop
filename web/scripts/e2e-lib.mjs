// Full real-stack UI journey (headless Chrome via puppeteer-core): dev server on 3010 -> API on 8005.
//   E2E_CREDS=../data/e2e/creds.json node scripts/e2e.mjs [phase ...]      phases: B C D E F (default: B)
// Credentials come from a gitignored file (the seed output), never from the repo. Shots/evidence go to $SHOTS (default ../data/e2e).
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

export const BASE = process.env.WEB ?? 'http://127.0.0.1:3010';
const CHROME = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
export const OUT = process.env.SHOTS ?? '../data/e2e';
const CREDS = JSON.parse(fs.readFileSync(process.env.E2E_CREDS ?? '../data/e2e/creds.json', 'utf8'));
const DB = process.env.E2E_DB ?? '../data/e2e/e2e.db';
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const results = [];
export const check = (name, ok, extra = '') => { results.push([ok ? 'PASS' : 'FAIL', name, String(extra).slice(0, 240)]); console.log(ok ? 'PASS' : 'FAIL', name, String(extra).slice(0, 240)); };
fs.mkdirSync(OUT, { recursive: true });

export const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] });

/** Log in as a role in an isolated context. Records every /api response body for later scanning. */
export async function login(who, { w = 1440, h = 900 } = {}) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: w, height: h });
  page.who = who; page.ctx = ctx; page.errors = []; page.bodies = [];
  page.on('pageerror', (e) => page.errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/401|403|404|409|413|422|429|Failed to load resource/.test(m.text())) page.errors.push(m.text()); });
  page.on('response', async (r) => {
    const u = r.url();
    if (!u.includes('/api/v1/') || u.endsWith('/stream') || /\/stream\?/.test(u)) return;
    try { page.bodies.push({ url: u, status: r.status(), body: await r.text() }); } catch { /* body unavailable (redirect) */ }
  });
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2' });
  const [email, pw] = CREDS[who];
  await page.type('#em', email); await page.type('#pw', pw);
  await Promise.all([page.waitForSelector('.topbar'), page.click('button.primary')]);
  return page;
}
/** An inspector (SBN super admin / evaluator / an org admin) opens a project's agent workflow tab. Business users have no such tab. */
export async function openWorkflow(page, pid) {
  await page.goto(`${BASE}/projects/${pid}`, { waitUntil: 'networkidle2' });
  await page.waitForSelector('[role=tab]', { timeout: 15000 });
  await page.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((t) => /Agents & workflow/.test(t.textContent)).click());
  await page.waitForSelector('.ws.wf', { timeout: 15000 });
  await sleep(1500);
}
export async function openTab(page, re) {
  await page.evaluate((src) => [...document.querySelectorAll('[role=tab]')].find((t) => new RegExp(src).test(t.textContent)).click(), re.source);
  await sleep(1200);
}
export const shot = (page, name) => page.screenshot({ path: `${OUT}/${name}.png` });

/** fetch from inside the page (cookies + CSRF like the app) */
export function api(page, method, path, body) {
  return page.evaluate(async (method, path, body) => {
    const csrf = document.cookie.split('; ').find((c) => c.startsWith('csrf='))?.slice(5) ?? '';
    const r = await fetch(`/api/v1${path}`, { method, credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: body === undefined ? undefined : JSON.stringify(body) });
    let j = null; try { j = await r.json(); } catch { /* empty */ }
    return { status: r.status, json: j };
  }, method, path, body);
}

export async function createProject(page, title, scenario, { start = true } = {}) {
  await page.goto(`${BASE}/projects`, { waitUntil: 'networkidle2' });
  await page.evaluate(() => [...document.querySelectorAll('button')].find((b) => /New sourcing project/.test(b.textContent)).click());
  await page.waitForSelector('#np-t');
  await page.type('#np-t', title);
  await page.evaluate((sc) => { const t = { happy: 'Happy path', no_deal: 'No deal', below_floor: 'Below floor', locked_clause: 'Locked clause', poisoned_news: 'Poisoned news', inflated_claims: 'Inflated claims' }[sc]; [...document.querySelectorAll('button[role=radio]')].find((b) => b.textContent.startsWith(t)).click(); }, scenario);
  if (!start) await page.evaluate(() => document.querySelector('form#newproj input[type=checkbox]').click());
  await Promise.all([page.waitForFunction(() => /\/projects\/[0-9a-f-]{36}/.test(location.pathname)), page.click('button[form=newproj]')]);
  return page.url().split('/projects/')[1];
}

export async function projectState(page, id) {
  const r = await api(page, 'GET', `/projects/${id}`);
  return r.json;
}

/**
 * One pass of an approver: open each pending item in the inbox and apply decide(kind, projectId) -> {act:'approve'|'reject'|'edit'|'skip', ...}.
 * Returns the list of resolved items.
 */
export async function approverPass(page, decide, log) {
  await page.goto(`${BASE}/approvals`, { waitUntil: 'networkidle2' });
  const done = [];
  for (let guard = 0; guard < 12; guard++) {
    await page.waitForSelector('.inbox .appr, .empty', { timeout: 8000 }).catch(() => null);
    const n = await page.$$eval('.inbox .appr', (e) => e.filter((b) => /pending/i.test(b.textContent)).length);
    let handled = false;
    for (let i = 0; i < n; i++) {
      const rows = (await page.$$('.inbox .appr')).filter(Boolean);
      const idxs = await page.$$eval('.inbox .appr', (e) => e.map((b, i) => [i, /pending/i.test(b.textContent)]).filter((x) => x[1]).map((x) => x[0]));
      if (i >= idxs.length) break;
      await rows[idxs[i]].click();
      await page.waitForSelector('.drawer h2');
      const info = await page.evaluate(() => ({
        kind: document.querySelector('.drawer h2 [data-kind]').getAttribute('data-kind'),
        pid: (document.querySelector('.drawer .drawer-head a')?.getAttribute('href') ?? '').split('/projects/')[1],
      }));
      const d = decide(info.kind, info.pid, page);
      if (d.act === 'skip') { await page.keyboard.press('Escape'); await sleep(200); continue; }
      const out = await act(page, info.kind, d);
      await shot(page, `${log?.prefix ?? 'appr'}-${page.who}-${info.kind}-${d.act}`);
      done.push({ kind: info.kind, pid: info.pid, ...d, ...out });
      handled = true; break;
    }
    if (!handled) break;
    await sleep(300);
  }
  return done;
}

async function act(page, kind, d) {
  const clickBtn = (re) => page.evaluate((src) => { const b = [...document.querySelectorAll('.drawer-foot button')].find((x) => new RegExp(src, 'i').test(x.textContent) && !x.disabled); if (!b) return false; b.click(); return true; }, re.source);
  if (d.act === 'approve') { if (!(await clickBtn(/^Approve/))) return { ok: false }; }
  else if (d.act === 'reject') {
    await clickBtn(/^Reject/); await page.waitForSelector('#cm'); await page.type('#cm', d.comment ?? 'Rejected by e2e test run.');
    await clickBtn(/Confirm reject/);
  } else if (d.act === 'edit') {
    await clickBtn(/^Edit/); await page.waitForSelector('#cm'); await page.type('#cm', d.comment ?? 'Edited by e2e test run.');
    const inputs = await page.$$('.drawer-foot fieldset input');
    const labels = await page.$$eval('.drawer-foot fieldset label', (l) => l.map((x) => x.textContent.trim()));
    d.options = labels;
    if (d.pick !== undefined) { const i = typeof d.pick === 'number' ? d.pick : labels.indexOf(d.pick); if (inputs[i]) await inputs[i].click(); }
    await sleep(200);
    await shot(page, `edit-controls-${kind}`);
    if (!(await clickBtn(/Submit edit/))) return { ok: false, options: labels };
  }
  await sleep(900);
  const toast = await page.$$eval('.toast', (t) => t.map((x) => x.textContent));
  const err = await page.$$eval('.err-text', (t) => t.map((x) => x.textContent));
  return { ok: true, toast, err };
}

export async function waitStatus(page, id, pred, ms = 120000, between) {
  const t0 = Date.now(); let p;
  while (Date.now() - t0 < ms) {
    p = await projectState(page, id);
    if (pred(p)) return p;
    if (between) await between(p);
    await sleep(700);
  }
  return p;
}

/** Drive a project to a terminal state by clicking approvals in the UI as the right role. */
export async function driveProject(actors, watcher, id, decide = () => ({ act: 'approve' }), ms = 180000) {
  const resolved = [];
  const final = await waitStatus(watcher, id, (p) => ['DONE', 'FAILED', 'STOPPED', 'FAILED_RECOVERABLE'].includes(p.status), ms, async (p) => {
    if (p.status !== 'ESCALATED' && p.status !== 'RUNNING') return;
    for (const role of ['buyer', 'compliance', 'quality', 'legal', 'finance']) {
      const r = await approverPass(actors[role], (k, pid) => (pid === id ? decide(k, role) : { act: 'skip' }), { prefix: `p-${id.slice(0, 4)}` });
      resolved.push(...r);
    }
  });
  // HOLD_RELEASE is raised after DONE (ONBOARDING): give FINANCE a chance to resolve it through the UI
  if (final.status === 'DONE' && final.outcome === 'DEAL') {
    for (let i = 0; i < 6; i++) {
      await sleep(800);
      const r = await approverPass(actors.finance, (k, pid) => (pid === id ? decide(k, 'finance') : { act: 'skip' }), { prefix: `p-${id.slice(0, 4)}` });
      resolved.push(...r);
      if (r.length) break;
    }
  }
  return { final: await projectState(watcher, id), resolved };
}

export function thresholdsFromDb() {
  const py = `import sqlite3,json,sys\nc=sqlite3.connect(sys.argv[1])\nout=[]\nfor side,payload in c.execute("select side,payload from mandate"):\n  out.append([side,json.loads(payload)])\nprint(json.dumps(out))`;
  return JSON.parse(execFileSync('../.venv/bin/python', ['-c', py, DB], { encoding: 'utf8' }));
}

export function numericLeaves(o, path = '', acc = []) {
  if (o && typeof o === 'object') { for (const [k, v] of Object.entries(o)) numericLeaves(v, `${path}.${k}`, acc); }
  else if (typeof o === 'number' || (typeof o === 'string' && /^\d+(\.\d+)?$/.test(o))) acc.push([path, String(o)]);
  return acc;
}

/** Leak scanner: numeric leaves of `side`'s mandate payloads that appear (as whole numbers) in `hay`. Returns leak descriptions. */
export function scanLeaks(thr, side, hayName, hay) {
  const leaks = [];
  for (const [sd, payload] of thr) {
    if (sd !== side) continue;
    for (const [path, val] of numericLeaves(payload)) {
      if (/max_capacity_units|lead_time_weeks/.test(path)) continue; // public registry attributes, not limits
      if (!(val.includes('.') || val.length >= 4) || /rounds|weeks|pct/.test(path) && val.length < 4) continue;
      if (new RegExp(`(?<![0-9.])${val.replace('.', '\\.')}(?![0-9])`).test(hay)) leaks.push(`${hayName}<-${sd}${path}=${val}`);
    }
  }
  return leaks;
}

/** run a statement against the e2e sqlite DB (fixture manipulation / read-only evidence). params are bound. */
export function dbExec(sql, params = [], db = DB) {
  const py = `import sqlite3,json,sys\nc=sqlite3.connect(sys.argv[1],timeout=30)\ncur=c.execute(sys.argv[2],json.loads(sys.argv[3]))\nrows=cur.fetchall() if cur.description else []\nc.commit()\nprint(json.dumps(rows))`;
  return JSON.parse(execFileSync('../.venv/bin/python', ['-c', py, db, sql, JSON.stringify(params)], { encoding: 'utf8' }));
}

export async function finish() {
  await browser.close();
  console.log('\n==== summary ====');
  for (const r of results) console.log(r.join('  '));
  fs.writeFileSync(`${OUT}/${process.env.RESULT_PREFIX ?? 'e2e-results'}-${process.argv.slice(2).join('') || 'B'}.json`, JSON.stringify(results, null, 1));
  process.exit(results.some((r) => r[0] === 'FAIL') ? 1 : 0);
}
