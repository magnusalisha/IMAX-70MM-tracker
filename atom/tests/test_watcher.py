import copy
import datetime as dt
import html
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

import watcher as w

NOW = "2026-10-16T15:00:00+00:00"
FIXTURES = Path(__file__).parent / "fixtures"


def show(key="1", date="2026-10-17", clock="18:30"):
    local = dt.datetime.fromisoformat(date + "T" + clock).replace(tzinfo=w.EASTERN)
    return {"id": key, "date": date, "startsAt": local.isoformat(), "format": w.FORMAT,
            "url": w.BASE + "/checkout/" + key + "#/tickets"}


def scan(*shows):
    return {"shows": list(shows), "datesChecked": sorted({s["date"] for s in shows})}


def fragment(date, ids=("1",), fmt="IMAX70MM", venue="164", clock="6:30 PM"):
    anchors = "".join(f'<a href="/checkout/{key}" data-vrs-showtime=\'{json.dumps({"venue_id": venue, "format": fmt})}\'>{clock}</a>' for key in ids)
    return f'<h2>{w.date_heading(date)}</h2><h2>AMC Lincoln Square 13</h2>{anchors}'


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.sent = []
        self.pings = []

    def cycle(self, state, value, send=None, persist=None, heartbeat=None):
        return w.run_cycle(state, lambda: value, persist or (lambda s: self.saved.append(copy.deepcopy(s))),
                           send or (lambda body: self.sent.append(body) or "accepted-123"),
                           heartbeat or (lambda: self.pings.append(True)), now=NOW)

    def test_silent_baseline_and_restart_persisted_ids(self):
        state, ok = self.cycle(w.initial_state(), scan(show()))
        self.assertTrue(ok)
        self.assertEqual(self.sent, [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            w.save_atomic(path, state)
            restarted = w.load_state(path)
        state, ok = self.cycle(restarted, scan(show()))
        self.assertEqual(self.sent, [])
        self.assertEqual(len(state["seen"]), 1)

    def test_new_date_and_additional_showtime_on_existing_date(self):
        base, _ = self.cycle(w.initial_state(), scan(show()))
        extra = show("2", clock="21:30")
        new_date = show("3", date="2026-11-08")
        state, ok = self.cycle(base, scan(show(), extra, new_date))
        self.assertTrue(ok)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("09:30 PM EDT", self.sent[0])
        self.assertIn(extra["url"], self.sent[0])
        self.assertIn("06:30 PM EST", self.sent[1])
        self.assertNotIn(show()["url"], "\n".join(self.sent))
        self.cycle(state, scan(show(), extra, new_date))
        self.assertEqual(len(self.sent), 2)

    def test_grouping_and_message_limits(self):
        groups = list(w.batches([show(str(i)) for i in range(1, 31)]))
        self.assertGreater(len(groups), 1)
        self.assertEqual(sum(map(len, groups)), 30)
        for group in groups:
            self.assertLessEqual(len(w.message(group).encode()), 950)
            for s in group:
                self.assertIn(s["url"], w.message(group))

    def test_failed_send_is_durable_and_retried_after_restart(self):
        base, _ = self.cycle(w.initial_state(), scan())
        def reject(_):
            raise TimeoutError("private endpoint must not be recorded")
        state, healthy = self.cycle(base, scan(show()), send=reject)
        self.assertFalse(healthy)
        self.assertEqual(state["outbox"][0]["status"], "pending")
        self.assertEqual(state["outbox"][0]["attempts"], 1)
        self.assertEqual(state["outbox"][0]["lastError"], "TimeoutError")
        pings_before = len(self.pings)
        state, healthy = self.cycle(json.loads(json.dumps(state)), scan(show()))
        self.assertTrue(healthy)
        self.assertEqual(state["outbox"][0]["status"], "sent")
        self.assertEqual(state["outbox"][0]["attempts"], 2)
        self.assertEqual(len(self.pings), pings_before + 1)

    def test_persist_before_send_and_after_ack(self):
        base, _ = self.cycle(w.initial_state(), scan())
        events = []
        def save(state):
            events.append(state["outbox"][0]["status"])
        def send(body):
            self.assertEqual(events, ["pending", "pending"])
            events.append("send")
            return "receipt"
        self.cycle(base, scan(show()), persist=save, send=send)
        self.assertEqual(events[:4], ["pending", "pending", "send", "sent"])

    def test_storage_failure_prevents_delivery_and_heartbeat(self):
        base, _ = self.cycle(w.initial_state(), scan())
        count = len(self.pings)
        def fail(_):
            raise OSError("push rejected")
        with self.assertRaises(OSError):
            self.cycle(base, scan(show()), persist=fail)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.pings), count)

    def test_partial_source_failure_preserves_history_and_success(self):
        base, _ = self.cycle(w.initial_state(), scan(show()))
        base["lastSuccessAt"] = "2026-10-16T14:00:00+00:00"
        base["lastHeartbeatAt"] = "2026-10-16T14:00:00+00:00"
        def broken():
            raise w.InvalidData("second date malformed")
        state, healthy = w.run_cycle(base, broken, lambda s: self.saved.append(copy.deepcopy(s)),
                                    lambda _: self.fail("unexpected send"), lambda: self.fail("unexpected heartbeat"), NOW)
        self.assertFalse(healthy)
        self.assertEqual(state["seen"], base["seen"])
        self.assertEqual(state["lastSuccessAt"], base["lastSuccessAt"])
        self.assertEqual(state["lastHeartbeatAt"], base["lastHeartbeatAt"])
        self.assertEqual(state["lastAttemptAt"], NOW)

    def test_missing_screenings_never_erase_history_or_realert(self):
        base, _ = self.cycle(w.initial_state(), scan(show()))
        empty, _ = self.cycle(base, scan())
        self.assertEqual(empty["seen"], base["seen"])
        self.cycle(empty, scan(show()))
        self.assertEqual(self.sent, [])

    def test_conflicting_stable_id_fails_without_advancing_success(self):
        base, _ = self.cycle(w.initial_state(), scan(show()))
        state, healthy = self.cycle(base, scan(show(clock="19:30")))
        self.assertFalse(healthy)
        self.assertEqual(state["seen"], base["seen"])

    def test_heartbeat_failure_is_visible(self):
        def fail():
            raise ConnectionError("secret URL")
        state, healthy = self.cycle(w.initial_state(), scan(), heartbeat=fail)
        self.assertFalse(healthy)
        self.assertIsNone(state["lastHeartbeatAt"])
        self.assertEqual(state["lastError"], "Heartbeat: ConnectionError")

    def test_corrupt_or_missing_state_never_rebaselines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with self.assertRaises(FileNotFoundError):
                w.load_state(path)
            for invalid in ("{bad", '{}', json.dumps({**w.initial_state(), "scope": {}}),
                            json.dumps({**w.initial_state(), "seen": {"1": show()}})):
                path.write_text(invalid)
                with self.assertRaises((ValueError, KeyError)):
                    w.load_state(path)


class ParserTests(unittest.TestCase):
    def test_login_captcha_script_is_not_a_visible_challenge(self):
        page = w.Page('<h1>AMC Lincoln Square 13</h1><script>var captchaInit = function() {};</script><style>.captcha {}</style>')
        self.assertEqual(page.visible, "AMC Lincoln Square 13")

    def test_real_standard_odyssey_not_imax(self):
        self.assertEqual(w.parse_fragment((FIXTURES / "odyssey-standard.html").read_text(), dt.date(2026, 10, 17)), [])

    def test_explicit_coming_soon_is_scoped_empty(self):
        body = (FIXTURES / "coming-soon.html").read_text()
        self.assertEqual(w.parse_fragment(body, dt.date(2026, 10, 25)), [])
        with self.assertRaises(w.InvalidData):
            w.parse_fragment(body, dt.date(2026, 10, 26))

    def test_exact_format_venue_id_date_clock_and_link(self):
        date = dt.date(2026, 10, 17)
        self.assertEqual(w.parse_fragment(fragment(date), date), [show()])
        for fmt in ("70MM", "IMAXLASERAMC", "IMAX", "LASERATAMC"):
            self.assertEqual(w.parse_fragment(fragment(date, fmt=fmt), date), [])
        for body in (fragment(date, venue="165"), fragment(date, clock="bad"), fragment(date).replace("2026", "2027") + "security verification", "", "<h2>Just a moment...</h2>"):
            with self.assertRaises((w.InvalidData, ValueError)):
                w.parse_fragment(body, date)

    def test_dst_ambiguity_is_not_guessed(self):
        date = dt.date(2026, 11, 1)
        with self.assertRaises(w.InvalidData):
            w.parse_fragment(fragment(date, clock="1:30 AM"), date)

    def test_collect_discovers_far_calendar_date_and_odyssey_preorder(self):
        today = dt.date(2026, 10, 16)
        calls = []
        dates = ["2026-12-18", "2027-01-02"]
        main = '<h1>AMC Lincoln Square 13</h1>' + fragment(today)
        main += '<a data-value=\'' + json.dumps({"serverFormat": dates[0]}) + '\'>date</a>'
        main += f'<a href="/movies/377232/showtimes?venueId=164&amp;localDate={dates[1]}T02:00:00-05:00">preorder</a>'
        def fetch(url):
            calls.append(url)
            if url == w.THEATRE:
                return main
            date = dt.date.fromisoformat(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["localDate"][0][:10])
            return fragment(date, ids=(date.strftime("%Y%m%d"),))
        result = w.collect(fetch, today, pause=lambda _: None)
        self.assertEqual(len(result["datesChecked"]), 16)
        self.assertIn(dates[0], result["datesChecked"])
        self.assertIn(dates[1], result["datesChecked"])
        self.assertIn("-05%3A00", calls[-1])

    def test_partial_collection_never_returns_partial_snapshot(self):
        today = dt.date(2026, 10, 16)
        main = '<h1>AMC Lincoln Square 13</h1>' + fragment(today)
        main += '<a data-value=\'{"serverFormat":"2026-10-16"}\'>date</a>'
        def fetch(url):
            if url == w.THEATRE:
                return main
            if "2026-10-16" in url:
                return fragment(today)
            raise TimeoutError()
        with self.assertRaises(TimeoutError):
            w.collect(fetch, today, pause=lambda _: None)


class TransportTests(unittest.TestCase):
    def test_git_store_push_is_required_before_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, checkout = root / "remote.git", root / "checkout"
            def git(*args):
                return subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True).stdout
            git("init", "--bare", remote)
            git("init", "-b", "atom-state", checkout)
            git("-C", checkout, "remote", "add", "origin", remote)
            store = w.GitStore(checkout)
            store.save(w.initial_state())
            state = json.loads(git("--git-dir", remote, "show", "atom-state:atom-state.json"))
            self.assertEqual(state, w.initial_state())
            hook = remote / "hooks" / "pre-receive"
            hook.write_text("#!/bin/sh\nexit 1\n")
            hook.chmod(0o755)
            with self.assertRaises(subprocess.CalledProcessError):
                w.run_cycle(state, lambda: scan(show()), store.save,
                            lambda _: self.fail("send after rejected push"),
                            lambda: self.fail("ping after rejected push"), NOW)
            self.assertIsNone(json.loads(git("--git-dir", remote, "show", "atom-state:atom-state.json"))["baselineAt"])

    @patch.dict(os.environ, {"PUSHOVER_APP_TOKEN": "fake", "PUSHOVER_USER_KEY": "fake"})
    def test_provider_requires_explicit_ack_and_clickable_links(self):
        def http(url, data):
            self.assertEqual(url, "https://api.pushover.net/1/messages.json")
            payload = urllib.parse.parse_qs(data.decode())
            self.assertIn(show()["url"], payload["message"][0])
            self.assertEqual(payload["priority"], ["0"])
            return '{"status":1,"request":"abc"}'
        self.assertEqual(w.pushover_send(w.message([show()]), http), "abc")
        for response in ('{"status":0}', '{"status":1}', 'not json'):
            with self.assertRaises(ValueError):
                w.pushover_send("body", lambda *args: response)

    def test_health_transport_exact_url_no_autoprovision(self):
        url = "https://hc-ping.com/00000000-0000-0000-0000-000000000000"
        with patch.dict(os.environ, {"HEALTHCHECKS_PING_URL": url}):
            calls = []
            w.health_ping(lambda u: calls.append(u) or "OK\n")
            self.assertEqual(calls, [url])
            with self.assertRaises(w.InvalidData):
                w.health_ping(lambda _: "rejected")
        with patch.dict(os.environ, {"HEALTHCHECKS_PING_URL": "https://example.com/token"}):
            with self.assertRaises(w.InvalidData):
                w.health_ping(lambda _: self.fail("must not call"))

    def test_live_cli_is_disabled_without_flag(self):
        with patch.dict(os.environ, {"WATCHER_ENABLED": "false"}), patch("sys.argv", ["watcher", "live"]):
            with self.assertRaises(w.InvalidData):
                w.main()


if __name__ == "__main__":
    unittest.main()
