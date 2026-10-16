// Browser integration fixture: uses the actual AMC DOM extractor, then the
// personal filter and digest delivery. No network access or real email.
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { readFile } from "node:fs/promises";
import { extractAmcInPage } from "./scrape";
import { normalizeAmcRecords } from "./parseAmc";
import { validateConfig, isTarget, emptyState, showtimeKey, deliverPending, type Message } from "./personalCore";
const config = validateConfig(JSON.parse(await readFile(new URL("../personal/config.json", import.meta.url), "utf8")));
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  const render = async (extra: string) => {
    await page.setContent(`<h1>AMC Lincoln Square 13</h1>
      <section aria-label="Showtimes for The Odyssey" id="the-odyssey-76238">
        <h3>IMAX 70MM: EXTRAORDINARY AWAITS</h3>
        <a href="/showtimes/111"><time datetime="2026-07-21T23:00:00Z">7pm</time></a>${extra}
        <h3>Dolby Cinema at AMC: COMPLETELY CAPTIVATING</h3>
        <a href="/showtimes/222"><time datetime="2026-07-22T00:00:00Z">8pm</time></a>
      </section>
      <section aria-label="Showtimes for Another Film" id="another-film-99999">
        <h3>IMAX 70MM: EXTRAORDINARY AWAITS</h3>
        <a href="/showtimes/333"><time datetime="2026-07-22T00:00:00Z">8pm</time></a>
      </section>`);
    return normalizeAmcRecords(await page.evaluate(extractAmcInPage)).filter(s => isTarget(s, config, new Date("2026-07-20")));
  };
  const state = emptyState(), sent: Message[] = [];
  const send = async (_to: string, message: Message) => { sent.push(message); };
  const save = async () => {};
  const first = await render(""); assert.equal(first.length, 1);
  for (const s of first) state.observed[showtimeKey(s, config)] = s;
  await deliverPending(state, ["fixture@example.com"], config, send, save, new Date("2026-07-20"));
  const second = await render('<a href="/showtimes/444"><time datetime="2026-07-22T03:00:00Z">11pm</time></a>');
  for (const s of second) state.observed[showtimeKey(s, config)] = s;
  await deliverPending(state, ["fixture@example.com"], config, send, save, new Date("2026-07-20"));
  assert.equal(sent.length, 2);
  assert.ok(sent[1].text.includes("11:00 PM EDT"));
  assert.ok(sent[1].text.includes("/showtimes/444"));
  assert.ok(!sent[1].text.includes("/showtimes/111"));
  assert.ok(!sent[1].text.includes("/showtimes/222"));
  console.log("Browser integration PASS: actual AMC DOM -> Odyssey IMAX 70mm -> new-showtime digest, correct Eastern time and direct link; no email sent.");
} finally { await browser.close(); }
