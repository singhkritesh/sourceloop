// STALE: written for the old role set and six-checkpoint flow; not rerun since the buyer-only change. Current journey: e2e-simple.mjs
// Real-stack e2e for the second-round features. Usage (from web/): E2E_CREDS=../data/e2e/creds.json RESULT_PREFIX=e2e-new node scripts/e2e-new.mjs N [H]
//   N : supplier views (awardee vs loser), overdue badge, stop cancels pending approvals, structured need, AI badge, override-rates table
//   H : HOLD_RELEASE after DONE is resolvable (also after an API restart) - see phase F in e2e.mjs for the restart variant
import fs from 'node:fs';
import { api, BASE, check, createProject, dbExec, driveProject, finish, login, numericLeaves, openWorkflow, projectState, scanLeaks, shot, sleep, thresholdsFromDb, waitStatus, approverPass } from './e2e-lib.mjs';

const phases = process.argv.slice(2).length ? process.argv.slice(2) : ['N'];
const ROLES = ['buyer', 'compliance', 'quality', 'legal', 'finance'];
const API = 'http://127.0.0.1:8005/api/v1';
const creds = JSON.parse(fs.readFileSync(process.env.E2E_CREDS ?? '../data/e2e/creds.json', 'utf8'));
const noErrors = (page) => check(`${page.who}: no uncaught page errors`, page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
async function actors() { const a = {}; for (const r of ROLES) a[r] = await login(r); return a; }

if (phases.includes('N')) {
  const A = await actors();
  const buyer = await login('buyer');
  const admin = await login('admin');
  const loser = await login('supplier1'); // owns S00703 only
  const awardee = await login('supplier2'); // S01454 (+ S01253 after the fixture below)

  // FIXTURE: in the seed no supplier user's org owns the supplier that wins (S01253); hand S01253 to supplier2's org so the awardee
  // view can be exercised through a real login. Test data only; touches the e2e DB, never product code.
  const [[org]] = dbExec("select org_id from supplier where id='S01454'");
  dbExec("update supplier set org_id=? where id='S01253'", [org]);

  // ---- N1: supplier views
  const TITLE = 'E2E N1 BUYERTITLE-7731';
  const pid = await createProject(buyer, TITLE, 'happy');
  const { final, resolved } = await driveProject(A, buyer, pid, () => ({ act: 'approve' }), 180000);
  check('N1 happy reaches DONE/DEAL', final.status === 'DONE' && final.outcome === 'DEAL', `${final.status}/${final.outcome}`);
  check('N1 HOLD_RELEASE resolved by FINANCE in the UI after DONE', resolved.some((r) => r.kind === 'HOLD_RELEASE' && r.ok && !(r.err?.length)), JSON.stringify(resolved.filter((r) => r.kind === 'HOLD_RELEASE').map((r) => [r.ok, r.err, r.toast])));
  await sleep(1500);
  const view = async (page) => {
    page.bodies.length = 0;
    await page.goto(`${BASE}/projects/${pid}`, { waitUntil: 'networkidle2' });
    await page.waitForSelector('.ws-head', { timeout: 15000 }); await sleep(2500);
    const head = await page.$eval('.ws-head', (e) => e.textContent);
    const text = await page.evaluate(() => document.body.innerText);
    const proj = (await api(page, 'GET', `/projects/${pid}`)).json;
    const msgs = (await api(page, 'GET', `/projects/${pid}/messages`)).json.items;
    const aud = (await api(page, 'GET', `/projects/${pid}/audit`)).json.items;
    return { head, text, proj, msgs, aud, net: page.bodies.map((b) => b.body).join('\n') };
  };
  const L = await view(loser); const W = await view(awardee);
  await shot(loser, 'N1-loser-view'); await shot(awardee, 'N1-awardee-view');
  const stagesOrder = ['NEED', 'DISCOVERY', 'QUALIFICATION', 'SOURCING', 'CONTRACTING', 'ONBOARDING'];
  const dense = (arr) => arr.every((x, i) => x.seq === i + 1);
  check('N1 loser: neutral title, not the buyer title', /^Sourcing request /.test(L.proj.title) && !L.text.includes(TITLE) && !L.net.includes(TITLE), L.proj.title);
  check('N1 awardee: neutral title too', /^Sourcing request /.test(W.proj.title) && !W.text.includes(TITLE) && !W.net.includes(TITLE), W.proj.title);
  check('N1 loser: outcome CLOSED (never DEAL), status DONE', L.proj.outcome === 'CLOSED' && L.proj.status === 'DONE', `${L.proj.status}/${L.proj.outcome}`);
  check('N1 awardee: outcome DEAL', W.proj.outcome === 'DEAL', `${W.proj.status}/${W.proj.outcome}`);
  check('N1 loser DOM+network never mention a DEAL outcome / contract / award', !/"outcome":\s*"DEAL"|\bDEAL\b|NO_DEAL|NO_VIABLE|STOPPED_BY_USER/.test(L.head + L.net) && !/"(contract|award)/i.test(L.net), (L.head.match(/DEAL|STOPPED|NO[ _]\w+/) ?? [''])[0]);
  check('N1 loser stage is clamped to its own last interaction (before CONTRACTING)', stagesOrder.indexOf(L.proj.stage) <= stagesOrder.indexOf('SOURCING'), L.proj.stage);
  const lastStageAudit = L.aud.filter((e) => e.action === 'stage_changed').map((e) => e.outcome).pop();
  check('N1 loser audit/stage_changed events stop at its last own stage; never contracting/onboarding', !L.aud.some((e) => e.action === 'stage_changed' && /CONTRACTING|ONBOARDING/.test(e.outcome ?? '')), lastStageAudit);
  check('N1 loser message seq is dense 1..n', dense(L.msgs) && L.msgs.length > 0, L.msgs.map((m) => m.seq).join(','));
  check('N1 awardee message seq is dense 1..n', dense(W.msgs) && W.msgs.length > L.msgs.length, W.msgs.map((m) => m.seq).join(','));
  check('N1 loser audit view dense and ends with neutral closed/CLOSED', dense(L.aud) && L.aud.at(-1).action === 'closed' && L.aud.at(-1).outcome === 'CLOSED', JSON.stringify(L.aud.at(-1)));
  check('N1 awardee audit view ends closed/DEAL', dense(W.aud) && W.aud.at(-1).action === 'closed' && W.aud.at(-1).outcome === 'DEAL', JSON.stringify(W.aud.at(-1)));
  check('N1 loser sees no other supplier agent', new Set(L.msgs.flatMap((m) => [m.from_agent, m.to_agent]).filter((a) => a?.startsWith('supplier:'))).size === 1);
  // threshold scan with positive control
  const thr = thresholdsFromDb();
  const leaksL = scanLeaks(thr, 'buyer', 'loserView', L.text + L.net);
  const leaksW = scanLeaks(thr, 'buyer', 'awardeeView', W.text + W.net);
  check('N1 loser DOM+network free of buyer threshold values', leaksL.length === 0, leaksL.slice(0, 3).join(','));
  check('N1 awardee DOM+network free of buyer threshold values', leaksW.length === 0, leaksW.slice(0, 3).join(','));
  const bval = thr.filter(([s]) => s === 'buyer').flatMap(([, p]) => numericLeaves(p)).find(([pa, v]) => (v.includes('.') || v.length >= 4) && !/max_capacity_units|lead_time_weeks|rounds|weeks|pct/.test(pa));
  check('N1 control: planting a real buyer limit into the loser haystack IS detected', !!bval && scanLeaks(thr, 'buyer', 'planted', L.text + L.net + ` ${bval[1]} `).length >= 1, bval?.join('='));
  check('N1 control: planting a sentinel in a fixture mandate IS detected', scanLeaks([['buyer', { ceiling: '424242.4242' }]], 'buyer', 'fx', L.text + ' 424242.4242 ').length === 1);
  // SSE for the loser (fresh login, raw fetch): only neutral frames, closes with CLOSED
  const rawLogin = async (who) => {
    const r = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: creds[who][0], password: creds[who][1] }) });
    return { status: r.status, cookie: r.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ') };
  };
  const streamText = async (who) => {
    const l = await rawLogin(who); const ac = new AbortController(); const t = setTimeout(() => ac.abort(), 8000);
    let txt = ''; try { const r = await fetch(`${API}/projects/${pid}/stream`, { headers: { Cookie: l.cookie }, signal: ac.signal }); const rd = r.body.getReader(); const dec = new TextDecoder();
      for (;;) { const { done, value } = await rd.read(); if (done) break; txt += dec.decode(value); if (/event: run_finished/.test(txt)) break; } } catch { /* aborted */ }
    clearTimeout(t); ac.abort(); return txt;
  };
  const sl = await streamText('supplier1'); const sw = await streamText('supplier2');
  const lastFinished = (t) => (t.match(/event: run_finished\s*\ndata: (.*)/g) ?? []).pop() ?? '';
  check('N1 loser SSE ends with run_finished CLOSED, no DEAL, no buyer title', /CLOSED/.test(lastFinished(sl)) && !/DEAL/.test(sl) && !sl.includes(TITLE), lastFinished(sl).slice(0, 80));
  check('N1 awardee SSE ends with run_finished DEAL', /DEAL/.test(lastFinished(sw)), lastFinished(sw).slice(0, 80));
  const auditFrames = [...sl.matchAll(/event: audit_event\s*\ndata: (.*)/g)].map((m) => JSON.parse(m[1]));
  const okFrames = auditFrames.length > 0 && auditFrames.every((f, i) => ['stage_changed', 'message_delivered', 'closed'].includes(f.action) && f.rule_id == null && f.seq === i + 1);
  check('N1 loser SSE: no policy_block/escalation/approval frames; audit_event frames are the dense neutral view (stage_changed/message_delivered/closed, no rule_id)', !/event: (policy_block|escalation_created|approval_resolved|subagent_call|subagent_result)/.test(sl) && okFrames, JSON.stringify(auditFrames.slice(0, 3)));

  // ---- N5: AI badge on agent messages (buyer workspace), override-rates table in Settings
  await openWorkflow(admin, pid);
  const badge = await admin.evaluate(() => ({ ai: [...document.querySelectorAll('.feed .frow .badge, .feed .frow [class*=badge]')].filter((b) => /AI agent/.test(b.textContent)).length, rows: document.querySelectorAll('.feed .frow').length }));
  check('N5 AI agent badge shown on agent messages', badge.ai > 0, JSON.stringify(badge));
  await shot(admin, 'N5-ai-badge');
  await admin.goto(`${BASE}/settings`, { waitUntil: 'networkidle2' }); await sleep(1500);
  const tbl = await admin.evaluate(() => { const h = [...document.querySelectorAll('h2')].find((x) => /Reviewer override rates/.test(x.textContent)); const t = h?.parentElement.querySelector('table'); return { has: !!h, rows: t ? t.querySelectorAll('tbody tr').length : 0, text: t?.textContent.slice(0, 200) ?? h?.parentElement.textContent.slice(0, 120) }; });
  check('N5 Settings shows Reviewer override rates table with rows', tbl.has && tbl.rows > 0, JSON.stringify(tbl));
  await shot(admin, 'N5-override-rates');
  const rates = (await api(admin, 'GET', '/reviewers/override-rates')).json;
  check('N5 override-rates API returns reviewers with rates in [0,1]', rates.items?.length > 0 && rates.items.every((r) => r.override_rate >= 0 && r.override_rate <= 1), JSON.stringify(rates).slice(0, 160));
  const bRates = await api(buyer, 'GET', '/reviewers/override-rates');
  check('N5 override-rates is not available to BUYER (403)', bRates.status === 403, bRates.status);

  // ---- N2: overdue approvals
  const pid2 = await createProject(buyer, 'E2E N2 overdue', 'happy');
  await waitStatus(buyer, pid2, (p) => p.status === 'ESCALATED', 60000);
  await sleep(800);
  const fresh = (await api(buyer, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.project_id === pid2);
  check('N2 fresh pending approval has due_at and overdue=false', fresh.length === 1 && !!fresh[0].due_at && fresh[0].overdue === false, JSON.stringify(fresh[0] ?? null).slice(0, 200));
  dbExec("update approval set created_at=datetime(created_at,'-30 hours') where project_id=? and status='PENDING'", [pid2.replace(/-/g, '')]);
  const old = (await api(buyer, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.project_id === pid2);
  check('N2 aged pending approval is overdue=true', old.length === 1 && old[0].overdue === true, JSON.stringify(old[0] ?? null).slice(0, 200));
  await buyer.goto(`${BASE}/approvals`, { waitUntil: 'networkidle2' }); await buyer.waitForSelector('.inbox .appr', { timeout: 10000 }); await sleep(500);
  const rowsTxt = await buyer.$$eval('.inbox .appr', (r) => r.map((x) => x.textContent));
  check('N2 inbox row shows OVERDUE badge', rowsTxt.some((t) => /OVERDUE/.test(t)), rowsTxt.map((t) => t.slice(0, 60)).join(' | '));
  await shot(buyer, 'N2-overdue');
  // N3: stop cancels pending approvals
  const st = await api(buyer, 'POST', `/projects/${pid2}/stop`);
  check('N3 stop returns STOPPED/STOPPED_BY_USER', st.status === 200 && st.json.status === 'STOPPED', JSON.stringify(st.json));
  await sleep(800);
  const pend = (await api(buyer, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.project_id === pid2);
  const dbSt = dbExec("select status, comment from approval where project_id=?", [pid2.replace(/-/g, '')]);
  check('N3 no PENDING approvals remain for the stopped project (API)', pend.length === 0, JSON.stringify(dbSt));
  check('N3 no pending approval rows in DB for the stopped project', dbSt.every(([s]) => s !== 'PENDING'), JSON.stringify(dbSt));
  await buyer.goto(`${BASE}/approvals`, { waitUntil: 'networkidle2' }); await sleep(800);
  const inboxTxt = await buyer.$$eval('.inbox .appr', (r) => r.filter((x) => /pending/i.test(x.textContent)).map((x) => x.textContent));
  check('N3 inbox shows no actionable item for it', !inboxTxt.some((t) => /N2 overdue/.test(t)), inboxTxt.join('|').slice(0, 100));
  const verify = (await api(admin, 'GET', `/projects/${pid2}/audit/verify`)).json;
  check('N3 stopped project audit chain valid', verify.valid === true, JSON.stringify(verify).slice(0, 120));

  // ---- N4: structured need (API only; the UI form has no structured fields)
  const body = { title: 'E2E N4 structured', need_text: '', scenario: 'happy', seed: 42, category: 'pcb_assembly', volume_units: 5000, required_certs: ['ISO9001'], excluded_countries: ['CN'], max_lead_time_weeks: 20, target_price: '12.3456' };
  const c1 = await api(buyer, 'POST', '/projects', body);
  check('N4 structured need accepted (201) and echoed in the brief', c1.status === 201, JSON.stringify(c1.json).slice(0, 300));
  const brief = JSON.stringify((await api(buyer, 'GET', `/projects/${c1.json.id}`)).json.brief ?? {});
  check('N4 brief reflects structured fields', /pcb_assembly/.test(brief) && /5000/.test(brief), brief.slice(0, 300));
  const bad = [['category', 'nope'], ['volume_units', 0], ['required_certs', ['ISO9001', 'ISO9001']], ['excluded_countries', ['XX']], ['max_lead_time_weeks', 500], ['target_price', '-1'], ['industry', 'zzz']];
  for (const [k, v] of bad) { const r = await api(buyer, 'POST', '/projects', { ...body, [k]: v }); check(`N4 invalid ${k} -> 422`, r.status === 422, r.status); }
  const ex = await api(buyer, 'POST', '/projects', { ...body, mandate: { ceiling: 1 } });
  check('N4 unknown key (mandate smuggling) -> 422', ex.status === 422, ex.status);
  check('N4 start accepted', (await api(buyer, 'POST', `/projects/${c1.json.id}/start`)).status === 202);
  const r4 = await driveProject(A, buyer, c1.json.id, () => ({ act: 'approve' }), 180000);
  check('N4 run with structured need finishes without FAILED', ['DONE', 'STOPPED'].includes(r4.final.status), `${r4.final.status}/${r4.final.outcome}/${r4.final.reason}`);

  for (const p of [buyer, admin, loser, awardee, ...Object.values(A)]) noErrors(p);
  if (phases.length === 1) await finish();
}

if (phases.includes('C2')) {
  const admin = await login('admin'); const buyer = await login('buyer'); const A = await actors();
  const clickText = (page, sel, re) => page.evaluate((sel, re) => { const b = [...document.querySelectorAll(sel)].find((x) => new RegExp(re, 'i').test(x.textContent) && !x.disabled); if (!b) return false; b.click(); return true; }, sel, re);
  const list = (await api(buyer, 'GET', '/projects')).json.items;
  const oldP = list.find((p) => p.title === 'E2E happy');
  const oldBefore = JSON.stringify((await api(buyer, 'GET', `/projects/${oldP.id}`)).json);
  const oldCalls = (id) => dbExec('select tool from subagent_call where project_id=?', [id.replace(/-/g, '')]).map((r) => r[0]);
  check('C2 baseline: old project called weather_logistics', oldCalls(oldP.id).includes('weather_logistics'));
  const verBefore = (await api(admin, 'GET', '/studio/versions')).json[0].version;
  await admin.goto(`${BASE}/studio/agents`, { waitUntil: 'networkidle2' });
  await admin.waitForSelector('details[data-path="agents.buyer_lead"]');
  await admin.evaluate(() => { document.querySelector('details[data-path="agents.buyer_lead"]').open = true; });
  await clickText(admin, '[data-path="agents.buyer_lead.allowed_tools"] button', 'Set');
  await admin.waitForSelector('#f-agents\\.buyer_lead\\.allowed_tools');
  const addTool = async (t) => { await admin.type('[id="f-agents.buyer_lead.allowed_tools"]', t); await admin.keyboard.press('Enter'); await sleep(150); };
  // 1. compliance screen omitted -> invariant error, publish blocked
  for (const t of ['research_suppliers', 'regional_news_risk', 'weather_logistics', 'landed_cost']) await addTool(t);
  await sleep(1800);
  let err = await admin.$eval('[data-path="agents.buyer_lead.allowed_tools"]', (e) => e.textContent);
  check('C2 removing compliance_screen from buyer_lead is flagged with its invariant id', /INV_COMPLIANCE_TOOL_ALLOWED/.test(err), err.slice(-160));
  await shot(admin, 'C2-allowed-tools-invariant');
  await admin.click('.studio-bar .btn.primary'); await admin.waitForSelector('.dialog'); await sleep(900);
  check('C2 publish blocked while compliance_screen is missing', await admin.$eval('.dialog-foot .btn.primary', (b) => b.disabled));
  await admin.keyboard.press('Escape'); await sleep(300);
  // 2. adding compliance back but dropping a required tool (landed_cost): flagged as required
  const tagX = async (t) => admin.evaluate((t) => { const tag = [...document.querySelectorAll('[data-path="agents.buyer_lead.allowed_tools"] .tag, [data-path="agents.buyer_lead.allowed_tools"] .tags > *')].find((x) => x.textContent.includes(t)); const btn = tag?.querySelector('button'); if (btn) { btn.click(); return true; } return false; }, t);
  await addTool('compliance_screen');
  const removed = await tagX('landed_cost'); await sleep(1800);
  err = await admin.$eval('[data-path="agents.buyer_lead.allowed_tools"]', (e) => e.textContent);
  check('C2 removing a REQUIRED tool (landed_cost) is rejected as required', removed && /landed_cost is required/.test(err), `removed=${removed} ${err.slice(-120)}`);
  await addTool('landed_cost'); await sleep(200);
  // 3. remove only the optional weather_logistics -> valid
  const rw = await tagX('weather_logistics'); await sleep(1800);
  const barTxt = await admin.$eval('.studio-bar', (e) => e.textContent);
  check('C2 removing only optional weather_logistics (compliance kept) is valid', rw && /Valid/.test(barTxt) && !/error/i.test(barTxt.replace(/0 errors?/, '')), `${rw} ${barTxt.slice(0, 100)}`);
  const d = (await api(admin, 'GET', '/studio/draft')).json;
  check('C2 draft allowed_tools = research, news, landed + compliance (no weather)', JSON.stringify([...d.config.agents.buyer_lead.allowed_tools].sort()) === JSON.stringify(['compliance_screen', 'landed_cost', 'regional_news_risk', 'research_suppliers']), JSON.stringify(d.config.agents.buyer_lead.allowed_tools));
  await shot(admin, 'C2-allowed-tools-valid');
  // server-side smuggle: API publish of an invalid draft must fail even without the UI
  const bad = JSON.parse(JSON.stringify(d.config)); bad.agents.buyer_lead.allowed_tools = ['research_suppliers', 'regional_news_risk', 'landed_cost'];
  const sv = await api(admin, 'PUT', '/studio/draft', { config: bad });
  const pubBad = await api(admin, 'POST', '/studio/publish', { note: 'smuggle' });
  check('C2 server refuses to publish a draft without compliance_screen', pubBad.status >= 400 && pubBad.status < 500, `${sv.status}/${pubBad.status} ${JSON.stringify(pubBad.json).slice(0, 120)}`);
  await api(admin, 'PUT', '/studio/draft', { config: d.config });
  await admin.reload({ waitUntil: 'networkidle2' }); await sleep(800);
  await admin.click('.studio-bar .btn.primary'); await admin.waitForSelector('#pn');
  await admin.type('#pn', 'e2e: drop weather tool'); await sleep(900);
  await clickText(admin, '.dialog-foot button', 'Publish new version'); await sleep(1800);
  const vers = (await api(admin, 'GET', '/studio/versions')).json;
  check('C2 published a new active version', vers[0].version === verBefore + 1 && vers[0].active, JSON.stringify(vers[0]).slice(0, 160));
  // new project: pinned to the new version, weather call refused/absent; old project untouched
  const nid = await createProject(buyer, 'E2E allowed_tools new', 'happy');
  check('C2 new project pins the new version', (await projectState(admin, nid)).config_version === vers[0].version);
  const r = await driveProject(A, buyer, nid, () => ({ act: 'approve' }), 180000);
  const newCalls = oldCalls(nid);
  const aud = JSON.stringify((await api(admin, 'GET', `/projects/${nid}/audit?limit=500`)).json);
  const wRows = dbExec("select output from subagent_call where project_id=? and tool='weather_logistics'", [nid.replace(/-/g, '')]).map((r) => JSON.parse(r[0]));
  const oldW = dbExec("select output from subagent_call where project_id=? and tool='weather_logistics'", [oldP.id.replace(/-/g, '')]).map((r) => JSON.parse(r[0]));
  check('C2 new project: every weather_logistics attempt was refused NOT_ALLOWED (none executed); landed_cost still ran', wRows.length > 0 && wRows.every((o) => o.ok === false && o.error === 'NOT_ALLOWED') && newCalls.includes('landed_cost'), JSON.stringify(wRows[0]));
  check('C2 old project weather_logistics calls actually executed', oldW.length > 0 && oldW.every((o) => o.error !== 'NOT_ALLOWED'), JSON.stringify(oldW[0]).slice(0, 80));
  check('C2 refusal is audited as NOT_ALLOWED (fail closed)', /NOT_ALLOWED/.test(aud), `final=${r.final.status}; ${/NOT_ALLOWED/.test(aud)}`);
  check('C2 new project still completes and chain verifies', ['DONE', 'ESCALATED', 'STOPPED'].includes(r.final.status) && (await api(admin, 'GET', `/projects/${nid}/audit/verify`)).json.valid === true, `${r.final.status}/${r.final.outcome}`);
  check('C2 old project record + calls unchanged', JSON.stringify((await api(buyer, 'GET', `/projects/${oldP.id}`)).json) === oldBefore && oldCalls(oldP.id).includes('weather_logistics'));
  const rb = await api(admin, 'POST', '/studio/rollback', { to_version: verBefore, note: 'e2e: restore default tools' });
  check('C2 cleanup: rollback restores the previous config for later phases', rb.status === 201 && (await api(admin, 'GET', '/studio/active')).json.config.agents.buyer_lead.allowed_tools == null, rb.status);
  for (const p of [admin, buyer, ...Object.values(A)]) noErrors(p);
  if (phases.length === 1) await finish();
}

if (phases.includes('F2')) {
  // DONE project with a pending HOLD_RELEASE survives an API restart (runtime memory gone): FINANCE still releases it through the UI.
  const { spawn, execSync } = await import('node:child_process');
  const restartApi = async () => {
    try { execSync('pkill -f "uvicorn sourceloop.main:app"'); } catch { /* none */ }
    await sleep(1500);
    spawn('sh', ['-c', 'cd .. && exec scripts/start.sh >> data/e2e/api.log 2>&1'], { detached: true, stdio: 'ignore', env: { ...process.env, DATABASE_URL: 'sqlite+aiosqlite:///./data/e2e/e2e.db', RATE_LIMIT_CREATE: '1000/hour', RATE_LIMIT_START: '1000/hour', RATE_LIMIT_RESOLVE: '1000/minute', RATE_LIMIT_LOGIN: '1000/minute', MAX_ACTIVE_PROJECTS_PER_ORG: '200' } }).unref();
    for (let i = 0; i < 30; i++) { try { if ((await fetch('http://127.0.0.1:8005/api/v1/health')).ok) return; } catch { /* wait */ } await sleep(1000); }
    throw new Error('API did not come back');
  };
  const A = await actors(); const buyer = A.buyer;
  const pid = await createProject(buyer, 'E2E hold after restart', 'happy');
  const r = await driveProject(A, buyer, pid, (k) => (k === 'HOLD_RELEASE' ? { act: 'skip' } : { act: 'approve' }), 180000);
  check('F2 project DONE/DEAL with HOLD_RELEASE still pending', r.final.status === 'DONE' && r.final.outcome === 'DEAL' && !r.resolved.some((x) => x.kind === 'HOLD_RELEASE'), `${r.final.status}/${r.final.outcome}`);
  const pend0 = (await api(A.finance, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.project_id === pid && a.kind === 'HOLD_RELEASE');
  check('F2 HOLD_RELEASE pending before restart', pend0.length === 1, pend0.length);
  const hold0 = dbExec("select status from ledger_hold where project_id=?", [pid.replace(/-/g, '')]);
  await restartApi();
  check('F2 API healthy after restart', (await (await fetch('http://127.0.0.1:8005/api/v1/health')).json()).status === 'ok');
  for (const p of Object.values(A)) { await p.close().catch(() => null); await p.ctx.close().catch(() => null); }
  const fin = await login('finance');
  const pend1 = (await api(fin, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.project_id === pid && a.kind === 'HOLD_RELEASE');
  check('F2 HOLD_RELEASE still pending after restart', pend1.length === 1, pend1.length);
  const ps = await projectState(fin, pid);
  check('F2 project still DONE/DEAL after restart (not marked lost)', ps.status === 'DONE' && ps.outcome === 'DEAL', `${ps.status}/${ps.outcome}/${ps.reason}`);
  const done = await approverPass(fin, (k, p) => (p === pid && k === 'HOLD_RELEASE' ? { act: 'approve' } : { act: 'skip' }), { prefix: 'F2' });
  check('F2 FINANCE released the hold through the UI after restart', done.some((d) => d.kind === 'HOLD_RELEASE' && d.ok && !(d.err?.length)) && !/runtime|restart/i.test(JSON.stringify(done.map((d) => d.err))), JSON.stringify(done.map((d) => [d.kind, d.ok, d.err, d.toast])));
  await shot(fin, 'F2-hold-after-restart');
  const pend2 = (await api(fin, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.project_id === pid);
  const hold1 = dbExec("select status from ledger_hold where project_id=?", [pid.replace(/-/g, '')]);
  check('F2 no pending approvals and the ledger hold changed state', pend2.length === 0 && JSON.stringify(hold1) !== JSON.stringify(hold0), `${JSON.stringify(hold0)} -> ${JSON.stringify(hold1)}`);
  const adm = await login('admin');
  const v = (await api(adm, 'GET', `/projects/${pid}/audit/verify`)).json;
  check('F2 audit chain valid after the post-restart resolution', v.valid === true, JSON.stringify(v).slice(0, 100));
  noErrors(fin);
  if (phases.length === 1) await finish();
}

if (phases.includes('H')) {
  // HOLD_RELEASE for a DONE project is pending: FINANCE resolves it through the UI (used before and after an API restart; see e2e-restart-hold.sh)
  const fin = await login('finance');
  const items = (await api(fin, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.kind === 'HOLD_RELEASE');
  check('H pending HOLD_RELEASE exists for FINANCE', items.length > 0, items.length);
  const pid = items[0]?.project_id;
  const ps = await projectState(fin, pid);
  console.log('H project', pid, ps.status, ps.outcome);
  const done = await approverPass(fin, (k, p) => (p === pid ? { act: 'approve' } : { act: 'skip' }), { prefix: 'H' });
  check('H FINANCE released the hold through the UI (no runtime)', done.some((d) => d.kind === 'HOLD_RELEASE' && d.ok && !(d.err?.length)), JSON.stringify(done.map((d) => [d.kind, d.ok, d.err, d.toast])));
  const after = (await api(fin, 'GET', '/approvals?status=PENDING')).json.items.filter((a) => a.project_id === pid);
  check('H no pending approvals left for that project', after.length === 0, after.length);
  await shot(fin, 'H-hold-release');
  noErrors(fin);
  await finish();
}
if (phases.includes('END')) await finish();
