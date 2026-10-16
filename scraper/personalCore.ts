import { createHash } from "node:crypto";
import { addDaysYmd, ymdInZone } from "./theatres";
import type { NormalizedShowtimeLite } from "./types";

export interface PersonalConfig {
  theatre: { chain: string; externalId: string; name: string; city: string; timeZone: string; showtimesUrl: string };
  movie: { title: string; movieIds: string[] };
  scanDays: number;
  targetDates: string[];
}
export interface WatchState {
  version: 1;
  initialized: boolean;
  observed: Record<string, NormalizedShowtimeLite>;
  delivered: Record<string, string[]>;
  lastScanAt: string | null;
  health: "unknown" | "ok" | "failed";
  // Only recipient hashes are persisted in the public state branch.
  healthDelivered: Record<string, string>;
}
export const emptyState = (): WatchState => ({
  version: 1, initialized: false, observed: {}, delivered: {}, lastScanAt: null,
  health: "unknown", healthDelivered: {},
});
export function parseState(value: unknown): WatchState {
  const s = value as WatchState;
  const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
  if (!s || s.version !== 1 || typeof s.initialized !== "boolean" ||
      !object(s.observed) || !object(s.delivered) || !object(s.healthDelivered) ||
      Object.entries(s.observed).some(([key, item]) => !object(item) ||
        typeof item.externalId !== "string" || key !== `amc-lincoln-square-13:${item.externalId}` ||
        typeof item.startsAt !== "string" || !Number.isFinite(Date.parse(item.startsAt)) ||
        typeof item.format !== "string" || typeof item.movieTitle !== "string") ||
      Object.values(s.healthDelivered).some(status => status !== "ok" && status !== "failed") ||
      !["unknown", "ok", "failed"].includes(s.health) ||
      !(s.lastScanAt === null || (typeof s.lastScanAt === "string" && Number.isFinite(Date.parse(s.lastScanAt)))) ||
      Object.values(s.delivered).some(v => !Array.isArray(v) || v.some(id => typeof id !== "string"))) {
    throw new Error("Invalid watcher state; refusing to reset history");
  }
  return s;
}
export function recipientKey(email: string): string {
  return createHash("sha256").update(email.trim().toLowerCase()).digest("hex");
}
export function validateConfig(c: PersonalConfig): PersonalConfig {
  if (!c || c.theatre?.externalId !== "amc-lincoln-square-13" || c.movie?.title !== "The Odyssey" ||
      !Number.isInteger(c.scanDays) || c.scanDays < 1 || c.scanDays > 120 || !Array.isArray(c.targetDates)) {
    throw new Error("Invalid Lincoln Square personal configuration");
  }
  new Intl.DateTimeFormat("en-US", { timeZone: c.theatre.timeZone });
  const url = new URL(c.theatre.showtimesUrl);
  if (url.origin !== "https://www.amctheatres.com" || !url.pathname.includes("/amc-lincoln-square-13/showtimes")) {
    throw new Error("Unexpected theatre URL");
  }
  for (const date of c.targetDates) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(`${date}T00:00:00Z`)) ||
        new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date) throw new Error("Invalid target date");
  }
  return c;
}
// Always scan the whole rolling window, even across empty stretches. Explicit
// target dates let an advance engagement beyond that window be monitored too.
export function scanDates(c: PersonalConfig, now = new Date()): string[] {
  const today = ymdInZone(now, c.theatre.timeZone);
  return [...new Set([
    ...Array.from({ length: c.scanDays }, (_, i) => addDaysYmd(today, i)),
    ...c.targetDates.filter(d => d >= today),
  ])].sort();
}
export function isTarget(s: NormalizedShowtimeLite, c: PersonalConfig, now = new Date()): boolean {
  const date = new Date(s.startsAt);
  return date > now && /\bimax\b/i.test(s.format) && /\b70\s*mm\b/i.test(s.format) &&
    (c.movie.movieIds.includes(s.movieExternalId ?? "") ||
      s.movieTitle.trim().toLowerCase() === c.movie.title.toLowerCase()) &&
    /^\d+$/.test(s.externalId);
}
export function showtimeKey(s: NormalizedShowtimeLite, c: PersonalConfig): string {
  return `${c.theatre.externalId}:${s.externalId}`;
}
export function ticketUrl(s: NormalizedShowtimeLite): string {
  if (!/^\d+$/.test(s.externalId)) throw new Error("Invalid AMC showtime id");
  return `https://www.amctheatres.com/showtimes/${s.externalId}`;
}
export function label(s: NormalizedShowtimeLite, c: PersonalConfig): string {
  return new Date(s.startsAt).toLocaleString("en-US", {
    timeZone: c.theatre.timeZone, timeZoneName: "short", weekday: "short",
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
}
export function pendingFor(state: WatchState, email: string, now = new Date()): NormalizedShowtimeLite[] {
  const sent = new Set(state.delivered[recipientKey(email)] ?? []);
  return Object.entries(state.observed)
    .filter(([key, s]) => !sent.has(key) && new Date(s.startsAt) > now)
    .map(([, s]) => s).sort((a, b) => a.startsAt.localeCompare(b.startsAt));
}
export interface Message { subject: string; text: string; html: string }
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]!));
export function dropMessage(shows: NormalizedShowtimeLite[], c: PersonalConfig): Message {
  const subject = `${shows.length} new Odyssey IMAX 70mm showtime${shows.length === 1 ? "" : "s"} — Lincoln Square`;
  return {
    subject,
    text: `${c.theatre.name}\n\n${shows.map(s => `${label(s, c)} — ${s.format}\n${ticketUrl(s)}`).join("\n\n")}\n\nListing detected; available seats are not checked.`,
    html: `<h2>${escapeHtml(subject)}</h2><p>${escapeHtml(c.theatre.name)}</p><ul>${shows.map(s => `<li>${escapeHtml(label(s, c))} — ${escapeHtml(s.format)}: <a href="${ticketUrl(s)}">Buy tickets</a></li>`).join("")}</ul><p>Listing detected; available seats are not checked.</p>`,
  };
}
// Persist after each recipient's SMTP acknowledgement. A failed recipient stays
// pending while successes are retained. A crash between SMTP and persistence can
// cause a duplicate (at-least-once delivery); no false exactly-once promise.
export async function deliverPending(
  state: WatchState, recipients: string[], c: PersonalConfig,
  send: (to: string, message: Message) => Promise<void>,
  save: (state: WatchState) => Promise<void>, now = new Date(),
): Promise<{ sent: number; errors: number }> {
  let sent = 0, errors = 0;
  for (const email of [...new Set(recipients.map(s => s.trim().toLowerCase()).filter(Boolean))]) {
    const pending = pendingFor(state, email, now);
    if (!pending.length) continue;
    try { await send(email, dropMessage(pending, c)); }
    catch { errors++; continue; }
    const key = recipientKey(email);
    state.delivered[key] = [...new Set([...(state.delivered[key] ?? []), ...pending.map(s => showtimeKey(s, c))])];
    await save(state); // Persistence errors must stop the run, not look delivered.
    sent++;
  }
  return { sent, errors };
}
