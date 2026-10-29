// Real-stack e2e: every login sees the project from ITS side only (P), and organization administration (O).
// Usage (from web/): E2E_CREDS=../data/e2e/creds.json RESULT_PREFIX=e2e-persp node scripts/e2e-persp.mjs P O
import fs from 'node:fs';
import { api, BASE, check, createProject, dbExec, driveProject, finish, login, openTab, shot, sleep } from './e2e-lib.mjs';

const phases = process.argv.slice(2).length ? process.argv.slice(2) : ['P'];
const ROLES = ['buyer', 'compliance', 'quality', 'legal', 'finance'];
const API = 'http://127.0.0.1:8005/api/v1';
const creds = JSON.parse(fs.readFileSync(process.env.E2E_CREDS ?? '../data/e2e/creds.json', 'utf8'));
const noErrors = (page) => check(`${page.who}: no uncaught page errors`, page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
async function actors() { const a = {}; for (const r of ROLES) a[r] = await login(r); return a; }
const WORKFLOW_WORDS = /Referee|orchestrator|Agents & workflow|Agent activity|Hash chain|AI agent|subagent|mandate|payload_hash/i;
const LEAK_KEYS = /orchestrator|from_agent|to_agent|prev_hash|payload_hash|subagent|unit_cost|min_margin/;
const rawLogin = async (email, password) => {
  const r = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
  const cookie = r.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  return { status: r.status, cookie };
};
const me = async (cookie) => (await fetch(`${API}/auth/me`, { headers: { Cookie: cookie } })).json();
// the supplier agents an organization owns (phase N may hand a second one to supplier2's organization; owning several is legitimate)
const ownSuppliers = (email) => new Set(dbExec('select s.id from supplier s join app_user u on u.org_id = s.org_id where u.email = ?', [email]).map((r) => r[0]));
const navLinks = (p) => p.$$eval('.nav a', (a) => a.map((x) => x.textContent.replace(/\d+/g, '').trim()));
const bizInfo = (p) => p.evaluate(() => ({
  tabs: document.querySelectorAll('[role=tab]').length, feed: document.querySelectorAll('.feed').length, text: document.body.innerText,
  cands: document.querySelectorAll('section[aria-label="Candidate suppliers"] tbody tr').length, ladder: !!document.querySelector('svg.ladder circle, svg.ladder path'),
  decisions: document.querySelectorAll('section[aria-label="Decisions"] li').length,
}));
const visit = async (p, pid) => { p.bodies.length = 0; await p.goto(`${BASE}/projects/${pid}`, { waitUntil: 'networkidle2' }); await p.waitForSelector('.ws-head', { timeout: 15000 }); await sleep(2000); };

if (phases.includes('P')) {
  const A = await actors();
  const buyer = A.buyer;
  const sup1 = await login('supplier1'), sup2 = await login('supplier2');
  const admin = await login('admin'), orgadmin = await login('orgadmin');
  const sa1 = await login('supplier1admin'), sa2 = await login('supplier2admin');
  const pid = await createProject(buyer, 'E2E perspectives', 'happy');
  const { final } = await driveProject(A, buyer, pid, () => ({ act: 'approve' }), 180000);
  check('P0 happy project reaches DONE/DEAL', final.status === 'DONE' && final.outcome === 'DEAL', `${final.status}/${final.outcome}`);
  await sleep(1500);

  // ---- every buyer-side business role: the business view only
  for (const role of ROLES) {
    const page = A[role];
    await visit(page, pid);
    const v = await bizInfo(page);
    const nav = await navLinks(page);
    check(`P1 ${role}: navigation is Projects / Approvals only`, nav.join(',') === 'Projects,Approvals', nav.join(','));
    check(`P1 ${role}: no tabs, no agent feed, no referee / agent / audit wording`, v.tabs === 0 && v.feed === 0 && !WORKFLOW_WORDS.test(v.text), (v.text.match(WORKFLOW_WORDS) ?? [''])[0]);
    check(`P1 ${role}: sees candidate suppliers (with names), prices and decisions`, v.cands >= 3 && v.ladder && v.decisions >= 5, `${v.cands}/${v.ladder}/${v.decisions}`);
    const net = page.bodies.map((b) => b.body).join('\n');
    check(`P1 ${role}: no workflow internals in any response the page received`, !LEAK_KEYS.test(net), (net.match(LEAK_KEYS) ?? [''])[0]);
  }
  await shot(buyer, 'P1-buyer-business-view');
  const apiProbe = {};
  for (const path of ['messages', 'audit', 'stream', 'activity', 'audit/verify', 'audit/export']) apiProbe[path] = (await api(buyer, 'GET', `/projects/${pid}/${path}`)).status;
  check('P1 buyer API: the raw workflow endpoints are refused (403)', Object.values(apiProbe).every((s) => s === 403), JSON.stringify(apiProbe));
  const bp = (await api(buyer, 'GET', `/projects/${pid}`)).json;
  check('P1 buyer API: project omits run internals', !['llm_mode', 'tokens_used', 'config_version', 'buyer_org_id'].some((k) => k in bp) && !('scenario' in (bp.brief ?? {})), Object.keys(bp).join(','));
  const bov = (await api(buyer, 'GET', `/projects/${pid}/overview`)).json;
  check('P1 buyer API: overview is available', bov.perspective === 'buyer');
  check('P1 buyer: every negotiation turn was judged (both sides), verdicts carry no prices', Array.isArray(bov.negotiation_review) && bov.negotiation_review.length >= 2
    && new Set(bov.negotiation_review.map((t) => t.actor)).size === 2 && !/\d+\.\d{2}/.test(JSON.stringify(bov.negotiation_review)), JSON.stringify(bov.negotiation_review ?? []).slice(0, 120));
  check('P1 buyer: the page shows a Negotiation review section', (await bizInfo(buyer)).text.includes('Negotiation review'));
  const vfy = (await api(admin, 'GET', `/projects/${pid}/audit/verify`)).json;
  check('P1 admin: audit verify also matches the external anchor file', vfy.valid === true && vfy.external_anchor?.anchors >= 1 && vfy.external_anchor.matches === true, JSON.stringify(vfy.external_anchor));

  // ---- suppliers: only their own thread
  const supplierOf = [];
  for (const [k, page] of [['supplier1', sup1], ['supplier2', sup2]]) if ((await api(page, 'GET', '/projects')).json.items.some((p) => p.id === pid)) supplierOf.push([k, page]);
  check('P2 at least one invited supplier user can see the project', supplierOf.length >= 1, supplierOf.map((x) => x[0]).join(','));
  for (const [k, page] of supplierOf) {
    await visit(page, pid);
    const v = await bizInfo(page);
    const nav = await navLinks(page);
    check(`P2 ${k}: navigation is Projects only`, nav.join(',') === 'Projects', nav.join(','));
    check(`P2 ${k}: no tabs / feed / workflow wording, no buyer-side sections`, v.tabs === 0 && v.feed === 0 && !WORKFLOW_WORDS.test(v.text) && v.cands === 0 && v.decisions === 0, (v.text.match(WORKFLOW_WORDS) ?? [''])[0]);
    check(`P2 ${k}: any negotiation verdicts shown are about its own conduct only`, ((await api(page, 'GET', `/projects/${pid}/overview`)).json.negotiation_review ?? []).every((t) => t.actor === 'you'));
    check(`P2 ${k}: sees its own quotes`, /Your quotes and the buyer/.test(v.text));
    const ov = (await api(page, 'GET', `/projects/${pid}/overview`)).json;
    const own = ownSuppliers(creds[k][0]);
    check(`P2 ${k}: overview is the supplier perspective with only offers of the supplier agents its organization owns`, ov.perspective === 'supplier' && ov.offers.every((o) => own.has(o.supplier_id)), JSON.stringify(ov).slice(0, 160));
    const net = page.bodies.map((b) => b.body).join('\n');
    check(`P2 ${k}: no buyer title or buyer internals in the responses`, !net.includes('E2E perspectives') && !LEAK_KEYS.test(net.replace(/from_agent|to_agent/g, '')));
  }

  // ---- organization admin (buyer org): the agent workflow of its own org, own-side activity only
  await visit(orgadmin, pid);
  const oTabs = await orgadmin.$$eval('[role=tab]', (t) => t.map((x) => x.textContent));
  check('P3 org admin sees Overview / Agents & workflow / Agent activity tabs', oTabs.join('|') === 'Overview|Agents & workflow|Agent activity', oTabs.join('|'));
  check('P3 org admin navigation includes Organization (not Studio / Settings)', (await navLinks(orgadmin)).join(',') === 'Projects,Approvals,Organization', (await navLinks(orgadmin)).join(','));
  await openTab(orgadmin, /Agents & workflow/);
  const wf = await orgadmin.evaluate(() => ({ rows: document.querySelectorAll('.feed .frow').length, audit: document.querySelectorAll('.tl li').length, verified: /Hash chain verified/.test(document.querySelector('.ws-side')?.textContent ?? '') }));
  check('P3 org admin workflow tab: conversation, audit timeline, hash chain verified', wf.rows > 5 && wf.audit > 10 && wf.verified, JSON.stringify(wf));
  await shot(orgadmin, 'P3-orgadmin-workflow');
  await openTab(orgadmin, /Agent activity/);
  const act = await orgadmin.evaluate(() => ({ rows: document.querySelectorAll('section[aria-label="Agent calls"] tbody tr').length, agents: [...document.querySelectorAll('section[aria-label="Agent calls"] tbody tr td:nth-child(2)')].map((x) => x.textContent) }));
  check('P3 org admin activity: calls listed, buyer agent only (never the suppliers\' agents)', act.rows > 3 && act.agents.every((a) => a === 'Buyer agent'), JSON.stringify([act.rows, [...new Set(act.agents)]]));
  await shot(orgadmin, 'P3-orgadmin-activity');

  // ---- supplier organization admin: supplier-side workflow, own agents only
  for (const [k, page] of [['supplier1admin', sa1], ['supplier2admin', sa2]]) {
    if (!(await api(page, 'GET', '/projects')).json.items.some((p) => p.id === pid)) continue;
    await visit(page, pid);
    check(`P4 ${k}: navigation is Projects / Organization (no Approvals, Studio, Settings)`, (await navLinks(page)).join(',') === 'Projects,Organization', (await navLinks(page)).join(','));
    await openTab(page, /Agents & workflow/);
    await openTab(page, /Agent activity/);
    const a = await page.evaluate(() => ({ rows: document.querySelectorAll('section[aria-label="Agent calls"] tbody tr').length, agents: [...document.querySelectorAll('section[aria-label="Agent calls"] tbody tr td:nth-child(2)')].map((x) => x.textContent) }));
    const own = ownSuppliers(creds[k][0]);
    check(`P4 ${k}: activity lists only the supplier agents its organization owns`, a.rows > 0 && a.agents.every((x) => /^Supplier agent S\d+$/.test(x) && own.has(x.slice('Supplier agent '.length))), JSON.stringify([a.rows, [...new Set(a.agents)], [...own]]));
    const probes = [(await api(page, 'GET', `/projects/${pid}/escalations`)).status, (await api(page, 'GET', `/projects/${pid}/audit/verify`)).status, (await api(page, 'POST', `/projects/${pid}/stop`)).status];
    check(`P4 ${k}: buyer-side endpoints refused (escalations, verify, stop)`, probes.every((s) => s === 403), probes.join(','));
    const msgs = (await api(page, 'GET', `/projects/${pid}/messages?limit=500`)).json.items;
    check(`P4 ${k}: raw messages involve only the supplier agents its organization owns`, msgs.flatMap((m) => [m.from_agent, m.to_agent]).filter((x) => x?.startsWith('supplier:')).every((x) => own.has(x.slice('supplier:'.length))));
    await shot(page, `P4-${k}-activity`);
  }

  // ---- SBN super admin: everything
  await visit(admin, pid);
  check('P5 super admin navigation: Projects / Approvals / Organizations / Studio / Settings', (await navLinks(admin)).join(',') === 'Projects,Approvals,Organizations,Studio,Settings', (await navLinks(admin)).join(','));
  await openTab(admin, /Agent activity/);
  const all = await admin.evaluate(() => [...new Set([...document.querySelectorAll('section[aria-label="Agent calls"] tbody tr td:nth-child(2)')].map((x) => /^Buyer/.test(x.textContent) ? 'buyer' : 'supplier'))]);
  check('P5 super admin activity shows BOTH the buyer agent and the supplier agents', all.includes('buyer') && all.includes('supplier'), all.join(','));
  await shot(admin, 'P5-super-activity');
  for (const p of [...Object.values(A), sup1, sup2, admin, orgadmin, sa1, sa2]) noErrors(p);
  if (phases.length === 1) await finish();
}

if (phases.includes('O')) {
  const orgadmin = await login('orgadmin'), admin = await login('admin'), buyer = await login('buyer');
  const flash = async (p) => (await p.$$eval('.toast', (t) => t.map((x) => x.textContent))).join('|');
  const rowOf = (p, email) => p.evaluateHandle((e) => [...document.querySelectorAll('table tbody tr')].find((r) => r.textContent.includes(e)), email);
  const clickIn = async (p, email, label) => { const r = await rowOf(p, email); await r.evaluate((row, l) => [...row.querySelectorAll('button')].find((b) => b.textContent.trim() === l).click(), label); };
  const secret = async (p) => { await p.waitForSelector('.dialog code[aria-label="One-time password"]', { timeout: 10000 }); const t = await p.$eval('.dialog code[aria-label="One-time password"]', (e) => e.textContent); const msg = await p.$eval('.dialog', (e) => e.textContent); await p.evaluate(() => [...document.querySelectorAll('.dialog button')].find((b) => /saved it/.test(b.textContent)).click()); await sleep(400); return { pw: t, msg }; };

  // O1: team of ITS OWN organization
  await orgadmin.goto(`${BASE}/organization`, { waitUntil: 'networkidle2' }); await orgadmin.waitForSelector('table tbody tr'); await sleep(800);
  const team = await orgadmin.$$eval('table tbody tr', (r) => r.map((x) => x.textContent));
  check('O1 team lists the organization\'s people (buyer-side roles + itself marked "you")', team.length >= 6 && team.some((t) => /you/.test(t) && t.includes(creds.orgadmin[0])), `${team.length}`);
  check('O1 another organization\'s people are not listed', !team.some((t) => t.includes(creds.buyer2[0]) || t.includes(creds.supplier1[0])));
  await shot(orgadmin, 'O1-team');
  const other = (await api(admin, 'GET', '/admin/orgs')).json.items.find((o) => o.name.startsWith('Globex'));
  check('O1 org admin cannot address another organization (404, not 403)', (await api(orgadmin, 'GET', `/org/users?org_id=${other.id}`)).status === 404);

  // O2: add a person through the form -> one-time password -> they can sign in
  const email = `new.person.${Date.now()}@sourceloop.test`;
  await orgadmin.type('#nu-e', email); await orgadmin.select('#nu-r', 'COMPLIANCE');
  await orgadmin.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Add person').click());
  const s1 = await secret(orgadmin);
  check('O2 one-time password is shown with the "only now" warning', /only now/.test(s1.msg) && s1.pw.length >= 14, s1.msg.slice(0, 80));
  let L = await rawLogin(email, s1.pw);
  check('O2 the new person can sign in with it, as Compliance', L.status === 200 && (await me(L.cookie)).role === 'COMPLIANCE');
  await orgadmin.reload({ waitUntil: 'networkidle2' }); await orgadmin.waitForSelector('table tbody tr'); await sleep(500);
  check('O2 the password is not retrievable afterwards (page + API)', !(await orgadmin.evaluate(() => document.body.innerText)).includes(s1.pw) && !JSON.stringify((await api(orgadmin, 'GET', '/org/users')).json).includes(s1.pw));

  // O3: role change and disable / enable
  const roleSel = await (await rowOf(orgadmin, email)).evaluateHandle((r) => r.querySelector('select'));
  await roleSel.select('LEGAL'); await sleep(1200);
  check('O3 role change is immediate', (await me(L.cookie)).role === 'LEGAL');
  await clickIn(orgadmin, email, 'Disable'); await sleep(1200);
  const dis = await rawLogin(email, s1.pw);
  check('O3 disabled: sign-in refused and the live session ended', dis.status === 401 && (await fetch(`${API}/auth/me`, { headers: { Cookie: L.cookie } })).status === 401, `${dis.status}`);
  await clickIn(orgadmin, email, 'Enable'); await sleep(1200);
  L = await rawLogin(email, s1.pw);
  check('O3 enabled again: can sign in', L.status === 200);

  // O4: reset password
  await clickIn(orgadmin, email, 'Reset password');
  const s2 = await secret(orgadmin);
  const oldTry = await rawLogin(email, s1.pw); const newTry = await rawLogin(email, s2.pw);
  check('O4 reset: old password stops working, the new one works', oldTry.status === 401 && newTry.status === 200);
  check('O4 org admin cannot disable itself (guard rail)', (await api(orgadmin, 'PATCH', `/org/users/${(await api(orgadmin, 'GET', '/auth/me')).json.id}`, { disabled: true })).status === 409);

  // O5: own-side agent settings (versioned overrides) through the UI
  await orgadmin.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((t) => /Agent settings/.test(t.textContent)).click());
  await orgadmin.waitForSelector('input[aria-label="Buyer lead agent timeout"]'); await sleep(500);
  const names = await orgadmin.$$eval('section table tbody tr td:first-child b', (b) => b.map((x) => x.textContent));
  check('O5 org admin only sees ITS side\'s agents (buyer agents, no supplier agent)', names.includes('Buyer lead agent') && !names.some((n) => /Supplier lead|Quote|Demand|Capacity and margin|Buyer verification/.test(n)), names.join(','));
  await orgadmin.click('input[aria-label="Buyer lead agent timeout"]', { clickCount: 3 }); await orgadmin.type('input[aria-label="Buyer lead agent timeout"]', '33');
  await orgadmin.type('input[aria-label="What changed"]', 'e2e: faster buyer lead');
  await orgadmin.evaluate(() => [...document.querySelectorAll('button')].find((b) => /Save as a new version/.test(b.textContent)).click());
  await sleep(1500);
  check('O5 saving confirms a new version', /version 1/i.test(await flash(orgadmin)), await flash(orgadmin));
  const hist = await orgadmin.$$eval('section table tbody tr', (r) => r.map((x) => x.textContent));
  check('O5 version history shows v1 as active', hist.some((t) => /v1/.test(t) && /active/.test(t)), hist.slice(-2).join('|').slice(0, 120));
  await shot(orgadmin, 'O5-agent-settings');
  const bad = await api(orgadmin, 'PUT', '/org/overrides', { payload: { agents: { supplier_lead: { timeout_s: 5 } } } });
  check('O5 an override for the OTHER side\'s agent is rejected with the reason (422)', bad.status === 422 && bad.json.error.code === 'OVERRIDE_REJECTED' && JSON.stringify(bad.json).includes('NOT_YOUR_AGENT'), JSON.stringify(bad.json).slice(0, 140));
  const bad2 = await api(orgadmin, 'PUT', '/org/overrides', { payload: { agents: { buyer_lead: { enabled: false } } } });
  check('O5 switching an agent off is not even expressible (422)', bad2.status === 422);
  const pid = await createProject(buyer, 'E2E override pin', 'happy');
  const pinned = (await api(admin, 'GET', `/projects/${pid}`)).json;
  check('O5 a new project pins the override version (visible to an inspector)', pinned.brief?.org_overrides?.buyer?.version === 1, JSON.stringify(pinned.brief?.org_overrides));
  check('O5 the buyer\'s own view does not expose the pin', !('org_overrides' in ((await api(buyer, 'GET', `/projects/${pid}`)).json.brief ?? {})));

  // O6: SBN super admin: organizations
  await admin.goto(`${BASE}/organization`, { waitUntil: 'networkidle2' }); await admin.waitForSelector('table tbody tr'); await sleep(800);
  const orgs = await admin.$$eval('table tbody tr', (r) => r.map((x) => x.textContent));
  check('O6 super admin sees every organization (buyers, suppliers, SBN)', orgs.length >= 3 && orgs.some((t) => /Acme/.test(t)) && orgs.some((t) => /Globex/.test(t)), `${orgs.length}`);
  const oname = `Initech ${Date.now() % 100000}`; const aemail = `boss.${Date.now()}@initech.test`;
  await admin.type('#co-n', oname); await admin.type('#co-e', aemail);
  await admin.evaluate(() => [...document.querySelectorAll('form button')].find((b) => b.textContent.trim() === 'Create').click());
  const s3 = await secret(admin);
  const NL = await rawLogin(aemail, s3.pw);
  const nUsers = await (await fetch(`${API}/org/users`, { headers: { Cookie: NL.cookie } })).json();
  check('O6 new organization: its admin signs in, is an org ADMIN, and sees only its own one-person team', NL.status === 200 && (await me(NL.cookie)).role === 'ADMIN' && nUsers.items.length === 1 && nUsers.items[0].email === aemail, `${NL.status}/${nUsers.items?.length}`);
  await admin.evaluate((n) => [...document.querySelectorAll('table tbody tr')].find((r) => r.textContent.includes(n)).querySelector('button').click(), oname); await sleep(1500);
  const pageTxt = await admin.evaluate(() => document.body.innerText);
  check('O6 super admin can open the organization and manage its team', new RegExp(`Managing:\\s*${oname}`).test(pageTxt) && pageTxt.includes(aemail), pageTxt.slice(0, 160));
  await shot(admin, 'O6-super-org');
  const ns = await api(orgadmin, 'GET', '/admin/orgs');
  check('O6 an org admin cannot list organizations (403)', ns.status === 403);
  for (const p of [orgadmin, admin, buyer]) noErrors(p);
  if (phases[phases.length - 1] === 'O') await finish();
}
