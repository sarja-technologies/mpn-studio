// ad-hoc: walk through the cores / networks / pairings / releases screens and take screenshots.
//   node e2e/look-cores.mjs <outdir> [w] [h] [part]       part: cores | pairing | releases | settings | all
import { chromium } from '@playwright/test';
import { readFileSync } from 'node:fs';
const [out, w = '1440', h = '900', part = 'all'] = process.argv.slice(2);
const keyA = process.env.KEY_A_FILE ? readFileSync(process.env.KEY_A_FILE, 'utf8').trim() : '';
const b = await chromium.launch({ channel: 'chrome', headless: true });
const p = await b.newPage({ viewport: { width: +w, height: +h } });
const logs = [];
p.on('pageerror', (e) => logs.push('PAGEERROR: ' + e.message));
p.on('console', (m) => m.type() === 'error' && logs.push('console: ' + m.text()));
let promptAnswer = null;
p.on('dialog', (d) => (promptAnswer ? d.accept(promptAnswer) : d.dismiss()));
await p.goto(process.env.MPN_URL ?? 'http://127.0.0.1:8871/');
const shot = async (name, full = false) => { await p.waitForTimeout(500); await p.screenshot({ path: `${out}/${name}-${w}.png`, fullPage: full }); };
const tab = (n) => p.getByRole('button', { name: n, exact: true }).first().click();
const want = (x) => part === 'all' || part === x;
await p.getByRole('button', { name: 'Model library' }).click();
await p.getByRole('button', { name: 'Pairings', exact: true }).waitFor({ timeout: 20000 });
await p.waitForTimeout(1500);

if (want('cores')) {
  await tab('Cognitive cores');
  await shot('cores');
  await p.getByRole('button', { name: 'Add a core on a server' }).click();
  const dlg = p.getByRole('dialog');
  await dlg.getByLabel('Name').fill('Core A again');
  await dlg.getByLabel('Server address').fill('http://127.0.0.1:18081/v1');
  await dlg.getByLabel('Model id').fill('core-a');
  await dlg.getByRole('button', { name: 'Test connection' }).click();
  await dlg.getByRole('status').first().waitFor();
  await shot('core-dialog-auth-error');
  await dlg.getByLabel(/API key/).fill(keyA);
  await dlg.getByRole('button', { name: 'Test connection' }).click();
  await dlg.getByText(/Answered in/).waitFor();
  await shot('core-dialog-ok');
  await dlg.getByLabel('Server address').fill('http://127.0.0.1:18999/v1');
  await dlg.getByRole('button', { name: 'Test connection' }).click();
  await dlg.getByText(/Nothing is listening/).waitFor();
  await shot('core-dialog-unreachable');
  await dlg.getByRole('button', { name: 'Cancel' }).click();
  await p.getByRole('button', { name: 'Test connection' }).first().click();
  await p.waitForTimeout(800);
  await p.getByRole('button', { name: 'Edit' }).first().click();
  await shot('core-edit');
  await p.keyboard.press('Escape');
}
if (want('pairing')) {
  await tab('Pairings');
  await p.locator('.studio').getByRole('button', { name: 'Open', exact: true }).first().click();
  await p.getByRole('heading', { name: 'Build' }).waitFor();
  await shot('pairing-core-a');
  const sel = p.getByLabel('Cognitive core');
  const cur = await sel.evaluate((e) => e.options[e.selectedIndex].text);
  const other = cur.includes('Core B') ? 'Core A' : 'Core B';
  const v = await sel.locator('option', { hasText: other }).getAttribute('value');
  await sel.selectOption(v);
  await p.getByRole('button', { name: 'Apply core' }).waitFor();
  await shot('pairing-core-changed');
  await p.getByRole('button', { name: 'Apply core' }).click();
  await p.getByText(/now answers with/).waitFor({ timeout: 60000 }).catch(() => {});
  await shot('pairing-core-applied');
  await p.getByRole('button', { name: 'Start', exact: true }).click();
  const box = p.getByPlaceholder('Ask the model');
  await box.waitFor({ timeout: 90000 });
  await box.fill('What is Incense?');
  await p.getByRole('button', { name: 'Ask', exact: true }).click();
  await p.getByRole('button', { name: 'Stop answer' }).waitFor();
  await p.getByRole('button', { name: 'Ask', exact: true }).waitFor({ timeout: 90000 });
  await shot('pairing-asked');
}
const inst = async (name, opts = {}) => {
  const row = p.locator('.studio tr', { hasText: name }).first();
  await row.scrollIntoViewIfNeeded();
  if (opts.select) await row.locator('select').selectOption(opts.select);
  await row.getByRole('button', { name: opts.button ?? 'Install' }).click();
  return row;
};
if (want('releases')) {
  await tab('MPN networks');
  await shot('networks-official');
  await inst('Fixture FG-MPN demo');
  await p.locator('.studio').getByText('Installed FG-MPN networks').waitFor({ timeout: 60000 });
  await shot('networks-fgmpn-installed');
  await inst('Fixture N10K slice');
  await p.locator('.studio tr', { hasText: 'Fixture N10K slice' }).getByText('Installed', { exact: true }).waitFor({ timeout: 60000 });
  await shot('networks-slice-installed', true);
  await inst('damaged file on the server');
  await p.locator('.studio').getByText(/does not match its SHA-256/).waitFor({ timeout: 60000 });
  await shot('networks-errors', true);
  await tab('Cognitive cores');
  await inst('Fixture core 4B', { select: 'Q8_0' });
  await p.locator('.studio tr', { hasText: 'Fixture core 4B' }).getByText('Installed', { exact: true }).last().waitFor({ timeout: 60000 });
  await shot('cores-official-installed', true);
  await tab('Pairings');
  const srv = await p.locator('.studio tr', { hasText: 'Fixture N10K-C4B' }).locator('select option', { hasText: 'Use Core B' }).getAttribute('value');
  await inst('Fixture N10K-C4B', { select: srv });
  await p.locator('.studio tr', { hasText: 'Fixture N10K-C4B' }).getByText('Installed', { exact: true }).waitFor({ timeout: 60000 });
  await shot('pairings-official-installed', true);
  await tab('MPN networks');
  await p.locator('.studio').getByRole('button', { name: 'Explore' }).first().click();
  await p.waitForTimeout(6000);
  await shot('fgmpn-explorer');
}
if (want('extra')) {
  // a manifest imported from a file (the system chooser is a typed path in a browser)
  await tab('Cognitive cores');
  promptAnswer = process.env.LOCAL_MANIFEST;
  await p.locator('.studio').getByRole('button', { name: 'Import manifest file' }).click();
  promptAnswer = null;
  await p.locator('.studio tr', { hasText: 'Fixture core from an imported file' }).waitFor();
  await shot('cores-imported-manifest');
  // a core's "Pair with a network" opens the composer with that core chosen
  await p.locator('.studio tr', { hasText: 'Core B' }).first().getByRole('button', { name: /More actions/ }).click();
  await p.getByRole('menuitem', { name: 'Pair with a network' }).click();
  await p.getByRole('heading', { name: /New pairing|Pair a core/ }).waitFor();
  await shot('new-pairing-preset');
  await p.getByRole('button', { name: 'Cancel' }).click().catch(() => {});
  // cancel an install in progress, then continue it
  await tab('MPN networks');
  const row = await inst('served slowly');
  await row.locator('..').getByText(/%/).first().waitFor({ timeout: 30000 });
  await p.waitForTimeout(2500);
  await shot('install-in-progress');
  await p.locator('.studio').getByRole('button', { name: 'Cancel', exact: true }).click();
  await p.locator('.studio').getByText(/Cancelled\./).waitFor({ timeout: 30000 });
  await shot('install-cancelled');
  await p.locator('.studio tr', { hasText: 'served slowly' }).getByRole('button', { name: 'Try again' }).click();
  await p.locator('.studio tr', { hasText: 'served slowly' }).getByText('Installed', { exact: true }).waitFor({ timeout: 90000 });
  await shot('install-resumed-done');
}
if (want('settings')) {
  await p.locator('.studio').getByRole('button', { name: 'Settings', exact: true }).click();
  await p.getByRole('tab', { name: 'Releases' }).click();
  await shot('settings-releases');
}
console.log(logs.join('\n') || 'no page errors');
await b.close();
