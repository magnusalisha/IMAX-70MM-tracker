// Read-only ticket-link validation. No login, ticket quantities, seats, or payment.
import { chromium } from '../scraper/node_modules/playwright/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
const base = 'https://www.atomtickets.com/theaters/amc-lincoln-square-13/164';
const userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const results = [];
await mkdir('atom-browser-results', { recursive: true });
const browser = await chromium.launch();
async function record(page, phase) {
  const text = await page.locator('body').innerText().catch(() => '');
  const r = { phase, at: new Date().toISOString(), url: page.url(), title: await page.title(),
    challenge: /just a moment|verif(?:y|ying|ication).*human|security verification|access denied/i.test(text),
    movieConfirmed: text.includes('Purchase tickets for Dune: Part Three'),
    theatreConfirmed: text.includes('AMC Lincoln Square 13'),
    dateTimeConfirmed: /Dec 18 at 6:00 AM/.test(text),
    formatConfirmed: /IMAX 70\s*MM/i.test(text),
    purchaseStarted: false,
    visibleText: text.slice(0, 5000) };
  results.push(r); console.log(JSON.stringify(r));
  await page.screenshot({ path: `atom-browser-results/${phase}.png`, fullPage: false }).catch(() => {});
}
try {
  const context = await browser.newContext({ userAgent, locale: 'en-US', timezoneId: 'America/New_York' });
  const page = await context.newPage();
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.locator('a[href^="/movies/359230/showtimes?"][href*="2026-12-18"]').first().click({ timeout: 30000 });
  const dialog = page.getByRole('dialog', { name: 'Showtimes for Friday, Dec 18' });
  await dialog.getByRole('button', { name: '6:00 AM', exact: true }).waitFor({ timeout: 20000 });
  await record(page, 'dated-listing');
  await dialog.getByRole('button', { name: '6:00 AM', exact: true }).click();
  await page.getByRole('heading', { name: 'Purchase tickets for Dune: Part Three', exact: true }).waitFor({ timeout: 30000 }).catch(() => {});
  await record(page, 'normal-flow-checkout');
  const fresh = await browser.newContext({ userAgent, locale: 'en-US', timezoneId: 'America/New_York' });
  const direct = await fresh.newPage();
  await direct.goto('https://www.atomtickets.com/checkout/642708403#/tickets', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await direct.getByRole('heading', { name: 'Purchase tickets for Dune: Part Three', exact: true }).waitFor({ timeout: 20000 }).catch(() => {});
  await record(direct, 'fresh-direct-checkout');
} catch (err) { results.push({ error: err.message }); console.error(err.message); }
finally {
  await browser.close();
  await writeFile('atom-browser-results/results.json', JSON.stringify(results, null, 2));
}
