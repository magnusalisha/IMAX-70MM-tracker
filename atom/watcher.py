"""Atom-only personal watcher. Python standard library; no default live mode.

Network effects are injected. A successful scan is persisted before any sends;
each provider acknowledgement is persisted separately. Never erase seen IDs.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = "https://www.atomtickets.com"
THEATRE = BASE + "/theaters/amc-lincoln-square-13/164"
PRODUCTION = "377232"
VENUE = "164"
FORMAT = "IMAX70MM"
SCOPE = {"production": PRODUCTION, "venue": VENUE, "format": FORMAT}
EASTERN = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


class InvalidData(ValueError):
    pass


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_http(url, data=None):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA, "Referer": THEATRE,
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=25) as response:
        # Do not accept redirects as evidence for the requested movie/date.
        if response.status != 200 or response.url != url:
            raise InvalidData("Unexpected HTTP response or redirect")
        body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise InvalidData("Oversized response")
        return body.decode("utf-8")


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.dates = set()
        self.preorders = set()
        self.shows = []
        self.current = None
        self.feed(body)
        self.close()
        if self.current:
            raise InvalidData("Truncated showtime anchor")
        self.visible = " ".join(" ".join(self.text).split())
        if re.search(r"just a moment|security verification|access denied|captcha", self.visible, re.I):
            raise InvalidData("Atom security challenge")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag != "a":
            return
        href = a.get("href", "")
        if a.get("data-value"):
            value = json.loads(a["data-value"])
            date = value.get("serverFormat")
            if date:
                self.dates.add(dt.date.fromisoformat(date))
        if href.startswith(f"/movies/{PRODUCTION}/showtimes?"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            if query.get("venueId") != [VENUE]:
                raise InvalidData("Unexpected preorder venue")
            self.preorders.add(dt.date.fromisoformat(query["localDate"][0][:10]))
        if href.startswith("/checkout/"):
            if self.current or not re.fullmatch(r"/checkout/[0-9]+", href):
                raise InvalidData("Malformed checkout link")
            meta = json.loads(a.get("data-vrs-showtime", "null"))
            if not isinstance(meta, dict) or str(meta.get("venue_id")) != VENUE or not meta.get("format"):
                raise InvalidData("Missing or incorrect showtime identity")
            self.current = {"id": href.rsplit("/", 1)[1], "format": meta["format"],
                            "label": "", "disabled": "disabled" in a.get("class", "")}

    def handle_data(self, value):
        self.text.append(value)
        if self.current is not None:
            self.current["label"] += value

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.current["label"] = " ".join(self.current["label"].split())
            self.shows.append(self.current)
            self.current = None


def date_heading(date):
    return f"Showtimes for {date.strftime('%A, %b')} {date.day}"


def parse_fragment(body, date):
    page = Page(body)
    # The endpoint supplies movie/venue scope; validate the exact returned date.
    heading = re.search(r"Showtimes for ([A-Za-z]+, [A-Za-z]+ \d{1,2})(?!\d)", page.visible)
    if not heading or heading.group(0) != date_heading(date):
        raise InvalidData("Missing or mismatched date heading")
    if not page.shows:
        # Explicit scoped empty result, NOT an arbitrary blank HTTP 200 page.
        if "COMING SOON" in page.visible and "Check back soon for more information." in page.visible:
            return []
        raise InvalidData("Unrecognized empty fragment")
    if "AMC Lincoln Square 13" not in page.visible:
        raise InvalidData("Missing theatre identity")
    result = []
    seen = set()
    for show in page.shows:
        if show["id"] in seen:
            raise InvalidData("Repeated ID in fragment")
        seen.add(show["id"])
        try:
            local_time = dt.datetime.strptime(show["label"], "%I:%M %p").time()
        except ValueError as exc:
            raise InvalidData("Unrecognized showtime clock") from exc
        if show["format"] != FORMAT:
            continue
        local = dt.datetime.combine(date, local_time, EASTERN)
        # Ambiguous/nonexistent DST clocks cannot safely identify an instant.
        if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
            raise InvalidData("Ambiguous DST clock")
        result.append({"id": show["id"], "date": date.isoformat(),
                       "startsAt": local.isoformat(), "format": FORMAT,
                       "url": BASE + "/checkout/" + show["id"] + "#/tickets"})
    return result


def collect(fetch=read_http, today=None, pause=time.sleep):
    today = today or dt.datetime.now(EASTERN).date()
    main = Page(fetch(THEATRE))
    if "AMC Lincoln Square 13" not in main.visible or not main.dates or not main.shows:
        raise InvalidData("Theatre calendar/listings missing")
    # Scan ALL advertised theatre dates, including distant presales, plus the
    # next 14 days and Odyssey-specific preorder links. Never rely on a horizon.
    dates = {today + dt.timedelta(days=i) for i in range(14)} | main.dates | main.preorders
    dates = sorted(date for date in dates if date >= today)
    if not dates or len(dates) > 400:
        raise InvalidData("Unexpected calendar size")
    shows = {}
    for date in dates:
        local = dt.datetime.combine(date, dt.time(2), EASTERN).isoformat()
        url = BASE + f"/movies/{PRODUCTION}/showtimes?" + urllib.parse.urlencode({
            "localDate": local, "venueId": VENUE})
        for show in parse_fragment(fetch(url), date):
            if show["id"] in shows and shows[show["id"]] != show:
                raise InvalidData("Showtime ID appeared with conflicting dates")
            shows[show["id"]] = show
        pause(0.25)
    return {"shows": sorted(shows.values(), key=lambda s: s["startsAt"]),
            "datesChecked": [date.isoformat() for date in dates]}


def initial_state():
    return {"version": 1, "scope": SCOPE.copy(), "baselineAt": None,
            "seen": {}, "outbox": [], "lastAttemptAt": None,
            "lastSuccessAt": None, "lastError": None, "lastHeartbeatAt": None}


def valid_show(show):
    if not isinstance(show, dict) or not re.fullmatch(r"[0-9]+", str(show.get("id", ""))):
        raise InvalidData("Invalid persisted showtime")
    local = dt.datetime.fromisoformat(show["startsAt"])
    if local.tzinfo is None or local.date().isoformat() != show["date"] or local.utcoffset() != local.astimezone(EASTERN).utcoffset():
        raise InvalidData("Invalid persisted Eastern datetime")
    if show.get("format") != FORMAT or show.get("url") != BASE + "/checkout/" + show["id"] + "#/tickets":
        raise InvalidData("Invalid persisted format/link")


def validate_state(state):
    if state.get("version") != 1 or state.get("scope") != SCOPE or not isinstance(state.get("seen"), dict) or not isinstance(state.get("outbox"), list):
        raise InvalidData("State schema/scope mismatch; refusing to reset history")
    for field in ("baselineAt", "lastAttemptAt", "lastSuccessAt", "lastHeartbeatAt"):
        if field not in state:
            raise InvalidData("Missing state field")
        if state[field] is not None and dt.datetime.fromisoformat(state[field]).tzinfo is None:
            raise InvalidData("Naive state timestamp")
    if state["baselineAt"] is None and (state["seen"] or state["outbox"] or state["lastSuccessAt"]):
        raise InvalidData("Uninitialized state with history")
    for key, show in state["seen"].items():
        valid_show(show)
        if key != show["id"]:
            raise InvalidData("State ID mismatch")
    batch_ids = set()
    queued = set()
    for batch in state["outbox"]:
        if batch["id"] in batch_ids or not batch["shows"] or batch["status"] not in ("pending", "sent") or not isinstance(batch["attempts"], int) or batch["attempts"] < 0:
            raise InvalidData("Invalid outbox")
        batch_ids.add(batch["id"])
        for show in batch["shows"]:
            valid_show(show)
            if show["id"] not in state["seen"] or show["id"] in queued:
                raise InvalidData("Invalid or repeated queued showtime")
            queued.add(show["id"])
        if batch["status"] == "sent" and not batch.get("providerRequest"):
            raise InvalidData("Missing provider acknowledgement")
    return state


def load_state(path):
    # Missing is an error. Initialization is an explicit operator action.
    return validate_state(json.loads(Path(path).read_text()))


def save_atomic(path, state):
    validate_state(state)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        name = handle.name
    os.replace(name, path)


def message(shows):
    lines = ["The Odyssey — AMC Lincoln Square 13 — IMAX 70mm"]
    for show in shows:
        local = dt.datetime.fromisoformat(show["startsAt"]).astimezone(EASTERN)
        lines.extend([local.strftime("%a %b %d, %Y • %I:%M %p %Z"), show["url"]])
    return "\n".join(lines)


def batches(shows):
    # One message per date when possible; split conservatively below 1024 UTF-8
    # bytes so every screening retains its own clickable link.
    current = []
    for show in sorted(shows, key=lambda s: (s["startsAt"], s["id"])):
        if current and (current[0]["date"] != show["date"] or len(message(current + [show]).encode()) > 950):
            yield current
            current = []
        current.append(show)
    if current:
        yield current


def record_scan(state, scan, now):
    next_state = copy.deepcopy(state)
    new = []
    for show in scan["shows"]:
        valid_show(show)
        prior = next_state["seen"].get(show["id"])
        if prior and prior != show:
            raise InvalidData("Previously seen ID changed identity")
        if prior is None:
            new.append(show)
        next_state["seen"][show["id"]] = show
    if next_state["baselineAt"] is None:
        next_state["baselineAt"] = now  # Baseline is completely silent.
    else:
        for group in batches(new):
            key = hashlib.sha256(",".join(sorted(s["id"] for s in group)).encode()).hexdigest()
            next_state["outbox"].append({"id": key, "shows": group,
                "status": "pending", "attempts": 0, "createdAt": now,
                "lastError": None, "providerRequest": None})
    next_state.update(lastAttemptAt=now, lastSuccessAt=now, lastError=None,
                      lastDatesChecked=scan["datesChecked"])
    return validate_state(next_state)


def pushover_send(body, http=read_http):
    payload = urllib.parse.urlencode({"token": os.environ["PUSHOVER_APP_TOKEN"],
        "user": os.environ["PUSHOVER_USER_KEY"], "title": "Odyssey IMAX 70mm — new screenings",
        "message": body, "priority": "0"}).encode()
    response = json.loads(http("https://api.pushover.net/1/messages.json", payload))
    if response.get("status") != 1 or not isinstance(response.get("request"), str) or not response["request"]:
        raise InvalidData("Push provider did not acknowledge message")
    return response["request"]


def health_ping(http=read_http):
    url = os.environ["HEALTHCHECKS_PING_URL"]
    # UUID-only exact host: never auto-provision a service/check accidentally.
    if not re.fullmatch(r"https://hc-ping\.com/[a-f0-9-]{36}", url):
        raise InvalidData("Invalid health-check URL")
    if http(url).strip() != "OK":
        raise InvalidData("Health check not acknowledged")


def safe_error(exc):
    # Network exceptions may embed secret URLs or tokens. Store class only.
    return type(exc).__name__


def run_cycle(state, scan, persist, send, heartbeat, now=None):
    now = now or stamp()
    source_ok = False
    try:
        next_state = record_scan(state, scan(), now)
        source_ok = True
    except Exception as exc:
        next_state = copy.deepcopy(state)
        next_state.update(lastAttemptAt=now, lastError="Atom scan: " + safe_error(exc))
    # persist is a DURABLE barrier (including remote git push) before sending.
    persist(next_state)
    for batch in next_state["outbox"]:
        if batch["status"] == "sent":
            continue
        batch["attempts"] += 1
        batch["lastAttemptAt"] = now
        persist(next_state)
        try:
            receipt = send(message(batch["shows"]))
            if not receipt:
                raise InvalidData("Missing acknowledgement")
        except Exception as exc:
            batch["lastError"] = safe_error(exc)
        else:
            batch.update(status="sent", providerRequest=receipt, sentAt=stamp(), lastError=None)
        persist(next_state)
    healthy = source_ok and not any(b["status"] == "pending" for b in next_state["outbox"])
    if healthy:
        # Only a complete scan + drained durable outbox refresh the independent
        # watchdog. Failed checks/retries cannot mask a stale source.
        try:
            heartbeat()
        except Exception as exc:
            next_state["lastError"] = "Heartbeat: " + safe_error(exc)
            persist(next_state)
            return next_state, False
        next_state["lastHeartbeatAt"] = stamp()
        persist(next_state)
    return next_state, healthy


class GitStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "atom-state.json"

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.directory), *args],
                              check=True, capture_output=True, text=True).stdout

    def save(self, state):
        save_atomic(self.path, state)
        self.git("add", "atom-state.json")
        if self.git("diff", "--cached", "--name-only").strip():
            self.git("-c", "user.name=Odyssey watcher", "-c", "user.email=watcher@users.noreply.github.com",
                     "commit", "-m", "Persist Atom watcher checkpoint")
            # A rejected push aborts all subsequent external effects.
            self.git("push", "origin", "HEAD:atom-state")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["probe", "initialize", "live"])
    parser.add_argument("--state-dir", default="atom-state")
    args = parser.parse_args()
    if args.mode == "probe":
        scan = collect()
        print(json.dumps({"mode": "read-only", "scope": SCOPE, **scan}, indent=2))
        return
    # CLI and workflow both enforce explicit activation. No notifications or
    # health pings from probe/tests. Credentials are not inspected by probe.
    if os.environ.get("WATCHER_ENABLED") != "true":
        raise InvalidData("Live monitoring remains disabled")
    for key in ("PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY", "HEALTHCHECKS_PING_URL"):
        if not os.environ.get(key):
            raise InvalidData("Notification/health configuration missing")
    store = GitStore(args.state_dir)
    if args.mode == "initialize":
        if store.path.exists():
            raise InvalidData("Refusing to replace existing state")
        store.save(initial_state())
        return
    _, healthy = run_cycle(load_state(store.path), collect, store.save, pushover_send, health_ping)
    if not healthy:
        raise SystemExit("Watcher unhealthy; state retained; watchdog not refreshed")


if __name__ == "__main__":
    main()
