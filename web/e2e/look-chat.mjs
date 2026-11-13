// ad-hoc: start a built model in the Studio, ask questions, screenshot the answers and their evidence. node e2e/look-chat.mjs <outdir> <question>...
import { chromium } from '@playwright/test';
const [out, ...qs] = process.argv.slice(2);
const b = await chromium.launch({ channel: 'chrome', headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
const logs = [];
p.on('pageerror', (e) => logs.push('PAGEERROR: ' + e.message));
p.on('console', (m) => m.type() === 'error' && logs.push('console: ' + m.text()));
await p.goto(process.env.MPN_URL ?? 'http://127.0.0.1:8871/');
await p.getByRole('button', { name: 'Open', exact: true }).first().click();
await p.getByRole('button', { name: 'Start' }).click();
const t0 = Date.now();
await p.getByRole('textbox', { name: 'Question' }).and(p.locator(':enabled')).waitFor({ timeout: 300000 });
logs.push(`ready after ${((Date.now() - t0) / 1000).toFixed(1)} s`);
for (const q of qs) {
  await p.getByRole('textbox', { name: 'Question' }).fill(q);
  await p.getByRole('button', { name: 'Ask' }).click();
  await p.getByRole('button', { name: 'Ask' }).waitFor({ timeout: 300000 });
  await p.locator('details.ev').last().waitFor({ timeout: 60000 }).catch(() => logs.push('no evidence for: ' + q));
}
for (const d of await p.locator('details.ev').all()) await d.locator('summary').click();
await p.getByRole('heading', { name: 'Test' }).scrollIntoViewIfNeeded();
await p.waitForTimeout(400);
await p.screenshot({ path: `${out}/chat.png`, fullPage: false });
const turns = await p.locator('.turn').allInnerTexts();
console.log(turns.join('\n-----\n'));
await p.getByRole('button', { name: 'Connect another app' }).click();
await p.waitForTimeout(300);
const base = (await p.locator('.conn code').first().innerText()).trim();
const model = (await p.locator('.conn code').nth(1).innerText()).trim();
const key = (await p.locator('.conn code').nth(2).innerText()).trim();
const r = await fetch(base + '/chat/completions', { method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer ' + key },
  body: JSON.stringify({ model, messages: [{ role: 'user', content: 'Who are you? One sentence.' }], max_tokens: 120 }) });
const j = await r.json();
console.log('OpenAI API', r.status, base.replace(/:\d+/, ':PORT'), model, '->', j.choices?.[0]?.message?.content);
const nokey = await fetch(base + '/models');
console.log('without key:', nokey.status);
await p.getByRole('button', { name: 'Stop', exact: true }).click();
await p.waitForTimeout(1500);
await p.screenshot({ path: `${out}/chat-stopped.png` });
console.log(logs.join('\n') || 'no page errors');
await b.close();
