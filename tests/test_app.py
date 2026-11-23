"""Tests for Sanakh. Run with:  python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["START_CHECKER"] = "0"                         # no background thread in tests
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "import.db")

try:
    import flask  # noqa: F401
except ImportError:   # the web tests need the packages from requirements.txt
    raise ImportError("Flask is not installed. Run first:  pip install -r requirements.txt  "
                      "(the algorithm tests in test_rules.py run without it)") from None
from data import database  # noqa: E402
from services import alerts  # noqa: E402
from app import create_app  # noqa: E402

PIN = {"pin": "123456", "pin2": "123456"}
TELEGRAM_ON = mock.patch.object(alerts, "telegram_configured", return_value=True)


class SanakhTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "DATABASE": os.path.join(self.tmp.name, "test.db"),
            "START_CHECKER": False,
            "GRACE_MINUTES": 30,
            "EARLY_MINUTES": 30,
            "GAP_RATIO": 0.5,
            "TESTING": True,
        })
        self.client = self.app.test_client()
        self.clock = datetime(2026, 11, 21, 7, 0)
        patcher = mock.patch.object(database, "now", lambda tz_name=None: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

        res = self.client.post("/create", data={"name": "Grandma", "caregiver_name": "Saraa", **PIN})
        self.code = res.headers["Location"].rsplit("/", 1)[-1]
        self.client.post(f"/c/{self.code}/medicines",
                         data={"name": "Metformin", "dose": "500 mg", "times": "8:00, 20:00"})

    def today(self):
        return self.client.get(f"/api/e/{self.code}/today").get_json()["doses"]

    def take(self, dose_id):
        return self.client.post(f"/api/e/{self.code}/doses/{dose_id}/take").get_json()

    # ------------------------------------------------------------------ tests
    def test_parse_times(self):
        self.assertEqual(database.parse_times("20:00, 8:00;8:00"), ["08:00", "20:00"])
        with self.assertRaises(ValueError):
            database.parse_times("8am")
        with self.assertRaises(ValueError):
            database.parse_times("  ")

    def test_schedule_created_for_today(self):
        doses = self.today()
        self.assertEqual([d["time"] for d in doses], ["08:00", "20:00"])
        self.assertTrue(all(d["status"] == "upcoming" for d in doses))

    def test_medicine_added_later_skips_past_times(self):
        self.clock = datetime(2026, 11, 21, 15, 0)
        self.client.post(f"/c/{self.code}/medicines", data={"name": "Aspirin", "times": "09:00, 21:00"})
        aspirin = [d for d in self.today() if d["name"] == "Aspirin"]
        self.assertEqual([d["time"] for d in aspirin], ["21:00"])

    def test_take_then_double_dose_warning(self):
        morning = self.today()[0]
        self.clock = datetime(2026, 11, 21, 8, 2)
        first = self.take(morning["id"])
        self.assertEqual(first["result"], "ok")
        self.assertEqual(first["dose"]["taken_at"], "08:02")

        self.clock = datetime(2026, 11, 21, 9, 15)
        second = self.take(morning["id"])
        self.assertEqual(second["result"], "duplicate")
        self.assertEqual(second["dose"]["taken_at"], "08:02")

    def test_too_close_blocks_next_dose(self):
        self.client.post(f"/c/{self.code}/medicines", data={"name": "Paracetamol", "times": "08:00, 12:00"})
        doses = [d for d in self.today() if d["name"] == "Paracetamol"]
        self.clock = datetime(2026, 11, 21, 11, 20)
        self.assertEqual(self.take(doses[0]["id"])["result"], "ok")      # late morning dose
        self.clock = datetime(2026, 11, 21, 12, 0)
        with TELEGRAM_ON, mock.patch.object(alerts, "send_telegram", return_value=True) as send:
            res = self.take(doses[1]["id"])
        self.assertEqual(res["result"], "too_close")                    # gap 2 h -> allowed after 13:20
        self.assertEqual(res["allowed_after"], "13:20")
        self.assertIn("too soon", send.call_args[0][1])

    def test_simulate_api(self):
        res = self.client.post("/api/simulate", json={
            "medicines": [{"name": "Metformin", "times": "08:00, 20:00"}],
            "attempts": [{"mi": 0, "dose_time": "08:00", "at": "08:02"},
                         {"mi": 0, "dose_time": "08:00", "at": "09:00"}],
            "now": "10:00", "settings": {"early_minutes": 30, "grace_minutes": 30, "gap_ratio": 0.5}})
        self.assertEqual([a["result"] for a in res.get_json()["attempts"]], ["ok", "duplicate"])
        bad = self.client.post("/api/simulate", json={"medicines": [{"name": "X", "times": "8am"}]})
        self.assertEqual(bad.status_code, 400)

    def test_too_early(self):
        evening = self.today()[1]
        self.clock = datetime(2026, 11, 21, 12, 0)
        self.assertEqual(self.take(evening["id"])["result"], "too_early")

    def test_missed_dose_alerts_once(self):
        with self.app.app_context():
            self.clock = datetime(2026, 11, 21, 8, 20)   # still within grace period
            self.assertEqual(database.find_new_missed(), [])

            self.clock = datetime(2026, 11, 21, 8, 31)   # grace period over
            with TELEGRAM_ON, mock.patch.object(alerts, "send_telegram", return_value=True) as send:
                missed = alerts.run_check()
                self.assertEqual(alerts.run_check(), [])        # not alerted twice
            self.assertEqual(len(missed), 1)
            self.assertEqual(missed[0]["outcome"], "sent")
            self.assertIn("Grandma has not confirmed Metformin", send.call_args[0][1])

        statuses = [d["status"] for d in self.today()]
        self.assertEqual(statuses, ["missed", "upcoming"])

    def test_late_take_notifies_caregiver(self):
        morning = self.today()[0]
        with self.app.app_context():
            self.clock = datetime(2026, 11, 21, 9, 0)
            with TELEGRAM_ON, mock.patch.object(alerts, "send_telegram", return_value=True):
                alerts.run_check()
        with TELEGRAM_ON, mock.patch.object(alerts, "send_telegram", return_value=True) as send:
            result = self.take(morning["id"])
        self.assertEqual(result["result"], "ok")
        self.assertTrue(result["dose"]["late"])
        self.assertIn("took Metformin at 09:00", send.call_args[0][1])

    def test_removed_medicine_stops_future_doses(self):
        with self.app.app_context():
            med = database.list_medicines(database.get_elder(self.code)["id"])[0]
        self.client.post(f"/c/{self.code}/medicines/{med['id']}/remove")
        self.assertEqual(self.today(), [])

    def test_pages_render(self):
        for path in ["/", "/simulator", f"/c/{self.code}", f"/e/{self.code}", f"/e/{self.code}/manifest.webmanifest", "/sw.js"]:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get("/e/NOPE99").status_code, 404)

    def test_other_profile_cannot_take_dose(self):
        dose_id = self.today()[0]["id"]
        other = self.client.post("/create", data={"name": "Other", **PIN}).headers["Location"].rsplit("/", 1)[-1]
        res = self.client.post(f"/api/e/{other}/doses/{dose_id}/take")
        self.assertEqual(res.status_code, 404)

    def test_timezone_saved_and_validated(self):
        code = self.client.post("/create", data={"name": "Teacher", "timezone": "Europe/Berlin", **PIN}).headers["Location"].rsplit("/", 1)[-1]
        with self.app.app_context():
            self.assertEqual(database.timezone_of(database.get_elder(code)), "Europe/Berlin")
        bad = self.client.post("/create", data={"name": "X", "timezone": "Mars/Base", **PIN}).headers["Location"].rsplit("/", 1)[-1]
        with self.app.app_context():
            self.assertEqual(database.timezone_of(database.get_elder(bad)), "Asia/Ulaanbaatar")   # falls back to default
        self.client.post(f"/c/{code}/settings", data={"name": "Teacher", "timezone": "Nope/Zone"})
        with self.app.app_context():
            self.assertEqual(database.timezone_of(database.get_elder(code)), "Europe/Berlin")   # invalid change rejected
        self.assertEqual(self.client.get(f"/api/e/{code}/today").get_json()["timezone"], "Europe/Berlin")

    def test_month_log_counts_and_events(self):
        doses = self.today()
        self.clock = datetime(2026, 11, 21, 8, 5)
        self.take(doses[0]["id"])                              # taken on time
        self.clock = datetime(2026, 11, 21, 9, 0)
        self.take(doses[0]["id"])                              # double dose attempt -> logged
        self.clock = datetime(2026, 11, 21, 21, 0)             # evening dose now missed
        with self.app.app_context():
            database.find_new_missed()
            log = database.month_log(database.get_elder(self.code)["id"], 2026, 11)
        day = next(d for d in log["days"] if d["day"] == "2026-11-21")
        self.assertEqual((day["taken"], day["total"], day["missed"]), (1, 2, 1))
        # the blocked double dose also records that no alert could go out (Telegram not set up here)
        self.assertEqual([e["kind"] for e in day["events"]], ["taken", "duplicate", "alert_off", "missed"])
        self.assertEqual(log["summary"]["blocked"], 1)
        self.assertEqual(log["summary"]["adherence"], 50)
        page = self.client.get(f"/c/{self.code}/log?month=2026-11")
        self.assertEqual(page.status_code, 200)
        csv = self.client.get(f"/c/{self.code}/log.csv?month=2026-11").get_data(as_text=True)
        self.assertIn("2026-11-21,08:00,Metformin,500 mg,taken,08:05", csv)
        self.assertIn("duplicate", csv)

    def test_demo_month_fills_history(self):
        self.client.post(f"/c/{self.code}/demo-month")
        with self.app.app_context():
            log = database.month_log(database.get_elder(self.code)["id"], 2026, 11)
        self.assertGreater(log["summary"]["scheduled"], 30)
        self.assertGreater(log["summary"]["missed"], 0)

    def test_price_reports_cheapest_per_pharmacy(self):
        post = lambda **kw: self.client.post("/api/prices", json=kw)
        self.assertEqual(post(osm_id="node/1", pharmacy="A", medicine="Metformin 500mg", price=12000).status_code, 201)
        post(osm_id="node/1", pharmacy="A", medicine="metformin   500MG", price=9500)       # same medicine, other spelling
        post(osm_id="way/2", pharmacy="B", medicine="Metformin 500mg", price=11000)
        res = self.client.get("/api/prices?medicine=METFORMIN 500mg&ids=node/1,way/2,node/3").get_json()
        self.assertEqual(res["node/1"]["price"], 9500)
        self.assertEqual(res["node/1"]["reports"], 2)
        self.assertEqual(res["way/2"]["price"], 11000)
        self.assertNotIn("node/3", res)
        # bad input is refused with a readable message
        self.assertEqual(post(osm_id="javascript:1", pharmacy="X", medicine="Metformin", price=100).status_code, 400)
        self.assertEqual(post(osm_id="node/1", pharmacy="X", medicine="M", price=100).status_code, 400)
        self.assertEqual(post(osm_id="node/1", pharmacy="X", medicine="Metformin", price=-5).status_code, 400)
        self.assertIn("error", post(osm_id="node/1", pharmacy="X", medicine="Metformin", price="abc").get_json())

    def test_pharmacy_page_lists_profile_medicines(self):
        page = self.client.get(f"/pharmacies?code={self.code}").get_data(as_text=True)
        self.assertIn("Metformin", page)
        self.assertEqual(self.client.get("/pharmacies").status_code, 200)

    # ------------------------------------------------ alert reliability
    def _missed_morning(self):
        self.clock = datetime(2026, 11, 21, 8, 31)

    def test_failed_alert_is_not_recorded_as_sent_and_is_retried(self):
        self._missed_morning()
        with self.app.app_context(), TELEGRAM_ON, mock.patch.object(alerts, "send_telegram", return_value=False) as send:
            first = alerts.run_check()
            self.assertEqual(first[0]["status"], "retry")
            self.assertEqual(self.today()[0]["alert_status"], "retry")
            self.assertFalse(self.today()[0]["alerted"])            # NOT counted as sent
            for _ in range(database.ALERT_MAX_TRIES - 1):
                alerts.run_check()
            self.assertEqual(send.call_count, database.ALERT_MAX_TRIES)
            self.assertEqual(alerts.run_check(), [])                # gives up after the last try
            self.assertEqual(self.today()[0]["alert_status"], "failed")
            log = database.month_log(database.get_elder(self.code)["id"], 2026, 11)
        kinds = [e["kind"] for d in log["days"] for e in d["events"]]
        self.assertEqual(kinds.count("missed"), 1)
        self.assertEqual(kinds.count("alert_failed"), 1)
        self.assertNotIn("alert_sent", kinds)

    def test_retry_that_succeeds_is_recorded_as_sent(self):
        self._missed_morning()
        with self.app.app_context(), TELEGRAM_ON:
            with mock.patch.object(alerts, "send_telegram", return_value=False):
                alerts.run_check()
            with mock.patch.object(alerts, "send_telegram", return_value=True):
                self.assertEqual(alerts.run_check()[0]["status"], "sent")
        self.assertEqual(self.today()[0]["alert_status"], "sent")

    def test_no_telegram_setup_is_shown_honestly(self):
        self._missed_morning()
        with self.app.app_context(), mock.patch.object(alerts, "send_telegram") as send:
            self.assertEqual(alerts.run_check()[0]["status"], "not_configured")
        send.assert_not_called()
        self.assertEqual(self.today()[0]["alert_status"], "not_configured")

    def test_real_send_requires_ok_true(self):
        class Resp:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *a): return self.body
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x"}):
            with mock.patch("urllib.request.urlopen", return_value=Resp(b'{"ok": false}')):
                self.assertFalse(alerts.send_telegram("123", "hi"))
            with mock.patch("urllib.request.urlopen", return_value=Resp(b'{"ok": true}')):
                self.assertTrue(alerts.send_telegram("123", "hi"))
            with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
                self.assertFalse(alerts.send_telegram("123", "hi"))
        self.assertFalse(alerts.send_telegram("123", "hi"))         # no token at all

    # ------------------------------------------------ privacy
    def test_dashboard_needs_pin_but_elder_screen_does_not(self):
        stranger = self.app.test_client()
        res = stranger.get(f"/c/{self.code}")
        self.assertEqual(res.status_code, 302)
        self.assertIn(f"/c/{self.code}/unlock", res.headers["Location"])
        for path in [f"/c/{self.code}/log", f"/c/{self.code}/export.json", f"/c/{self.code}/log.csv"]:
            self.assertEqual(stranger.get(path).status_code, 302, path)
        self.assertEqual(stranger.post(f"/c/{self.code}/medicines", data={"name": "X", "times": "09:00"}).status_code, 302)
        self.assertEqual(stranger.get(f"/e/{self.code}").status_code, 200)
        self.assertEqual(stranger.get(f"/api/e/{self.code}/today").status_code, 200)
        res = stranger.post(f"/c/{self.code}/unlock", data={"pin": "123456", "next": f"/c/{self.code}/log"})
        self.assertTrue(res.headers["Location"].endswith(f"/c/{self.code}/log"))
        self.assertEqual(stranger.get(f"/c/{self.code}").status_code, 200)

    def test_wrong_pins_lock_the_profile(self):
        stranger = self.app.test_client()
        for i in range(database.PIN_MAX_FAILS):
            page = stranger.post(f"/c/{self.code}/unlock", data={"pin": "000000"}).get_data(as_text=True)
        self.assertIn("Too many wrong tries", page)
        page = stranger.post(f"/c/{self.code}/unlock", data={"pin": "123456"}).get_data(as_text=True)
        self.assertIn("Too many wrong tries", page)                  # even the right PIN waits
        self.assertEqual(stranger.get(f"/c/{self.code}").status_code, 302)

    def test_pin_is_stored_only_as_hash_and_create_needs_a_valid_pin(self):
        with self.app.app_context():
            elder = database.get_elder(self.code)
            self.assertNotIn("123456", elder["pin_hash"])
            self.assertTrue(elder["pin_hash"])
            count = database.get_db().execute("SELECT COUNT(*) FROM elders").fetchone()[0]
        self.client.post("/create", data={"name": "NoPin"})
        self.client.post("/create", data={"name": "BadPin", "pin": "12ab56", "pin2": "12ab56"})
        self.client.post("/create", data={"name": "Mismatch", "pin": "123456", "pin2": "654321"})
        with self.app.app_context():
            self.assertEqual(database.get_db().execute("SELECT COUNT(*) FROM elders").fetchone()[0], count)
        self.assertEqual(len(self.code), database.CODE_LENGTH)

    def test_lock_this_device(self):
        self.client.post(f"/c/{self.code}/lock")
        self.assertEqual(self.client.get(f"/c/{self.code}").status_code, 302)

    def test_export_has_data_but_no_pin(self):
        data = self.client.get(f"/c/{self.code}/export.json").get_json(force=True)
        self.assertEqual(data["profile"]["name"], "Grandma")
        self.assertEqual(data["medicines"][0]["name"], "Metformin")
        self.assertNotIn("pin_hash", data["profile"])

    def test_delete_profile_needs_the_code_then_removes_everything(self):
        self.today()
        self.client.post(f"/c/{self.code}/delete", data={"confirm": "WRONG"})
        self.assertEqual(self.client.get(f"/e/{self.code}").status_code, 200)    # still there
        self.client.post(f"/c/{self.code}/delete", data={"confirm": self.code.lower()})
        self.assertEqual(self.client.get(f"/e/{self.code}").status_code, 404)
        with self.app.app_context():
            db = database.get_db()
            for table in ("elders", "medicines", "doses", "events"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)

    def test_privacy_page_and_headers(self):
        self.assertEqual(self.client.get("/privacy").status_code, 200)
        res = self.client.get(f"/c/{self.code}")
        self.assertEqual(res.headers["Cache-Control"], "no-store")
        self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertEqual(res.headers["Referrer-Policy"], "same-origin")
        self.assertIn("not a medical device", self.client.get("/").get_data(as_text=True))

    def test_home_page_has_tour_button(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/simulator?tour=1"', page)

    def test_cron_key_required_when_set(self):
        self.app.config["CRON_KEY"] = "secret"
        self.assertEqual(self.client.get("/api/check-missed").status_code, 403)
        self.assertEqual(self.client.get("/api/check-missed?key=secret").status_code, 200)


if __name__ == "__main__":
    unittest.main()
