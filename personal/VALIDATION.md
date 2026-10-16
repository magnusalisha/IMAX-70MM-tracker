# Validation record — October 16, 2026

Implemented in the personal GitHub Actions path, not the inherited Vercel app:
Lincoln Square only; The Odyssey only; strict IMAX 70mm; America/New_York;
new showtime IDs on existing/new dates; individual direct ticket links;
per-recipient retry checkpoints; queue/parser failure handling; full-window
scans plus explicit target dates; independent stale-scan watchdog.

- Local: 156 tests pass; app and scraper type checks pass.
- GitHub: tests and both type checks pass. Real Chromium fixture exercises
  AMC DOM extraction → strict filter → added-showtime digest, time and ticket URL.
  https://github.com/magnusalisha/IMAX-70MM-tracker/actions/runs/38899781538
- GitHub: inherited test workflow fixed to install scraper dependencies before
  tests; passes.
  https://github.com/magnusalisha/IMAX-70MM-tracker/actions/runs/38899781652
- Live hosted dry run: FAIL, AMC HTTP 403. Retried with upstream browser identity
  and bounded waiting: FAIL, queue/challenge detected. No email or history change
  in either dry run. This proves failure detection, not ticket availability.
  https://github.com/magnusalisha/IMAX-70MM-tracker/actions/runs/38899781363
- Authorized test email: SMTP accepted on October 16, 2026; test workflow passed.
  Inbox receipt awaits recipient confirmation.
  https://github.com/magnusalisha/IMAX-70MM-tracker/actions/runs/38899848316
- Scraper runtime dependency audit: zero reported vulnerabilities after updating
  its Nodemailer dependency. The legacy web app is not deployed by this setup.
- Personal schedule: `WATCHER_ENABLED=false`. Original ChatGPT watcher untouched.
- Remaining acceptance: successful complete scan from an allowed host; inbox
  receipt; live state persistence on the actual host; scheduled-run observation;
  actual ticket-link behavior when matching listings exist. Home runner fallback
  is documented and selectable, but has not been installed or tested.

No claim of live monitoring or successful replacement is made.
