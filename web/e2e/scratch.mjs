// ad-hoc look at the running page: node e2e/scratch.mjs <out.png> [w] [h] [js to eval before the shot]
import { chromium } from '@playwright/test';
const [out, w = '1440', h = '900', js = ''] = process.argv.slice(2);
const b = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-gpu', '--ignore-gpu-blocklist'] });
const p = await b.newPage({ viewport: { width: +w, height: +h } });
const logs = [];
p.on('console', (m) => logs.push(m.type() + ': ' + m.text()));
p.on('pageerror', (e) => logs.push('PAGEERROR: ' + e.message));
await p.goto(process.env.MPN_URL ?? 'http://127.0.0.1:5174/');
await p.waitForFunction(() => window.__mpn && window.__mpn.level() !== null, null, { timeout: 15000 }).catch((e) => logs.push('WAIT: ' + e.message));
if (js) logs.push('EVAL: ' + JSON.stringify(await p.evaluate(js).catch((e) => 'ERR ' + e.message)));
await p.waitForTimeout(900);
logs.push('STATS: ' + JSON.stringify(await p.evaluate(() => window.__mpn && { ...window.__mpn.stats, requests: window.__mpn.stats.requests.length, cam: window.__mpn.cam })));
logs.push('GL: ' + (await p.evaluate(() => { const c = document.createElement('canvas').getContext('webgl2'); const e = c && c.getExtension('WEBGL_debug_renderer_info'); return c ? c.getParameter(e ? e.UNMASKED_RENDERER_WEBGL : c.RENDERER) : 'none'; })));
await p.screenshot({ path: out });
console.log(logs.join('\n'));
await b.close();
