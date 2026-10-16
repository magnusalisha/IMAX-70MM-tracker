# Personal Odyssey watcher — inactive implementation

Scope is fixed to Atom production **377232**, venue **164**, exact **IMAX70MM**.
No dependency installation, browser, server, database, dashboard or Docker is
required. Runs on GitHub-hosted Ubuntu with Python's standard library.

## Current status and activation boundaries

This branch has **no monitoring schedule**. `WATCHER_ENABLED` remains `false`.
No Pushover account/application, Healthchecks check/integration, service secret,
or live Atom state branch has been configured. Tests inject fake transports.
The existing ChatGPT hourly backup is untouched. Retired AMC/email workflows
are disabled in this proposed branch so a later flag change cannot run both.
Main is unchanged until the user approves the reviewed replacement.

`atom-tests.yml` runs simulated tests and a real, **read-only** cloud probe on
branch pushes. It has no notification or health-service credentials and writes
only a CI artifact. `atom-watch.yml` offers a read-only manual probe; its live
job requires an additional explicit activation flag and initialized state.

## Source validation, October 16, 2026

[Bounded cloud observation](https://github.com/magnusalisha/IMAX-70MM-tracker/actions/runs/38903357932):
12 rounds at approximately 10-minute starts, **110.28 minutes** from first to
last sample (not a full 120-minute elapsed observation).

* Theater page: 12/12 successful, HTTP 200; its current-day listing count changed
  as past performances dropped away (61 down to 49), without resetting history.
* Odyssey October 16–20: each of five dates 12/12 successful, two standard laser
  screenings each. These correctly do **not** match IMAX70MM.
* Odyssey October 21–29: all 108 requests returned HTTP 200. The conservative
  diagnostic reported them as unconfirmed, not successful empty results. A
  separately inspected response is Atom's date-specific **COMING SOON / Check
  back soon for more information** modal. The implementation only accepts that
  explicit empty shape with a matching heading and verified theater calendar;
  it rejects blank, challenged, malformed or date-mismatched responses. A full
  cloud probe using this stricter parser is part of implementation validation.
* Dune Part Three December 18: 12/12 successful, six explicit IMAX70MM IDs. Dune
  is a positive format control only; the watcher never monitors it.
* Fandango was blocked in every round; AMC likewise unavailable. Neither is a
  fallback silently interpreted as empty data.

No newly added screenings appeared. **Feed publication delay is unmeasured**;
first-seen timestamps and HTTP Date do not establish latency relative to AMC.
No live Odyssey IMAX70mm screening was available for an end-to-end positive
movie/format test. Fixture tests cover that combination explicitly.

[Cloud checkout diagnostic](https://github.com/magnusalisha/IMAX-70MM-tracker/actions/runs/38903574974)
was security-gated despite successful plain-HTTP showtime access. A normal
browser verified `/checkout/642708403#/tickets`: Dune, December 18 at 6 AM,
Lincoln Square, Screen 13, IMAX70MM. `/checkout/649836334#/tickets` verified
Odyssey October 16 at 2:30 PM, Screen 6, **standard laser/open captions**.
No quantities/seats were selected. iPhone link behavior remains to be tested.

## Detection and state safety

Each check fetches the theater calendar, then the Odyssey fragment for **all
advertised future dates**, Odyssey preorder dates, and the next 14 days. This
covers distant presales and extra showtimes on existing dates. It does not claim
to discover dates Atom itself has not exposed. Every fragment must validate;
one failed response aborts the entire scan. No partial baseline is established.
Explicit empty responses never delete past IDs. Invalid or missing state is an
error, never an invitation to create a new baseline.

The first complete scan records existing matching IDs silently. Later unseen
IDs create grouped outbox messages, split by date and conservative byte size.
Messages contain each exact Atom purchase URL and America/New_York date/time,
including EDT/EST. Previously known IDs are retained indefinitely. A changed
identity under an existing ID is treated as an error for investigation rather
than sending an incorrect link or silently overwriting history.

`atom-state.json` on a dedicated future `atom-state` Git branch records baseline,
seen IDs, last attempt, last fully successful check, date coverage, pending/sent
messages, attempts, provider acknowledgement and last successful heartbeat.
Each state update uses atomic local replacement and a required remote Git push.
Detection is committed **before** delivery; attempts and acknowledgements are
committed separately. A rejected push stops external effects. Pending deliveries
can still retry during a source outage, but cannot refresh the health check.

Delivery is **at least once**, not exactly once: if Pushover accepts a message but
the response is lost, or the runner dies before acknowledgement is committed,
retrying can duplicate that message. Normal successful runs/restarts deduplicate
by persistent showtime ID. We prefer this narrow duplicate possibility to lost
alerts. Provider acceptance is not proof the iPhone displayed the alert.

## Proposed notification and independent health services — approval needed

* [Pushover](https://pushover.net/pricing): $4.99 USD one-time iOS purchase, with
  30-day trial. Individual app registration is free; current API allowance is
  10,000 messages/month. App on iPhone, notification permission, user key and
  application token required. Proposed repository secrets: `PUSHOVER_USER_KEY`
  and `PUSHOVER_APP_TOKEN`. Do not paste credentials in chat or commit them.
  Standard-priority messages, no repeated emergency alarms.
* [Healthchecks.io](https://healthchecks.io/pricing/): Hobbyist $0/month, up to
  20 checks. Proposed one simple check: period 10 minutes, grace 20 minutes;
  alerts after roughly 30 minutes without an acknowledged healthy execution.
  Configure Pushover plus email as independent notification routes. Proposed
  secret: `HEALTHCHECKS_PING_URL`. Its server detects silence even if GitHub
  never starts another run. A successful source scan with failed delivery does
  not refresh it. No check is created or pinged by tests/probes.

GitHub's proposed 10-minute schedule is **best effort**, with possible delayed
or dropped jobs; a watchdog detects failure but does not fix cadence. Before
activation, run an explicitly approved finite scheduled **read-only** trial
over several hours, record actual starts/gaps, and evaluate against 10–15 minutes.
If it misses that requirement, use a managed cloud scheduler after reviewing
its cost/access rather than pretending GitHub meets it. No home runner needed.
Read-only probe success does not validate production state pushes or iPhone
delivery; those are separate final tests.

## Tests and remaining approval stages

CI runs `PYTHONPATH=atom python -m unittest discover -s atom/tests -v`.
Tests cover silent baseline; new dates; extra same-date screenings; stored IDs
after restart; grouping, Eastern DST and exact links; failed/rejected delivery
and retry; pre-send durable barrier; missing/corrupt state; partial source failure;
no deletion/realert on disappearance; conflicting IDs; stale heartbeat behavior;
exact format/venue/date; distant calendar discovery and explicit empty responses.

1. Review inactive implementation and passing cloud tests/probe.
2. Ask user approval **before** service account/check/integration/secret setup or
   any test notification. Then test a clearly labeled push on the actual iPhone,
   click ticket links, reject/retry a fake alert, and test an independent stale
   heartbeat alert/recovery. Use isolated test state; no real screening alerts.
3. With explicit testing approval, run a bounded read-only schedule trial and a
   remote Git persistence test on disposable test state. Do not enable the live
   watcher or initialize production baseline as a side effect of testing.
4. Report evidence and obtain **separate live monitoring approval**. Only then
   prepare the `atom-state` branch, run explicit initialization once, merge the
   reviewed code, add the schedule, and set `WATCHER_ENABLED=true`. The first
   complete scan establishes the silent baseline; retain the ChatGPT backup.

Never manually reset/delete production state to fix an error. Restore a known
good commit or investigate; resetting a baseline could suppress genuine drops.

Official API references: [Pushover response/limits](https://pushover.net/api),
[Healthchecks ping and grace semantics](https://healthchecks.io/docs/),
[GitHub schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
