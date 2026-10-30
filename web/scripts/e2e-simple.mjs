// First-run journey against the Docker stack (http://localhost:3010), live mode: the local model reads what the buyer types and the
// agents run; the buyer is asked only for what truly needs a person.
//   node scripts/e2e-simple.mjs      (no credentials needed; screenshots to ../data/ui/simple-*.png)
import puppeteer from 'puppeteer-core';
const BASE = process.env.WEB ?? 'http://localhost:3010';
const CHROME = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let bad = 0; const check = (n, ok, x = '') => { if (!ok) bad++; console.log(ok ? 'PASS' : 'FAIL', n, x); };
const b = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'] });
const p = await b.newPage(); await p.setViewport({ width: 1280, height: 900 });
const text = () => p.evaluate(() => document.body.innerText);
const click = (label) => p.evaluate((l) => { const e = [...document.querySelectorAll('button,a')].find((x) => x.textContent.trim().includes(l)); if (e) e.click(); return !!e; }, label);
const shot = (n) => p.screenshot({ path: `../data/ui/simple-${n}.png` });
// A second person, without the browser: the awarded supplier's user confirms its final terms (found through the SBN admin's view).
async function asUser(email) {
  const r = await fetch(BASE + '/api/v1/auth/demo-login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email }) });
  const jar = r.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const csrf = /csrf=([^;]+)/.exec(jar)?.[1] ?? '';
  const call = (path, init = {}) => fetch(BASE + '/api/v1' + path, { ...init, headers: { 'content-type': 'application/json', cookie: jar, 'x-csrf-token': csrf }, body: init.body && JSON.stringify(init.body) });
  return { get: async (path) => (await call(path)).json(), post: async (path, body) => call(path, { method: 'POST', body }) };
}
const supplierConfirmed = [];
async function supplierWatch(until) {
  const admin = await asUser('superadmin@sourceloop.test');
  while (!until()) {
    await sleep(3000);
    const items = ((await admin.get('/approvals?status=PENDING')).items ?? []).filter((a) => a.kind === 'SUPPLIER_ACCEPT');
    for (const a of items) {
      const sup = await asUser(`supplier-${a.evidence.supplier.toLowerCase()}@sourceloop.test`);
      const r = await sup.post(`/approvals/${a.id}/resolve`, { status: 'APPROVED', version: a.version });
      if (r.status === 200) supplierConfirmed.push(a.evidence.supplier);
    }
  }
}
let finished = false;
const NEED = 'We need 500 PCB assemblies certified IATF16949 and RoHS, delivered within 8 weeks. Please avoid suppliers in Turkey. Target price around 95 per unit.';
try {
  await p.goto(BASE + '/login');
  await p.waitForFunction(() => /Choose a role/i.test(document.body.innerText), { timeout: 15000 });
  const t0 = await text();
  check('login is a role picker with no password field', !(await p.$('#pw')) && /Buyer/.test(t0));
  check('only buyers, suppliers and admins are offered', !/Compliance|Quality|Legal|Finance|Evaluator|Designer/.test(t0));
  await shot('login');
  await p.evaluate(() => { [...document.querySelectorAll('button')].find((x) => /^Buyer/.test(x.textContent.trim()))?.click(); });
  await p.waitForFunction(() => /Sourcing projects/.test(document.body.innerText), { timeout: 15000 });
  let t = await text();
  check('no scripted sample button and no demo banner in live mode', !/Run a sample project/.test(t) && !/Demo mode/i.test(t));
  await click('New sourcing project'); await sleep(500);
  await p.type('#np-t', 'PCB assemblies Q4'); await p.type('#np-n', NEED);
  t = await text();
  check('scenario picker is hidden in live mode', !(await p.$('.scenario-grid')) && /agent reads this/i.test(t));
  await shot('newproject');
  await click('Create and start');
  await p.waitForFunction(() => /\/projects\/[0-9a-f-]{36}/.test(location.pathname), { timeout: 20000 });
  const projectUrl = p.url();
  check('the typed need is shown back from what the model read', await p.waitForFunction(() => /pcb assembly/i.test(document.body.innerText) && /IATF16949/.test(document.body.innerText), { timeout: 20000 }).then(() => true, () => false));
  const watcher = supplierWatch(() => finished);
  const asked = [];
  for (let i = 0; i < 6; i++) {
    await p.waitForFunction(() => /Your decision is needed|Deal reached/i.test(document.body.innerText), { timeout: 240000 }).catch(() => {});
    t = await text();
    if (/Deal reached/i.test(t) && !/Your decision is needed/i.test(t)) break;
    asked.push((t.match(/Your decision is needed: ([^\n]+)/i) ?? [])[1] ?? '?');
    await p.goto(BASE + '/approvals'); await sleep(1500);
    await p.evaluate(() => document.querySelector('button.appr')?.click()); await sleep(900);
    await click('Approve'); await sleep(2500);
    await p.goto(projectUrl); await sleep(2000);
  }
  finished = true; await watcher;
  await shot('deal');
  check('run reaches a deal', /Deal reached/i.test(await text()));
  check('the awarded supplier confirmed its own terms (its user, not the buyer)', supplierConfirmed.length === 1, supplierConfirmed.join(','));
  check('the buyer was asked at most 3 times, and only for real decisions', asked.length >= 1 && asked.length <= 3, asked.join(' | '));
  // an SBN admin can see how the agents performed: which choices the model made, which fell back to the rule, and how sure it was
  await p.evaluate(() => document.querySelector('button[aria-label="Sign out"]')?.click());
  await p.waitForFunction(() => /Choose a role/i.test(document.body.innerText), { timeout: 15000 });
  await p.evaluate(() => { [...document.querySelectorAll('button')].find((x) => /^SBN admin/.test(x.textContent.trim()))?.click(); });
  await p.waitForFunction(() => /Sourcing projects/.test(document.body.innerText), { timeout: 15000 });
  await p.goto(projectUrl); await sleep(1500);
  await click('Agent performance'); await sleep(1500);
  await p.waitForFunction(() => /Every decision|No model made a choice/i.test(document.body.innerText), { timeout: 15000 }).catch(() => {});
  t = await text(); await shot('performance');
  check('agent performance shows the model-vs-rule decisions', /Every decision/.test(t) && /by the model/.test(t) && /Buyer agent/.test(t), t.includes('Every decision') ? '' : t.slice(0, 120));
  await p.goto(BASE + '/help'); await sleep(800); t = await text();
  check('help page has glossary', /Glossary/i.test(t) && /Orchestrator/.test(t)); await shot('help');
} finally { await b.close(); }
process.exit(bad ? 1 : 0);
