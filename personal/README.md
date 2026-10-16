# Lincoln Square / The Odyssey — personal watcher

This fork's personal runner is `scraper/personalWatch.ts`. It reuses the original
AMC DOM extractor, but does not use the legacy Vercel, Postgres, Google sign-in,
Regal, subscription UI, or date-based email pipeline. Those upstream files remain
for reference; their setup guide describes a different execution path.

## Behavior

- Only AMC Lincoln Square 13, only The Odyssey, only a format containing BOTH
  IMAX and 70mm. No digital IMAX, plain 70mm, or other movies.
- Every date in the next 60 days is scanned, including dates after empty gaps.
  `personal/config.json` supports additional `targetDates` (`YYYY-MM-DD`) for
  advance engagements outside that rolling window. Nothing beyond these dates
  is checked. `scanDays` is configurable from 1 to 120.
- Browser date and email display use America/New_York, including daylight saving.
- Each new AMC showtime ID triggers an alert even on a previously reported date.
  One email groups all new showtimes for that recipient. Every screening has its
  own `https://www.amctheatres.com/showtimes/ID` ticket link.
- The first live scan reports all matching future showtimes. Subsequent scans
  skip IDs already delivered. It does not check seats, sold-out status, refunds,
  or cancellations; an existing ID with newly available seats does not alert.
- Each SMTP success is checkpointed per recipient. Failed recipients retry on
  the next successful scan without resending to recipients already checkpointed.
  A crash after SMTP acceptance but before a Git push can produce a duplicate:
  delivery is at least once, not exactly once. SMTP acceptance is not proof of
  inbox delivery.
- Queues, unexpected theaters, parsing failures, and unexplained empty pages
  fail the run. No "nothing available" conclusion is drawn from a failure.
  A failed scan does not advance the full-scan heartbeat.
- State is committed to `watcher-state`, separate from source. Emails and SMTP
  credentials are not stored in that public branch; delivery keys use SHA-256
  recipient hashes. Showtime history and timestamps are public.
- One concurrency group serializes scans and the watchdog, without cancelling
  in-flight deliveries. Scans run on a 15-minute target schedule; GitHub delays
  and scan duration add latency. The independent watchdog checks for a successful
  scan within 45 minutes, emails on failure/recovery, and marks failures in
  Actions. If GitHub Actions itself stops, neither workflow can alert; enable
  GitHub's workflow failure emails and keep the existing watcher until verified.

## Configuration and activation

Repository Actions secrets:

| Secret | Purpose |
| --- | --- |
| WATCHER_RECIPIENTS | Comma-separated alert recipients |
| GMAIL_USER | Gmail SMTP sender |
| GMAIL_APP_PASSWORD | Google app password for that sender, never its login password |

Create app passwords at https://myaccount.google.com/apppasswords with Google
2-Step Verification enabled. Set secrets in repository Settings → Secrets and
variables → Actions. Never put passwords in an issue, commit, or chat.

Repository Actions variable `WATCHER_ENABLED` defaults to false. Only set it to
`true` after all of these pass:

1. `Personal watcher tests`: unit tests, both type checks, and real Chromium
   fixture covering DOM extraction -> filtering -> added-showtime email.
2. Manual `Lincoln Square personal watcher`, mode `dry-run`: a complete 60-day
   scan from GitHub's runner. Inspect the report artifact and page failures.
3. With the user's explicit permission, manual mode `test-email`: confirm the
   labeled test arrives in the inbox and on the phone.
4. Manual mode `live`: verify full scan, state checkpoints, and real links. If
   the movie has no current listings, recorded fixtures prove logic only;
   real ticket-drop behavior remains to be confirmed when listings exist.
5. Enable schedule, observe scheduled success, then consider retiring the old
   watcher. This fork never changes or disables the prior ChatGPT watcher.

Use the source from the fork, not upstream. The inherited `scrape.yml` is a
manual-only legacy Vercel workflow in this fork, not the personal watcher; it
has no APP_URL/CRON_SECRET configured here and should not be used for personal monitoring.

## Local verification

```sh
npm ci
(cd scraper && npm ci && npx playwright install chromium)
npm test
npm run typecheck
(cd scraper && npm run typecheck && npx tsx personalSmoke.ts)
(cd scraper && npx tsx personalWatch.ts --dry-run)
```

`--dry-run --scan-days=1` is a short diagnostic only, not a full acceptance scan.
Dry runs do not send email or change history. `watcher-report.json` contains the
findings. The live runner refuses missing or corrupt state rather than silently
resetting dedupe. Keep the state branch and its Git history.

## Home-computer fallback

The live GitHub-hosted dry run on October 16, 2026 encountered AMC HTTP 403 /
bot challenges, including with the original scraper's browser identification.
Do not activate the hosted schedule on that evidence.

A home runner is supported but not installed or verified yet:

1. On an always-on Linux or macOS computer on your home connection, use this
   fork's Settings → Actions → Runners → New self-hosted runner. Follow GitHub's
   OS-specific installation steps. Give it the custom label `lincoln-square-home`.
2. Set the repository variable `WATCHER_RUNNER` to `lincoln-square-home`. Only the
   personal scanning workflow uses that runner. The independent heartbeat check
   stays on GitHub, so it can notice a home machine that stops scanning.
3. Keep `WATCHER_ENABLED=false`. Manually run mode `dry-run` and verify a complete
   scan. Then test a manual live scan and check the history branch.
4. Only after success, enable the schedule. Keep the computer awake and online.
   AMC may also challenge home connections; this fallback still requires testing.

The test suite remains on GitHub-hosted machines. Avoid running untrusted code
on the machine holding your runner and email credentials.
