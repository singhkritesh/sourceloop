// STALE: written for the old role set and six-checkpoint flow; not rerun since the buyer-only change. Current journey: e2e-simple.mjs
// Real-stack e2e journeys. Usage (from web/): E2E_CREDS=../data/e2e/creds.json node scripts/e2e.mjs B [C D E F ...]
// Requires: API on 8005 (fresh DB seeded; creds file from the seed output) and `npm run dev` on 3010.
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { api, check, dbExec, scanLeaks, createProject, driveProject, finish, login, numericLeaves, openWorkflow, OUT, shot, sleep, thresholdsFromDb, waitStatus, BASE, projectState } from './e2e-lib.mjs';

const phases = process.argv.slice(2).length ? process.argv.slice(2) : ['B'];
const ROLES = ['buyer', 'compliance', 'quality', 'legal', 'finance'];
const noErrors = (page) => check(`${page.who}: no uncaught page errors`, page.errors.length === 0, page.errors.slice(0, 2).join(' | '));

async function actors() {
  const a = {};
  for (const r of ROLES) a[r] = await login(r);
  return a;
}

const wsInfo = (page) => page.evaluate(() => ({
  rows: document.querySelectorAll('.feed .frow').length, stages: document.querySelectorAll('.stage-row').length,
  chips: document.querySelectorAll('.chip .conf').length, ladder: !!document.querySelector('svg.ladder circle, svg.ladder path'),
  audit: document.querySelectorAll('.tl li').length, blocks: document.querySelectorAll('.callout.bad').length, esc: document.querySelectorAll('.callout.warn, .callout.ok, .next.warn').length,
  text: document.body.innerText,
}));

if (phases.includes('B')) {
  const A = await actors();
  const watcher = await login('buyer');
  const admin = await login('admin');

  // --- B1: happy path, every approval clicked in the UI by the right role
  const pid = await createProject(watcher, 'E2E happy', 'happy');
  check('B1 project created through UI and pinned to config v1 (an inspector reads the pin; the buyer\'s own view omits it)', (await projectState(admin, pid)).config_version === 1 && (await projectState(watcher, pid)).config_version === undefined);
  await watcher.waitForSelector('.ws-head', { timeout: 20000 });
  const used = [];
  const { final, resolved } = await driveProject(A, watcher, pid, (k, role) => { used.push(`${role}:${k}`); return { act: 'approve' }; });
  console.log('happy resolved:', resolved.map((r) => `${r.kind}`).join(','));
  check('B1 happy reaches DONE/DEAL', final.status === 'DONE' && final.outcome === 'DEAL', `${final.status}/${final.outcome}/${final.reason}`);
  const kinds = new Set(resolved.map((r) => r.kind));
  for (const k of ['REQUIREMENT', 'COMPLIANCE_FLAG', 'TECHNICAL_QUAL', 'AWARD', 'SIGNATURE', 'BANK_DETAILS', 'HOLD_RELEASE']) check(`B1 approval ${k} resolved by clicking`, kinds.has(k));
  check('B1 every resolved approval succeeded', resolved.every((r) => r.ok), JSON.stringify(resolved.filter((r) => !r.ok)));
  await sleep(2500);
  await watcher.reload({ waitUntil: 'networkidle2' }); await sleep(1500);
  const biz = await watcher.evaluate(() => ({ rows: document.querySelectorAll('section[aria-label="Candidate suppliers"] tbody tr').length, ladder: !!document.querySelector('svg.ladder circle, svg.ladder path'), decisions: document.querySelectorAll('section[aria-label="Decisions"] li').length, tabs: document.querySelectorAll('[role=tab]').length, feed: document.querySelectorAll('.feed').length, text: document.body.innerText }));
  check('B1 buyer view: candidate suppliers table with names', biz.rows >= 3, biz.rows);
  check('B1 buyer view: price chart', biz.ladder);
  check('B1 buyer view: decisions listed', biz.decisions >= 5, biz.decisions);
  check('B1 buyer view has NO agent workflow (no tabs, no feed, no referee / agent / audit wording)', biz.tabs === 0 && biz.feed === 0 && !/Referee|orchestrator|Agents & workflow|Agent activity|Hash chain|AI agent|subagent|mandate|payload_hash/i.test(biz.text), biz.text.match(/Referee|orchestrator|Agents & workflow|Agent activity|Hash chain|AI agent|subagent|mandate/i)?.[0]);
  await shot(watcher, 'B1-happy-buyer-view');
  await openWorkflow(admin, pid);
  const info = await wsInfo(admin);
  check('B1 inspector (SBN super admin) workflow: conversation rows', info.rows > 5, info.rows);
  check('B1 inspector workflow: subagent chips w/ confidence', info.chips > 0, info.chips);
  check('B1 inspector workflow: ladder', info.ladder);
  check('B1 inspector workflow: audit timeline', info.audit > 10, info.audit);
  await shot(admin, 'B1-happy-workflow');
  await admin.waitForSelector('.ws-side .callout', { timeout: 15000 });
  await sleep(800);
  const badge = await admin.$eval('.ws-side .callout', (e) => e.textContent);
  check('B1 admin sees Hash chain verified', /Hash chain verified/.test(badge), badge);
  await shot(admin, 'B1-admin-verified');

  // --- B2: Edit + Reject paths on a second project
  const pid2 = await createProject(watcher, 'E2E edit and reject', 'happy');
  const seen = [];
  let rejected = false;
  const dec = (k) => {
    seen.push(k);
    if (k === 'COMPLIANCE_FLAG') return { act: 'edit', pick: 0, comment: 'Clearing the first flagged supplier after review.' };
    if (k === 'TECHNICAL_QUAL') return { act: 'edit', pick: 0, comment: 'Failing first candidate on technical grounds.' };
    if (k === 'AWARD' && !rejected) { rejected = true; return { act: 'reject', comment: 'Rejecting this award for the e2e reject path.' }; }
    return { act: 'approve' };
  };
  const r2 = await driveProject(A, watcher, pid2, dec, 150000);
  console.log('second resolved:', JSON.stringify(r2.resolved.map((r) => [r.kind, r.act, r.ok, r.err, r.options])));
  check('B2 edit approvals accepted', r2.resolved.filter((r) => r.act === 'edit').every((r) => r.ok && !(r.err?.length)), JSON.stringify(r2.resolved.filter((r) => r.act === 'edit')));
  check('B2 a reject with comment was submitted', r2.resolved.some((r) => r.act === 'reject' && r.ok), JSON.stringify(r2.resolved.filter((r) => r.act === 'reject')));
  console.log('second final:', r2.final.status, r2.final.outcome, r2.final.reason);
  await shot(watcher, 'B2-second-project');
  fs.writeFileSync(`${OUT}/B2-resolved.json`, JSON.stringify({ final: r2.final, resolved: r2.resolved }, null, 1));

  // --- B3: other scenarios
  let thr = [];
  const scen = {
    no_deal: (p) => p.status === 'STOPPED' && p.outcome === 'NO_DEAL',
    below_floor: (p) => p.status === 'DONE' || p.status === 'ESCALATED',
    locked_clause: (p) => p.status === 'DONE',
    poisoned_news: (p) => p.status === 'STOPPED' && p.outcome === 'NO_VIABLE_SUPPLIER',
    inflated_claims: (p) => p.status === 'DONE',
  };
  const supp = await login('supplier1');
  for (const [sc, ok] of Object.entries(scen)) {
    const id = await createProject(watcher, `E2E ${sc}`, sc);
    const r = await driveProject(A, watcher, id, () => ({ act: 'approve' }), 150000);
    console.log(sc, 'resolved:', r.resolved.map((x) => x.kind).join(','), '->', r.final.status, r.final.outcome, r.final.reason);
    check(`B3 ${sc} expected outcome`, ok(r.final), `${r.final.status}/${r.final.outcome}/${r.final.reason}`);
    await openWorkflow(admin, id);
    const w = await wsInfo(admin);
    await shot(admin, `B3-${sc}`);
    if (sc === 'below_floor' || sc === 'inflated_claims' || sc === 'poisoned_news') check(`B3 ${sc} policy-block or escalation callout rendered`, w.blocks + w.esc > 0, `blocks=${w.blocks} esc=${w.esc}`);
    if (sc === 'locked_clause') check('B3 locked_clause escalation callout rendered', /clause|locked|legal/i.test(w.text) && w.esc > 0, `esc=${w.esc}`);
    // supplier view: server filtered; no thresholds
    await supp.goto(`${BASE}/projects/${id}`, { waitUntil: 'networkidle2' }).catch(() => null);
    await sleep(1500);
    const sInfo = await wsInfo(supp);
    await shot(supp, `B3-${sc}-supplier`);
    const hay = sInfo.text + '\n' + supp.bodies.map((b) => b.body).join('\n');
    thr = thresholdsFromDb();
    watcher.bodies.length = 0;
    await watcher.goto(`${BASE}/projects/${id}`, { waitUntil: 'networkidle2' }); await sleep(1500);
    const buyerHay = watcher.bodies.map((x) => x.body).join('\n') + (await wsInfo(watcher)).text;
    const leaks = [...scanLeaks(thr, 'buyer', 'supplierView', hay), ...scanLeaks(thr, 'supplier', 'buyerView', buyerHay)];
    check(`B3 ${sc} supplier DOM+network free of buyer threshold values`, leaks.length === 0, leaks.slice(0, 4).join(', '));
    check(`B3 ${sc} supplier body has no mandate keys`, !/"(ceiling|walk_away|reservation|max_price|budget_cap)"/.test(hay));
  }
  // POSITIVE CONTROL: prove the scanner can fire. (1) a known sentinel planted in a fixture payload + haystack; (2) a real mandate value planted into a real haystack.
  const sentinel = '918273.6455';
  check('B3 control: scanner fires on a planted sentinel (fixture)', scanLeaks([['buyer', { ceiling: sentinel }]], 'buyer', 'fixture', `x ${sentinel} y`).length === 1);
  check('B3 control: scanner is silent on a clean fixture', scanLeaks([['buyer', { ceiling: sentinel }]], 'buyer', 'fixture', 'x 918273.6456 y 1918273.6455').length === 0);
  const realVal = thr.filter(([s]) => s === 'buyer').flatMap(([, p]) => numericLeaves(p)).find(([pa, v]) => (v.includes('.') || v.length >= 4) && !/max_capacity_units|lead_time_weeks|rounds|weeks|pct/.test(pa));
  check('B3 control: scanner fires when a REAL buyer limit is planted in the supplier haystack', !!realVal && scanLeaks(thr, 'buyer', 'planted', `<td>${realVal?.[1]}</td>`).length >= 1, realVal ? `${realVal[0]}=${realVal[1]}` : 'no scannable buyer value');
  fs.writeFileSync(`${OUT}/mandate-paths.json`, JSON.stringify(thr.map(([s, p]) => [s, numericLeaves(p).map(([a]) => a)]), null, 1));
  for (const p of [...Object.values(A), watcher, admin, supp]) noErrors(p);
  if (phases.length === 1) await finish();
}

const setNum = async (page, path, val) => {
  const sel = `[id="f-${path}"]`;
  await page.waitForSelector(sel);
  await page.click(sel, { clickCount: 3 });
  await page.keyboard.press('Backspace');
  if (val !== '') await page.type(sel, String(val));
};
const clickText = (page, sel, re) => page.evaluate((sel, re) => { const b = [...document.querySelectorAll(sel)].find((x) => new RegExp(re, 'i').test(x.textContent) && !x.disabled); if (!b) return false; b.click(); return true; }, sel, re);
const bar = (page) => page.$eval('.studio-bar', (e) => e.textContent);

if (phases.includes('C')) {
  const admin = await login('admin');
  const buyer = await login('buyer');
  const SECRET = 'sk-e2e-SECRETVALUE-9f3a71c2';
  const list = (await api(buyer, 'GET', '/projects')).json.items;
  const oldP = list.find((p) => p.title === 'E2E happy');
  const oldBefore = JSON.stringify((await api(admin, 'GET', `/projects/${oldP.id}`)).json);
  const oldShort = (await api(admin, 'GET', `/projects/${oldP.id}/messages`)).json.items.filter((m) => m.type === 'RFQ').length;

  // C1: edit + inline validation
  await admin.goto(`${BASE}/studio/workflow`, { waitUntil: 'networkidle2' });
  await admin.waitForSelector('[id="f-workflow.shortlist_size"]');
  await setNum(admin, 'workflow.shortlist_size', 0);
  await sleep(1800);
  const errTxt = await admin.$$eval('.studio-main .fld-err, .studio-side .callout.bad', (e) => e.map((x) => x.textContent).join(' | '));
  check('C1 out-of-range value shows inline validation', errTxt.length > 0 && /error/i.test(await bar(admin)), errTxt.slice(0, 120));
  await shot(admin, 'C1-inline-validation');
  await setNum(admin, 'workflow.shortlist_size', 3);
  await sleep(1800);
  check('C1 valid edit autosaves', /Draft saved/.test(await bar(admin)) && /Valid/.test(await bar(admin)), (await bar(admin)).slice(0, 80));

  // C2: invariant cannot be disabled
  await admin.goto(`${BASE}/studio/agents`, { waitUntil: 'networkidle2' });
  await admin.waitForSelector('details[data-path="agents.compliance_screen"]');
  await admin.evaluate(() => { document.querySelector('details[data-path="agents.compliance_screen"]').open = true; });
  await admin.click('details[data-path="agents.compliance_screen"] button[role="switch"]');
  await sleep(1800);
  const side = await admin.$eval('.studio-side', (e) => e.textContent);
  check('C2 disabling compliance agent flagged with invariant id', /INV_[A-Z_]+/.test(side), (side.match(/INV_[A-Z_]+/) ?? [''])[0]);
  await admin.click('.studio-bar .btn.primary'); await admin.waitForSelector('.dialog'); await sleep(900);
  check('C2 publish blocked while invariant violated', await admin.$eval('.dialog-foot .btn.primary', (b) => b.disabled));
  await shot(admin, 'C2-invariant-blocked');
  await admin.keyboard.press('Escape'); await sleep(200);
  await admin.click('details[data-path="agents.compliance_screen"] button[role="switch"]'); await sleep(1800);
  check('C2 reverting clears the error', /Valid/.test(await bar(admin)), (await bar(admin)).slice(0, 80));
  // server-side: smuggle the violation straight at publish (UI bypassed) must also fail
  const d = (await api(admin, 'GET', '/studio/draft')).json;
  check('C2 draft still has compliance_screen enabled', d.config.agents.compliance_screen.enabled === true);

  // C3: publish with note
  await admin.click('.studio-bar .btn.primary'); await admin.waitForSelector('#pn');
  await admin.type('#pn', 'e2e: shortlist 3'); await sleep(900);
  await clickText(admin, '.dialog-foot button', 'Publish new version'); await sleep(1500);
  const vers = (await api(admin, 'GET', '/studio/versions')).json;
  check('C3 published v2 active with note', vers[0].version === 2 && vers[0].active && vers[0].note === 'e2e: shortlist 3', JSON.stringify(vers.map((v) => [v.version, v.active, v.note])));
  await admin.goto(`${BASE}/studio/versions`, { waitUntil: 'networkidle2' });
  await admin.waitForSelector('.table tbody tr');
  check('C3 version list shows 2 rows', (await admin.$$('.table tbody tr')).length === 2);
  await admin.click('input[aria-label="Select v1 for compare"]'); await admin.click('input[aria-label="Select v2 for compare"]');
  await clickText(admin, 'button', 'Compare selected'); await admin.waitForSelector('.diff-row .path', { timeout: 8000 }).catch(() => null);
  const diffTxt = await admin.$$eval('.diff-row', (r) => r.map((x) => x.textContent).join(' | '));
  check('C3 diff shows shortlist_size 5 -> 3', /shortlist_size/.test(diffTxt) && /5/.test(diffTxt) && /3/.test(diffTxt), diffTxt.slice(0, 160));
  await shot(admin, 'C3-diff');
  await admin.keyboard.press('Escape'); await sleep(200);

  // C4: new project pins v2 and behaves differently; old unchanged
  const nid = await createProject(buyer, 'E2E pinned v2', 'happy');
  const np = await projectState(admin, nid);
  check('C4 new project pins v2', np.config_version === 2, np.config_version);
  const A = await actors();
  const r = await driveProject(A, buyer, nid, () => ({ act: 'approve' }), 150000);
  const cmp = async (id) => {
    const aud = (await api(admin, 'GET', `/projects/${id}/messages`)).json.items;
    return new Set(aud.filter((m) => m.type === 'RFQ').map((m) => m.to_agent)).size;
  };
  const newShort = await cmp(nid); const oldShort2 = await cmp(oldP.id);
  check('C4 new project shortlists 3 (v2), old project 5 (v1)', newShort === 3 && oldShort2 === 5, `new=${newShort} old=${oldShort2} (was ${oldShort}) final=${r.final.status}`);
  const oldAfter = JSON.stringify((await api(admin, 'GET', `/projects/${oldP.id}`)).json);
  check('C4 old project record unchanged', oldAfter === oldBefore);

  // C5: rollback
  await admin.goto(`${BASE}/studio/versions`, { waitUntil: 'networkidle2' });
  await admin.waitForSelector('.table tbody tr');
  await admin.evaluate(() => { const row = [...document.querySelectorAll('.table tbody tr')].find((r) => /v1/.test(r.querySelector('b').textContent)); [...row.querySelectorAll('button')].find((b) => /Roll back/.test(b.textContent)).click(); });
  await admin.waitForSelector('.dialog'); await clickText(admin, '.dialog-foot button', '^Roll back$'); await sleep(1500);
  const v3 = (await api(admin, 'GET', '/studio/versions')).json;
  check('C5 rollback creates v3 (rollback of v1), active', v3[0].version === 3 && v3[0].rollback_of === 1 && v3[0].active, JSON.stringify(v3[0]));
  await shot(admin, 'C5-rollback');
  const active = (await api(admin, 'GET', '/studio/active')).json;
  check('C5 active config shortlist_size back to 5', active.config.workflow.shortlist_size === 5, active.config.workflow.shortlist_size);

  // C6: provider key, write-only
  const provs = (await api(admin, 'GET', '/studio/providers')).json;
  const prov = provs.find((p) => p.is_local) ?? provs[0];
  await admin.goto(`${BASE}/studio/providers`, { waitUntil: 'networkidle2' });
  await admin.waitForSelector('input[type=password]');
  admin.bodies.length = 0;
  await admin.type(`#key-${prov.name}`, SECRET);
  await admin.click('form .btn.primary'); await sleep(1500);
  const inputVal = await admin.$eval(`#key-${prov.name}`, (i) => i.value);
  const html = await admin.content();
  check('C6 key input cleared, secret absent from DOM', inputVal === '' && !html.includes(SECRET));
  check('C6 has_key shown', /key set/.test(html));
  await admin.reload({ waitUntil: 'networkidle2' }); await sleep(800);
  const html2 = await admin.content();
  const allBodies = admin.bodies.map((b) => b.body).join('\n');
  check('C6 secret not in any response body after save (providers/draft/versions/audit)', !allBodies.includes(SECRET) && !html2.includes(SECRET));
  const exp = JSON.stringify((await api(admin, 'GET', '/studio/export')).json);
  const aud = JSON.stringify((await api(admin, 'GET', '/studio/audit?limit=200')).json);
  const diff = JSON.stringify((await api(admin, 'GET', '/studio/diff?a=1&b=3')).json);
  check('C6 secret not in export/audit/diff', ![exp, aud, diff].some((t) => t.includes(SECRET)));
  const grepCount = (f) => { try { return (execFileSync('grep', ['-c', SECRET, f], { encoding: 'utf8' }).trim().split('\n')[0]); } catch (e) { return String(e.stdout ?? '0').trim() || '0'; } };
  const dbHas = ['', '-wal'].map((x) => grepCount(`../data/e2e/e2e.db${x}`)).join('+');
  check('C6 secret not stored in plaintext in sqlite file', /^0(\+0)?$/.test(dbHas), `grep count ${dbHas}`);
  // test connection
  await clickText(admin, '.card button', 'Test connection'); await sleep(2500);
  const tc = await admin.$$eval('.callout', (c) => c.map((x) => x.textContent).join(' | '));
  const models = await admin.$$eval('.chip.mono', (c) => c.map((x) => x.textContent));
  check('C6 test connection against Ollama lists models', /Reachable/.test(tc) && models.some((m) => /gemma/.test(m)), `${models.length} models: ${models.slice(0, 5)}`);
  await shot(admin, 'C6-providers');
  await clickText(admin, '.card button', 'Remove key'); await admin.waitForSelector('.dialog'); await clickText(admin, '.dialog-foot button', '^Remove$'); await sleep(1000);

  // C7: export / import
  const bundle = (await api(admin, 'GET', '/studio/export')).json;
  await admin.goto(`${BASE}/studio/workflow`, { waitUntil: 'networkidle2' });
  await admin.waitForSelector('.studio-bar');
  await clickText(admin, '.studio-bar button', 'Export'); await sleep(800);
  await clickText(admin, '.studio-bar button', 'Import'); await admin.waitForSelector('textarea[aria-label="Bundle JSON"]');
  bundle.config.workflow.shortlist_size = 4;
  await admin.evaluate((t) => { const ta = document.querySelector('textarea[aria-label="Bundle JSON"]'); const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set; set.call(ta, t); ta.dispatchEvent(new Event('input', { bubbles: true })); }, JSON.stringify(bundle));
  await sleep(300);
  await clickText(admin, '.dialog-foot button', 'Import into draft'); await sleep(1500);
  const dd = (await api(admin, 'GET', '/studio/draft')).json;
  check('C7 import replaced the draft (shortlist 4)', dd.config.workflow.shortlist_size === 4, dd.config.workflow.shortlist_size);
  await api(admin, 'DELETE', '/studio/draft');
  for (const p of [admin, buyer, ...Object.values(A)]) noErrors(p);
  if (phases[phases.length - 1] === 'C') await finish();
}

if (phases.includes('D')) {
  const admin = await login('admin');
  const buyer = await login('buyer');
  const A = await actors();
  const setMode = async (m) => {
    await admin.goto(`${BASE}/settings`, { waitUntil: 'networkidle2' });
    await admin.waitForSelector('#s-mode');
    await admin.select('#s-mode', m);
    await clickText(admin, 'form button', '^Save$'); await sleep(1500);
    const toasts = await admin.$$eval('.toast', (t) => t.map((x) => x.textContent).join('|'));
    return toasts;
  };
  console.log('set live ->', await setMode('live'));
  const h = await (await fetch('http://127.0.0.1:8005/api/v1/health')).json();
  check('D settings UI switched to live', h.llm_mode === 'live', JSON.stringify(h));
  await shot(admin, 'D-settings-live');
  for (const sc of ['happy', 'poisoned_news']) {
    const id = await createProject(buyer, `E2E live ${sc}`, sc);
    const t0 = Date.now();
    const r = await driveProject(A, buyer, id, () => ({ act: 'approve' }), 420000);
    const finAdmin = await projectState(admin, id); // run internals (mode) are for inspectors; the buyer's own view omits them
    console.log(sc, 'live:', r.final.status, r.final.outcome, r.final.reason, finAdmin.llm_mode, `${Math.round((Date.now() - t0) / 1000)}s`);
    check(`D ${sc} live run completes`, r.final.status === (sc === 'happy' ? 'DONE' : 'STOPPED'), `${r.final.status}/${r.final.outcome}/${r.final.reason}`);
    check(`D ${sc} project llm_mode is live`, finAdmin.llm_mode === 'live', finAdmin.llm_mode);
    const sc_rows = dbExec("select tool, model_id, tokens, latency_ms, confidence from subagent_call where project_id=?", [id.replace(/-/g, '')]);
    const news = sc_rows.filter((r) => r[0] === 'regional_news_risk');
    check(`D ${sc} live news subagent_call has model_id + tokens > 0 + latency > 0 (provenance)`, news.length > 0 && news.some((r) => r[1] && r[2] > 0 && r[3] > 0), JSON.stringify(news.slice(0, 2)));
    check(`D ${sc} deterministic tools carry no model_id`, sc_rows.filter((r) => r[0] !== 'regional_news_risk').every((r) => !r[1]), JSON.stringify(sc_rows.filter((r) => r[0] !== 'regional_news_risk' && r[1]).slice(0, 2)));
    const llmOut = dbExec("select output from subagent_call where project_id=? and tool='regional_news_risk'", [id.replace(/-/g, '')]).map((r) => JSON.parse(r[0]));
    check(`D ${sc} subagent_call.output.llm lists provider/model/tokens (provenance is outside the hash path by design)`, llmOut.some((o) => o.llm?.[0]?.provider && o.llm[0].model && o.llm[0].tokens > 0), JSON.stringify(llmOut[0]?.llm));
    await openWorkflow(admin, id);
    const lat = await admin.$$eval('.chip', (cs) => cs.filter((c) => /regional_news_risk/.test(c.textContent)).map((c) => (c.textContent.match(/(\d+)ms/) ?? [])[1]));
    check(`D ${sc} live news subagent latency > 0`, lat.length > 0 && lat.some((x) => Number(x) > 0), JSON.stringify(lat));
    await shot(admin, `D-${sc}-live`);
    await admin.waitForSelector('.ws-side .callout', { timeout: 20000 }); await sleep(800);
    const badge = await admin.$eval('.ws-side .callout', (e) => e.textContent);
    check(`D ${sc} audit valid`, /Hash chain verified/.test(badge), badge);
    await shot(admin, `D-${sc}-audit`);
  }
  console.log('set replay ->', await setMode('replay'));
  const h2 = await (await fetch('http://127.0.0.1:8005/api/v1/health')).json();
  check('D settings back to replay', h2.llm_mode === 'replay');
  for (const p of [admin, buyer, ...Object.values(A)]) noErrors(p);
  if (phases[phases.length - 1] === 'D') await finish();
}

if (phases.includes('E')) {
  const API = 'http://127.0.0.1:8005/api/v1';
  const creds = JSON.parse(fs.readFileSync(process.env.E2E_CREDS ?? '../data/e2e/creds.json', 'utf8'));
  const rawLogin = async (who, pw) => {
    const r = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: creds[who][0], password: pw ?? creds[who][1] }) });
    const cookies = r.headers.getSetCookie().map((c) => c.split(';')[0]);
    return { status: r.status, body: await r.text(), cookie: cookies.join('; '), csrf: (cookies.find((c) => c.startsWith('csrf=')) ?? '').slice(5) };
  };
  const buyer = await login('buyer');
  const list = (await api(buyer, 'GET', '/projects')).json.items;
  const proj = list.find((p) => p.title === 'E2E happy') ?? list[0];

  // E1 cross-tenant
  const b2 = await login('buyer2');
  const r1 = await api(b2, 'GET', `/projects/${proj.id}`);
  const r1b = await api(b2, 'GET', `/projects/${proj.id}/overview`);
  const r1c = await api(b2, 'GET', `/projects/${proj.id}/audit/verify`);
  const ghostV = await api(b2, 'GET', '/projects/00000000-0000-4000-8000-000000000000/audit/verify');
  check('E1 buyer2 cannot see buyer project: 404 (detail, overview); verify is role-gated identically to a nonexistent id (no existence leak)', r1.status === 404 && r1b.status === 404 && r1c.status === ghostV.status, `${r1.status},${r1b.status},${r1c.status}/${ghostV.status}`);
  check('E1 buyer2 project list excludes it', !(await api(b2, 'GET', '/projects')).json.items.some((p) => p.id === proj.id));
  const r1d = await api(b2, 'POST', `/projects/${proj.id}/stop`);
  check('E1 buyer2 cannot stop it: 404', r1d.status === 404, r1d.status);
  // E2 supplier scope
  const s1 = await login('supplier1'); const s2 = await login('supplier2');
  for (const [nm, sp] of [['supplier1', s1], ['supplier2', s2]]) {
    const pl = (await api(sp, 'GET', '/projects')).json.items;
    for (const p of pl.slice(0, 4)) {
      const m = (await api(sp, 'GET', `/projects/${p.id}/messages`)).json.items;
      const parties = new Set(m.flatMap((x) => [x.from_agent, x.to_agent]).filter((a) => a?.startsWith('supplier:')));
      // N-phase fixture hands S01253 to supplier2's org, so supplier2 legitimately owns two supplier agents; nobody else's may appear
      const own = nm === 'supplier1' ? ['supplier:S00703'] : ['supplier:S01454', 'supplier:S01253'];
      check(`E2 ${nm} messages in ${p.title} involve only its own supplier agent(s)`, [...parties].every((x) => own.includes(x)), [...parties].join(','));
      const aud = await api(sp, 'GET', `/projects/${p.id}/audit/verify`);
      check(`E2 ${nm} no audit verify`, aud.status === 403 || aud.status === 404, aud.status);
    }
    check(`E2 ${nm} lists only invited projects`, pl.length < list.length + 100, `${pl.length}`);
  }
  const draftP = (await api(buyer, 'POST', '/projects', { title: 'E2E uninvited draft', need_text: '', scenario: 'happy', need_index: 0, seed: 1 })).json;
  const sp = await api(s1, 'GET', `/projects/${draftP.id}`);
  check('E2 supplier on a project it is not invited to: 404', sp.status === 404, sp.status);
  // E3 evaluator read-only
  const ev = await login('evaluator');
  const e1 = await api(ev, 'POST', '/projects', { title: 'x', need_text: '', scenario: 'happy', need_index: 0, seed: 1 });
  const e2 = await api(ev, 'POST', `/projects/${proj.id}/start`);
  const e3 = await api(ev, 'PUT', '/studio/draft', { config: {} });
  const e4 = await api(ev, 'GET', `/projects/${proj.id}`);
  const aps = (await api(ev, 'GET', '/approvals')).json.items;
  const e5 = await api(ev, 'POST', `/approvals/${aps[0].id}/resolve`, { status: 'APPROVED', comment: '', version: aps[0].version });
  check('E3 evaluator can read', e4.status === 200);
  check('E3 evaluator writes are 403', [e1.status, e2.status, e3.status, e5.status].every((s) => s === 403), [e1.status, e2.status, e3.status, e5.status].join(','));
  await ev.goto(`${BASE}/approvals`, { waitUntil: 'networkidle2' });
  await clickText(ev, '.tabs button', 'All'); await sleep(500);
  await ev.click('.inbox .appr'); await ev.waitForSelector('.drawer');
  const foot = await ev.$eval('.drawer-foot', (e) => e.textContent);
  check('E3 evaluator UI shows read-only drawer, no Approve button', /Read-only|Resolved/.test(foot) && !(await ev.$('.drawer-foot .btn.primary')), foot.slice(0, 80));
  await shot(ev, 'E3-evaluator-readonly');
  // E4 CSRF
  const L = await rawLogin('buyer');
  const noCsrf = await fetch(`${API}/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: L.cookie }, body: JSON.stringify({ title: 't', need_text: '', scenario: 'happy', need_index: 0, seed: 1 }) });
  const badCsrf = await fetch(`${API}/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: L.cookie, 'X-CSRF-Token': 'nope' }, body: JSON.stringify({ title: 't', need_text: '', scenario: 'happy', need_index: 0, seed: 1 }) });
  const okCsrf = await fetch(`${API}/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: L.cookie, 'X-CSRF-Token': L.csrf }, body: JSON.stringify({ title: 'csrf ok', need_text: '', scenario: 'happy', need_index: 0, seed: 1 }) });
  check('E4 missing CSRF header -> 403', noCsrf.status === 403, noCsrf.status + ' ' + (await noCsrf.text()).slice(0, 80));
  check('E4 wrong CSRF header -> 403', badCsrf.status === 403, badCsrf.status);
  check('E4 correct CSRF -> 201', okCsrf.status === 201, okCsrf.status);
  // E5 oversize
  const big = await fetch(`${API}/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: L.cookie, 'X-CSRF-Token': L.csrf }, body: JSON.stringify({ title: 't', need_text: 'x'.repeat(3_000_000), scenario: 'happy' }) });
  check('E5 oversize body -> 413 with envelope', big.status === 413 && /"error"/.test(await big.text()), big.status);
  // E6 uniform bad logins (throwaway: designer), then locked
  const bodies = [];
  for (let i = 0; i < 5; i++) { const r = await rawLogin('designer', 'wrong-password-' + i); bodies.push([r.status, r.body.replace(/"request_id":"[^"]*"/, '')]); }
  const ghost = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'nobody@sourceloop.test', password: 'wrong-password-x' }) });
  const gb = (await ghost.text()).replace(/"request_id":"[^"]*"/, '');
  check('E6 five bad logins: identical 401s', bodies.every((b) => b[0] === 401 && b[1] === bodies[0][1]), JSON.stringify(bodies[0]));
  check('E6 unknown email gets the same 401 body', ghost.status === 401 && gb === bodies[0][1], gb.slice(0, 100));
  const locked = await rawLogin('designer');
  check('E6 after lockout the correct password is still the same uniform 401', locked.status === 401 && locked.body.replace(/"request_id":"[^"]*"/, '') === bodies[0][1], locked.status);
  // E7 logout: stream closes, API 401
  const L0 = await rawLogin('buyer');
  const L2 = await rawLogin('orgadmin'); // the live stream is an inspector feature: the organization admin
  const streamPid = (await (await fetch(`${API}/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: L0.cookie, 'X-CSRF-Token': L0.csrf }, body: JSON.stringify({ title: 'stream test', need_text: '', scenario: 'happy', need_index: 0, seed: 1 }) })).json()).id;
  const ac = new AbortController();
  const sr = await fetch(`${API}/projects/${streamPid}/stream`, { headers: { Cookie: L2.cookie }, signal: ac.signal });
  check('E7 stream opens', sr.status === 200, sr.status);
  const reader = sr.body.getReader();
  let closedAt = null; const t0 = Date.now();
  const pump = (async () => { try { for (;;) { const { done } = await reader.read(); if (done) { closedAt = Date.now() - t0; return; } } } catch { closedAt = Date.now() - t0; } })();
  await sleep(1000);
  const lo = await fetch(`${API}/auth/logout`, { method: 'POST', headers: { Cookie: L2.cookie, 'X-CSRF-Token': L2.csrf } });
  check('E7 logout 204', lo.status === 204, lo.status);
  await Promise.race([pump, sleep(40000)]);
  check('E7 server closes the SSE stream after logout', closedAt !== null, closedAt === null ? 'still open after 40s' : `${closedAt}ms`);
  ac.abort();
  const after = await fetch(`${API}/auth/me`, { headers: { Cookie: L2.cookie } });
  const after2 = await fetch(`${API}/projects`, { headers: { Cookie: L2.cookie } });
  const after3 = await fetch(`${API}/projects/${streamPid}/stream`, { headers: { Cookie: L2.cookie } });
  check('E7 old session cookies now 401 (me, projects, stream)', [after.status, after2.status, after3.status].every((s) => s === 401), [after.status, after2.status, after3.status].join(','));
  // E7b UI logout from a workspace
  await buyer.goto(`${BASE}/projects/${streamPid}`, { waitUntil: 'domcontentloaded' });
  await sleep(1200);
  const conn = await buyer.$eval('.conn', (e) => e.textContent).catch(() => '');
  await buyer.click('button[aria-label="Sign out"]'); await sleep(1500);
  check('E7b UI logout lands on /login', buyer.url().includes('/login'), `${buyer.url()} (conn before: ${conn})`);
  const un = await api(buyer, 'GET', '/projects');
  check('E7b API 401 after UI logout', un.status === 401, un.status);
  for (const p of [buyer, b2, s1, s2, ev]) noErrors(p);
  if (phases[phases.length - 1] === 'E') await finish();
}

if (phases.includes('F')) {
  const { spawn, execSync } = await import('node:child_process');
  const restartApi = async () => {
    try { execSync('pkill -f "uvicorn sourceloop.main:app"'); } catch { /* none */ }
    await sleep(1500);
    const child = spawn('sh', ['-c', 'cd .. && exec scripts/start.sh >> data/e2e/api.log 2>&1'], { detached: true, stdio: 'ignore', env: { ...process.env, DATABASE_URL: 'sqlite+aiosqlite:///./data/e2e/e2e.db', RATE_LIMIT_CREATE: '1000/hour', RATE_LIMIT_START: '1000/hour', RATE_LIMIT_RESOLVE: '1000/minute', RATE_LIMIT_LOGIN: '1000/minute', MAX_ACTIVE_PROJECTS_PER_ORG: '200' } });
    child.unref();
    for (let i = 0; i < 30; i++) { try { if ((await fetch('http://127.0.0.1:8005/api/v1/health')).ok) return; } catch { /* wait */ } await sleep(1000); }
    throw new Error('API did not come back');
  };
  let buyer = await login('buyer');
  const pid = await createProject(buyer, 'E2E restart mid-run', 'happy');
  await waitStatus(buyer, pid, (p) => p.status === 'ESCALATED', 60000);
  const mid = await projectState(buyer, pid);
  check('F project is mid-run (ESCALATED, approval pending)', mid.status === 'ESCALATED', `${mid.status}/${mid.stage}`);
  await sleep(1500);
  await shot(buyer, 'F-before-restart');
  await restartApi();
  check('F API healthy after restart', (await (await fetch('http://127.0.0.1:8005/api/v1/health')).json()).status === 'ok');
  // dev JWT secret is random per process => session reset; UI must bounce to login cleanly
  await sleep(3500);
  const bounced = buyer.url().includes('/login') || (await api(buyer, 'GET', '/auth/me')).status === 401;
  console.log('session after restart: ', buyer.url(), bounced);
  await buyer.close(); await buyer.ctx.close();
  buyer = await login('buyer');
  const after = await projectState(buyer, pid);
  console.log('after restart:', after.status, after.stage, after.reason, after.outcome);
  check('F restored after the restart: same checkpoint, not lost', after.status === 'ESCALATED' && after.stage === mid.stage && !/RESTART/.test(after.reason ?? ''), `${after.status}/${after.stage}/${after.reason}`);
  await buyer.goto(`${BASE}/projects/${pid}`, { waitUntil: 'domcontentloaded' });
  await buyer.waitForSelector('.ws-head', { timeout: 15000 }); await sleep(2000);
  const txt = await buyer.$eval('.ws-head', (e) => e.textContent);
  check('F workspace shows NO lost-on-restart banner for the restored project', !/lost on restart|interrupted by a restart/i.test(txt), txt.slice(0, 160));
  await shot(buyer, 'F-restored');
  await buyer.goto(`${BASE}/approvals`, { waitUntil: 'networkidle2' });
  const pend = (await api(buyer, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.project_id === pid);
  check('F the pending approval survived the restart and is still decidable', pend.length >= 1, `${pend.length} pending`);
  await shot(buyer, 'F-inbox');
  // finish the restored project through the UI: approvals clicked by the right roles
  const A0 = await actors();
  const r0 = await driveProject(A0, buyer, pid, () => ({ act: 'approve' }), 150000);
  check('F restored project finishes DONE/DEAL through the UI', r0.final.status === 'DONE' && r0.final.outcome === 'DEAL', `${r0.final.status}/${r0.final.outcome}`);
  // new project works
  const A = A0;
  const nid = await createProject(buyer, 'E2E after restart', 'happy');
  const r = await driveProject(A, buyer, nid, () => ({ act: 'approve' }), 150000);
  check('F new project after restart completes DONE/DEAL', r.final.status === 'DONE' && r.final.outcome === 'DEAL', `${r.final.status}/${r.final.outcome}`);
  const adminP = await login('admin');
  await openWorkflow(adminP, pid);
  await adminP.waitForSelector('.ws-side .callout', { timeout: 20000 }); await sleep(800);
  check('F restored project audit chain verifies', /Hash chain verified/.test(await adminP.$eval('.ws-side .callout', (e) => e.textContent)));
  for (const p of [buyer, adminP, ...Object.values(A)]) noErrors(p);
  if (phases[phases.length - 1] === 'F') await finish();
}

if (phases.includes('END')) await finish();
