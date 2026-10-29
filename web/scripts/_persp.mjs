import puppeteer from 'puppeteer-core';
import fs from 'fs';
const L=fs.readFileSync('data/demo-creds.txt','utf8').split('\n').map(l=>l.match(/(\S+@sourceloop\.test)\s+(\w+)\s+(\S+)/)).filter(Boolean);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const b=await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--no-sandbox']});
async function sess(email){const ctx=await b.createBrowserContext();const p=await ctx.newPage();await p.setViewport({width:1440,height:1000});await p.goto('http://localhost:3010/',{waitUntil:'networkidle2'});
 const m=L.find(x=>x[1]===email);await p.type('input[type=email]',m[1]);await p.type('input[type=password]',m[3]);await p.keyboard.press('Enter');await sleep(2500);return p;}
async function openDone(p){const rows=await p.$$('tbody tr');const t=await p.$$eval('tbody tr',r=>r.map(x=>x.innerText));const i=t.findIndex(x=>/deal reached|Completed/i.test(x));await rows[i>=0?i:0].click();await sleep(3500);}
const shots=[['buyer@sourceloop.test','buyer'],['supplier1@sourceloop.test','supplier'],['buyeradmin@sourceloop.test','orgadmin'],['supplier1admin@sourceloop.test','supadmin'],['superadmin@sourceloop.test','super']];
for(const [email,tag] of shots){
  const p=await sess(email);
  await openDone(p);
  console.log(tag,p.url().split('/').pop().slice(0,8), (await p.$$eval('[role=tab]',t=>t.map(x=>x.textContent))).join('|')||'(no tabs)', '| nav:', (await p.$$eval('.nav a',a=>a.map(x=>x.textContent.replace(/\d+/,'').trim()))).join(','));
  await p.screenshot({path:`data/ui/p-${tag}.png`,fullPage:false});
  if(['orgadmin','super'].includes(tag)){const tabs=await p.$$('[role=tab]');await tabs[1].click();await sleep(2500);await p.screenshot({path:`data/ui/p-${tag}-workflow.png`});await tabs[2].click();await sleep(1500);await p.screenshot({path:`data/ui/p-${tag}-activity.png`});}
  if(['orgadmin','super'].includes(tag)){await p.goto('http://localhost:3010/organization',{waitUntil:'networkidle2'});await sleep(1500);await p.screenshot({path:`data/ui/p-${tag}-org.png`});}
  await p.close();
}
await b.close();
