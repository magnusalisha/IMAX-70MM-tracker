import { describe, expect, it, vi } from "vitest";
import configJson from "../personal/config.json";
import { emptyState, validateConfig, scanDates, isTarget, showtimeKey, recipientKey, label, pendingFor, deliverPending, dropMessage, parseState } from "../scraper/personalCore";
import { assertAmcPage } from "../scraper/personalAmc";
import type { NormalizedShowtimeLite } from "../scraper/types";
const config = validateConfig(configJson);
const now = new Date("2026-07-20T12:00:00Z");
const show = (id = "144500192", startsAt = "2026-07-21T23:00:00Z"): NormalizedShowtimeLite => ({
  externalId: id, startsAt, movieTitle: "The Odyssey", movieExternalId: "76238",
  format: "IMAX 70MM", is70mm: true, bookingUrl: "https://wrong.invalid/old-link",
});
describe("Lincoln Square personal watcher", () => {
  it("scans in Eastern calendar time across blank dates and beyond the rolling window", () => {
    const dates = scanDates({ ...config, scanDays: 3, targetDates: ["2026-12-18", "2026-07-20"] }, new Date("2026-07-21T05:00:00Z"));
    expect(dates).toEqual(["2026-07-21", "2026-07-22", "2026-07-23", "2026-12-18"]);
  });
  it("requires Odyssey AND IMAX AND film, excludes past shows", () => {
    expect(isTarget(show(), config, now)).toBe(true);
    for (const format of ["70mm", "IMAX at AMC", "Dolby Cinema at AMC", "Digital IMAX", "IMAX 170mm"]) {
      expect(isTarget({ ...show(), format }, config, now)).toBe(false);
    }
    expect(isTarget({ ...show(), movieTitle: "Another Odyssey", movieExternalId: "other" }, config, now)).toBe(false);
    expect(isTarget(show(), config, new Date("2026-08-01"))).toBe(false);
  });
  it("formats EDT/EST and late-night screenings correctly", () => {
    expect(label(show(), config)).toContain("7:00 PM EDT");
    expect(label(show("2", "2026-12-19T00:00:00Z"), config)).toContain("7:00 PM EST");
    expect(label(show("3", "2026-07-22T03:00:00Z"), config)).toContain("Jul 21");
    expect(label(show("4", "2026-07-22T07:00:00Z"), config)).toContain("3:00 AM EDT");
  });
  it("finds a new date and an added showtime on an already delivered date with its own link", async () => {
    const state = emptyState(); const email = "test@example.com";
    const old = show(), added = show("144500193", "2026-07-22T03:00:00Z"), nextDate = show("144500194", "2026-07-22T23:00:00Z");
    for (const s of [old, added, nextDate]) state.observed[showtimeKey(s, config)] = s;
    state.delivered[recipientKey(email)] = [showtimeKey(old, config)];
    const send = vi.fn().mockResolvedValue(undefined), save = vi.fn().mockResolvedValue(undefined);
    expect(await deliverPending(state, [email], config, send, save, now)).toEqual({ sent: 1, errors: 0 });
    const message = send.mock.calls[0][1];
    expect(message.text).toContain("/showtimes/144500193");
    expect(message.text).toContain("/showtimes/144500194");
    expect(message.text).not.toContain("/showtimes/144500192");
    expect(message.text).not.toContain("wrong.invalid");
    expect(pendingFor(state, email, now)).toEqual([]);
    await deliverPending(state, [email], config, send, save, now);
    expect(send).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledTimes(1);
  });
  it("retains failed email for retry without resending to successful recipients", async () => {
    const state = emptyState(); state.observed[showtimeKey(show(), config)] = show();
    const save = vi.fn().mockResolvedValue(undefined);
    const send = vi.fn(async (to: string) => { if (to === "fail@example.com") throw new Error("SMTP down"); });
    expect(await deliverPending(state, ["ok@example.com", "fail@example.com"], config, send, save, now)).toEqual({ sent: 1, errors: 1 });
    expect(pendingFor(state, "ok@example.com", now)).toHaveLength(0);
    expect(pendingFor(state, "fail@example.com", now)).toHaveLength(1);
    const retry = vi.fn().mockResolvedValue(undefined);
    await deliverPending(state, ["ok@example.com", "fail@example.com"], config, retry, save, now);
    expect(retry).toHaveBeenCalledTimes(1);
    expect(retry.mock.calls[0][0]).toBe("fail@example.com");
    expect(JSON.stringify(state)).not.toContain("@example.com");
  });
  it("fails visibly when checkpointing fails after SMTP", async () => {
    const state = emptyState(); state.observed[showtimeKey(show(), config)] = show();
    await expect(deliverPending(state, ["ok@example.com"], config, async () => {}, async () => { throw new Error("push failed"); }, now)).rejects.toThrow("push failed");
  });
  it("refuses queues, bot challenges, wrong theatres, and unexplained empty pages", () => {
    const url = config.theatre.showtimesUrl;
    expect(() => assertAmcPage("https://queue.amctheatres.com/", "Queue", "", 0)).toThrow();
    expect(() => assertAmcPage(url, "Just a moment", "AMC Lincoln Square 13", 0)).toThrow();
    expect(() => assertAmcPage(url, "Showtimes", "AMC Metreon 16", 4)).toThrow();
    expect(() => assertAmcPage(url, "Showtimes", "AMC Lincoln Square 13", 0)).toThrow();
    expect(() => assertAmcPage(url, "Showtimes", "AMC Lincoln Square 13: No showtimes available", 0)).not.toThrow();
  });
  it("does not silently reset corrupt history", () => {
    expect(() => parseState({ version: 0 })).toThrow();
    expect(parseState(JSON.parse(JSON.stringify(emptyState())))).toEqual(emptyState());
  });
  it("rejects malformed target dates and forged showtime ids", () => {
    expect(() => validateConfig({ ...config, targetDates: ["2026-02-30"] })).toThrow();
    expect(() => dropMessage([show('1" onclick="bad')], config)).toThrow();
  });
});
