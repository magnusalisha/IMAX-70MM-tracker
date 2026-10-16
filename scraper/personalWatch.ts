import { readFile, writeFile, rename, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import nodemailer from "nodemailer";
import { scanLincoln } from "./personalAmc";
import {
  emptyState, parseState, validateConfig, isTarget, showtimeKey, deliverPending,
  recipientKey, type WatchState, type Message, type PersonalConfig,
} from "./personalCore";

const args = new Set(process.argv.slice(2));
const dryRun = args.has("--dry-run");
const watchdog = args.has("--watchdog");
const statePath = resolve(process.env.WATCHER_STATE_PATH ?? "../watcher-state/state.json");
const reportPath = resolve(process.env.WATCHER_REPORT_PATH ?? "../watcher-report.json");
const configPath = resolve(process.env.WATCHER_CONFIG_PATH ?? "../personal/config.json");

async function main() {
  const config = validateConfig(JSON.parse(await readFile(configPath, "utf8")) as PersonalConfig);
  // Diagnostic-only shortcut; live/scheduled scans always use committed config.
  const override = process.argv.find(a => a.startsWith("--scan-days="));
  if (override) {
    if (!dryRun) throw new Error("--scan-days override is allowed only in dry run");
    config.scanDays = Number(override.split("=")[1]); validateConfig(config);
  }
  let state: WatchState;
  try { state = parseState(JSON.parse(await readFile(statePath, "utf8"))); }
  catch (err) {
    if (dryRun && (err as NodeJS.ErrnoException).code === "ENOENT") state = emptyState();
    else throw err; // Missing/corrupt live state must never silently erase dedupe history.
  }
  const recipients = [...new Set((process.env.WATCHER_RECIPIENTS ?? "").split(",").map(e => e.trim().toLowerCase()).filter(Boolean))];
  if (recipients.some(e => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e))) throw new Error("Invalid alert recipient");
  if (!dryRun && (!recipients.length || !process.env.GMAIL_USER || !process.env.GMAIL_APP_PASSWORD)) {
    throw new Error("Configure WATCHER_RECIPIENTS, GMAIL_USER and GMAIL_APP_PASSWORD in GitHub Secrets before a live run");
  }
  const transport = dryRun ? null : nodemailer.createTransport({
    service: "gmail", auth: { user: process.env.GMAIL_USER, pass: process.env.GMAIL_APP_PASSWORD },
    connectionTimeout: 20000, greetingTimeout: 15000, socketTimeout: 30000,
  });
  const send = async (to: string, message: Message) => {
    if (!transport) throw new Error("Sending disabled in dry run");
    const result = await transport.sendMail({ from: process.env.GMAIL_USER, to, ...message });
    if (!result.accepted?.length || result.rejected?.length) throw new Error("SMTP recipient not accepted");
  };
  const save = async (s: WatchState) => {
    if (dryRun) return;
    await mkdir(dirname(statePath), { recursive: true });
    await writeFile(`${statePath}.tmp`, JSON.stringify(s, null, 2) + "\n");
    await rename(`${statePath}.tmp`, statePath);
    if (process.env.WATCHER_GIT_STATE === "true") {
      const cwd = dirname(statePath);
      const git = (params: string[]) => execFileSync("git", params, { cwd, stdio: "pipe", timeout: 30000 });
      git(["add", "state.json"]);
      if (git(["status", "--porcelain"]).toString().trim()) {
        git(["-c", "user.name=Lincoln Square Watcher", "-c", "user.email=watcher@users.noreply.github.com", "commit", "-m", "Checkpoint Lincoln Square watcher"]);
        git(["push", "origin", "HEAD:watcher-state"]);
      }
    }
  };
  const healthNotice = async () => {
    if (dryRun) return;
    for (const email of recipients) {
      const key = recipientKey(email), prior = state.healthDelivered[key];
      if (prior === state.health || (state.health === "ok" && prior !== "failed")) continue;
      const subject = state.health === "failed" ? "Lincoln Square watcher needs attention" : "Lincoln Square watcher recovered";
      const text = state.health === "failed"
        ? "The AMC scan failed or is stale. No ticket availability conclusion can be drawn. Check your fork's Actions tab. Your existing watcher has not been changed."
        : "The Lincoln Square AMC scan is working again.";
      await send(email, { subject, text, html: `<p>${text}</p>` });
      state.healthDelivered[key] = state.health;
      await save(state);
    }
  };
  if (args.has("--test-email")) {
    if (dryRun) throw new Error("Test email requires an explicit live invocation");
    for (const email of recipients) await send(email, {
      subject: "TEST — Lincoln Square Odyssey watcher",
      text: "Test notification only. This does not indicate a ticket drop.",
      html: "<p>Test notification only. This does not indicate a ticket drop.</p>",
    });
    console.log("Test notification accepted by SMTP; inbox receipt still needs confirmation.");
    return;
  }
  if (watchdog) {
    if (!state.lastScanAt || Date.now() - Date.parse(state.lastScanAt) > 45 * 60000) {
      state.health = "failed"; await save(state); await healthNotice();
      throw new Error("No successful full scan within 45 minutes");
    }
    console.log("Watcher full-scan heartbeat is fresh."); return;
  }
  try {
    const records = await scanLincoln(config, (date, count) => console.log(`[Lincoln Square] ${date}: ${count} listings`));
    const shows = records.filter(s => isTarget(s, config));
    await writeFile(reportPath, JSON.stringify({ checkedAt: new Date().toISOString(), theatre: config.theatre.name, scanDays: config.scanDays, targetDates: config.targetDates, totalListings: records.length, matchingShowtimes: shows, dryRun }, null, 2));
    if (dryRun) { console.log(`DRY RUN: ${shows.length} future Odyssey IMAX 70mm listings. No history changed; no email sent.`); return; }
    state.observed = Object.fromEntries(shows.map(s => [showtimeKey(s, config), s]));
    state.initialized = true; state.lastScanAt = new Date().toISOString(); state.health = "ok";
    await save(state); // Found screenings survive even if SMTP subsequently fails.
    await healthNotice();
    const delivery = await deliverPending(state, recipients, config, send, save);
    console.log(`Digest recipients sent=${delivery.sent} failed=${delivery.errors}`);
    if (delivery.errors) throw new Error("One or more digests failed; retained for retry");
  } catch (err) {
    if (!dryRun) {
      // lastScanAt only advances after a successful full scan; failures remain
      // visible to the independent watchdog as well as the Actions run status.
      state.health = "failed"; await save(state);
      try { await healthNotice(); } catch { console.error("Health email failed; inspect Actions run."); }
    }
    throw err;
  } finally { transport?.close(); }
}
main().catch(err => { console.error((err as Error).message); process.exitCode = 1; });
