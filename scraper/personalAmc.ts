import { chromium, type Page } from "playwright";
import { extractAmcInPage } from "./scrape";
import { normalizeAmcRecords } from "./parseAmc";
import { type PersonalConfig, scanDates } from "./personalCore";
import type { NormalizedShowtimeLite } from "./types";

export function assertAmcPage(url: string, title: string, body: string, recordCount: number): void {
  if (new URL(url).hostname !== "www.amctheatres.com" ||
      /just a moment|attention required|access denied|waiting room|you're in line|you are in line|enable javascript|verify you are human/i.test(`${title} ${body}`)) {
    throw new Error("AMC returned a queue, challenge, or unexpected host");
  }
  if (!/AMC Lincoln Square 13/i.test(body)) throw new Error("Lincoln Square page identity not confirmed");
  // Unknown/half-rendered pages are failures, never healthy empty dates.
  if (recordCount === 0 && !/no (?:movies|showtimes|showings)|showtimes (?:are |not |have )?(?:not available|available soon|announced)|sorry.{0,80}showtimes/i.test(body)) {
    throw new Error("AMC page has no extractable showtimes or explicit empty-date marker");
  }
}
async function extractDate(page: Page, c: PersonalConfig, date: string) {
  const url = new URL(c.theatre.showtimesUrl); url.searchParams.set("date", date);
  const response = await page.goto(url.toString(), { waitUntil: "domcontentloaded", timeout: 45000 });
  if (!response?.ok()) throw new Error(`AMC HTTP ${response?.status() ?? "unknown"}`);
  // Allow lazy experience groups to populate before extraction.
  for (let y = 0; y < 6; y++) {
    await page.evaluate(n => window.scrollTo(0, n * window.innerHeight), y);
    await page.waitForTimeout(450);
  }
  await page.waitForSelector('section[aria-label^="Showtimes for"] a[href^="/showtimes/"]', { timeout: 5000 }).catch(() => {});
  const records = await page.evaluate(extractAmcInPage);
  const body = await page.locator("body").innerText();
  assertAmcPage(page.url(), await page.title(), body, records.length);
  return normalizeAmcRecords(records.map(r => ({ ...r, queryDate: date })));
}
export async function scanLincoln(c: PersonalConfig, onDate?: (date: string, records: number) => void): Promise<NormalizedShowtimeLite[]> {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      locale: "en-US", timezoneId: c.theatre.timeZone, viewport: { width: 1280, height: 800 },
    });
    const page = await context.newPage();
    const found = new Map<string, NormalizedShowtimeLite>();
    for (const date of scanDates(c)) {
      let records: NormalizedShowtimeLite[] | undefined;
      for (let attempt = 0; attempt < 2; attempt++) {
        try { records = await extractDate(page, c, date); break; }
        catch (err) { if (attempt === 1) throw new Error(`Scan failed for ${date}: ${(err as Error).message}`); }
      }
      for (const s of records ?? []) found.set(s.externalId, s);
      onDate?.(date, records?.length ?? 0);
    }
    return [...found.values()];
  } finally { await browser.close(); }
}
