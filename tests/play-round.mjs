// Drive the page over the Chrome DevTools Protocol: enter a name, tap the game, check it runs, crash it, check the save.
// Usage: node tests/play-round.mjs http://<host>:<port>/   (needs google-chrome-stable and node >= 22)
import { spawn } from 'node:child_process';
const url = process.argv[2];
const chrome = spawn('google-chrome-stable', ['--headless=new','--disable-gpu','--no-sandbox','--remote-debugging-port=9333','--window-size=900,700','--user-data-dir=/tmp/dino-cdp-profile','about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
await sleep(1500);
const targets = await (await fetch('http://127.0.0.1:9333/json')).json();
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map(); const logs = [];
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } if (d.method === 'Runtime.exceptionThrown') logs.push('EXC ' + d.params.exceptionDetails.exception?.description); if (d.method === 'Runtime.consoleAPICalled') logs.push('LOG ' + d.params.args.map(a => a.value).join(' ')); };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result?.exceptionDetails) return 'EXC:' + r.result.exceptionDetails.exception?.description?.split('\n')[0]; if (!r.result) return 'ERR:' + JSON.stringify(r).slice(0,200); return r.result.result.value; };
await send('Runtime.enable'); await send('Page.enable');
const nav = await send('Page.navigate', { url }); console.log('nav:', JSON.stringify(nav.result)); await sleep(2500); console.log('url:', await ev('location.href'), 'title:', await ev('document.title'));
console.log('name dialog shown:', await ev(`document.getElementById('nameov').classList.contains('show')`));
await ev(`document.getElementById('name').value='CDP'; document.getElementById('nameform').requestSubmit(); 1`);
await sleep(300);
console.log('dialog hidden:', !(await ev(`document.getElementById('nameov').classList.contains('show')`)));
console.log('runner ready:', await ev(`!!(Runner.instance_ && Runner.instance_.tRex)`));
const tap = () => ev(`(()=>{const g=document.getElementById('game'); g.dispatchEvent(new MouseEvent('mousedown',{bubbles:true})); setTimeout(()=>g.dispatchEvent(new MouseEvent('mouseup',{bubbles:true})),80); return 1})()`);
await tap(); await sleep(1500);
console.log('playing after tap:', await ev(`Runner.instance_.playing`), 'distance:', await ev(`Math.round(Runner.instance_.distanceRan)`));
await tap(); await sleep(400);
console.log('jumping after 2nd tap:', await ev(`Runner.instance_.tRex.jumping`));
// run until it crashes (no more taps), max 25 s
for (let i = 0; i < 50 && !(await ev(`Runner.instance_.crashed`)); i++) await sleep(500);
console.log('crashed:', await ev(`Runner.instance_.crashed`), 'score shown:', await ev(`document.getElementById('hint').textContent`));
await sleep(1500);
console.log('status:', await ev(`document.getElementById('status').textContent`));
console.log('board rows:', await ev(`document.querySelectorAll('#board tr').length`), 'you row:', await ev(`!!document.querySelector('#board tr.you')`));
await sleep(900); await tap(); await sleep(500);
console.log('restarted on tap:', await ev(`Runner.instance_.playing && !Runner.instance_.crashed`));
console.log('console/exceptions:', logs.length ? logs.join('\n') : 'none');
ws.close(); chrome.kill();
