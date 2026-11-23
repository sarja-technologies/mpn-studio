// ad-hoc: the Pairings screen with the package import actions, and a package import through the window's folder prompt.
import { chromium } from '@playwright/test';
const [out, w = '1440', h = '900', path = ''] = process.argv.slice(2);
const b = await chromium.launch({ channel: 'chrome', headless: true });
const p = await b.newPage({ viewport: { width: +w, height: +h } });
const logs = [];
p.on('pageerror', (e) => logs.push('PAGEERROR: ' + e.message));
p.on('console', (m) => m.type() === 'error' && logs.push('console: ' + m.text()));
p.on('dialog', (d) => (path ? d.accept(path) : d.dismiss()));
await p.goto(process.env.MPN_URL ?? 'http://127.0.0.1:8871/');
const shot = async (n) => { await p.waitForTimeout(600); await p.screenshot({ path: `${out}/${n}-${w}.png` }); };
await p.getByRole('button', { name: 'Model library' }).click();
await p.getByRole('button', { name: 'Pairings', exact: true }).first().click();
await shot('pairings');
if (path) {
  await p.getByRole('button', { name: 'Import package folder' }).click();
  await p.getByText(/package is imported|already imported/).waitFor({ timeout: 30000 }).catch(() => logs.push('no toast'));
  await shot('pairings-after-import');
}
await p.locator('.studio').getByRole('button', { name: 'Open', exact: true }).nth(process.env.ROW ? +process.env.ROW : 0).click().catch(() => {});
await p.getByRole('heading', { name: 'Build' }).waitFor({ timeout: 10000 }).catch(() => logs.push('no build heading'));
await shot('pairing-page');
console.log(logs.join('\n') || 'no console errors');
await b.close();
