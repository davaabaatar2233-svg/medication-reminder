"""Tests for the decision rules in algorithm/rules.py. Run with:  python -m unittest discover tests"""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from algorithm import rules  # noqa: E402

S = rules.Settings(early_minutes=30, grace_minutes=30, gap_ratio=0.5)
T = lambda hm: datetime(2026, 11, 21, *map(int, hm.split(":")))  # noqa: E731


class StatusRules(unittest.TestCase):
    def status(self, now, taken=None):
        return rules.dose_status(T("08:00"), T(taken) if taken else None, T(now), S)[0]

    def test_status_moves_through_the_day(self):
        self.assertEqual(self.status("07:00"), "upcoming")
        self.assertEqual(self.status("07:45"), "open")
        self.assertEqual(self.status("08:10"), "due")
        self.assertEqual(self.status("08:30"), "due")       # grace ends exactly at 08:30
        self.assertEqual(self.status("08:31"), "missed")

    def test_taken_on_time_and_late(self):
        self.assertEqual(self.status("09:00", taken="08:05"), "taken")
        self.assertEqual(self.status("10:00", taken="09:40"), "taken_late")

    def test_every_status_has_a_reason(self):
        for now in ["07:00", "07:45", "08:10", "09:00"]:
            self.assertTrue(rules.dose_status(T("08:00"), None, T(now), S)[1])


class DoubleDoseRules(unittest.TestCase):
    def test_min_gap(self):
        self.assertEqual(rules.min_gap(["08:00", "20:00"], S), (360, 720))
        self.assertEqual(rules.min_gap(["08:00", "12:00", "16:00", "20:00"], S), (120, 240))
        self.assertEqual(rules.min_gap(["09:00"], S), (720, 1440))
        self.assertEqual(rules.min_gap(["22:00", "02:00"], S), (120, 240))   # wraps past midnight

    def test_duplicate_is_blocked_and_alerts(self):
        d = rules.check_confirmation(T("08:00"), T("08:02"), T("09:15"), None, 360, S, "Metformin")
        self.assertEqual(d.result, "duplicate")
        self.assertTrue(d.alert_caregiver)
        self.assertIn("08:02", d.reason)

    def test_too_close_is_blocked_with_allowed_time(self):
        d = rules.check_confirmation(T("12:00"), None, T("12:05"), T("11:20"), 120, S, "Paracetamol")
        self.assertEqual(d.result, "too_close")
        self.assertEqual(d.allowed_after, T("13:20"))
        self.assertTrue(d.alert_caregiver)

    def test_too_early(self):
        d = rules.check_confirmation(T("20:00"), None, T("12:00"), None, 360, S)
        self.assertEqual(d.result, "too_early")
        self.assertFalse(d.alert_caregiver)

    def test_ok(self):
        d = rules.check_confirmation(T("20:00"), None, T("20:05"), T("08:00"), 360, S)
        self.assertEqual(d.result, "ok")

    def test_gap_ratio_zero_turns_check_off(self):
        s = rules.Settings(gap_ratio=0)
        gap, _ = rules.min_gap(["08:00", "12:00"], s)
        d = rules.check_confirmation(T("12:00"), None, T("12:00"), T("11:59"), gap, s)
        self.assertEqual(d.result, "ok")


class Simulation(unittest.TestCase):
    MEDS = [{"name": "Metformin", "times": ["08:00", "20:00"]}]

    def test_double_press_scenario(self):
        r = rules.simulate(self.MEDS, [{"mi": 0, "dose_time": "08:00", "at": "08:02"},
                                        {"mi": 0, "dose_time": "08:00", "at": "09:15"}], "10:00", S)
        self.assertEqual([a["result"] for a in r["attempts"]], ["ok", "duplicate"])
        self.assertEqual(len(r["alerts"]), 1)
        self.assertIn("possible double dose", r["alerts"][0]["text"])

    def test_missed_then_taken_late(self):
        r = rules.simulate(self.MEDS, [{"mi": 0, "dose_time": "08:00", "at": "08:04"},
                                        {"mi": 0, "dose_time": "20:00", "at": "21:40"}], "22:00", S)
        self.assertEqual([d["status"] for d in r["doses"]], ["taken", "taken_late"])
        self.assertEqual([a["at"] for a in r["alerts"]], ["20:30", "21:40"])

    def test_future_presses_are_pending(self):
        r = rules.simulate(self.MEDS, [{"mi": 0, "dose_time": "20:00", "at": "20:05"}], "12:00", S)
        self.assertEqual(r["attempts"][0]["result"], "pending")
        self.assertEqual(r["doses"][1]["status"], "upcoming")
        self.assertEqual([a["at"] for a in r["alerts"]], ["08:30"])   # only the missed 08:00 dose

    def test_on_hold_prevents_false_missed_alert(self):
        meds = [{"name": "Paracetamol", "times": ["08:00", "12:00", "16:00", "20:00"]}]
        presses = [{"mi": 0, "dose_time": "08:00", "at": "11:20"},
                   {"mi": 0, "dose_time": "12:00", "at": "12:05"}]
        r = rules.simulate(meds, presses, "13:00", S)
        self.assertEqual(r["doses"][1]["status"], "on_hold")          # told to wait until 13:20
        r = rules.simulate(meds, presses + [{"mi": 0, "dose_time": "12:00", "at": "13:30"}], "14:00", S)
        self.assertEqual(r["doses"][1]["status"], "taken")            # taken before 13:50 -> not missed
        self.assertNotIn("12:30", [a["at"] for a in r["alerts"]])
        r = rules.simulate(meds, presses, "14:00", S)
        self.assertEqual(r["doses"][1]["status"], "missed")           # never taken -> missed at 13:50
        self.assertIn("13:50", [a["at"] for a in r["alerts"]])

    def test_changing_grace_changes_decision(self):
        attempts = [{"mi": 0, "dose_time": "08:00", "at": "08:45"}]
        strict = rules.simulate(self.MEDS, attempts, "09:00", rules.Settings(grace_minutes=30))
        relaxed = rules.simulate(self.MEDS, attempts, "09:00", rules.Settings(grace_minutes=60))
        self.assertEqual(strict["doses"][0]["status"], "taken_late")
        self.assertEqual(relaxed["doses"][0]["status"], "taken")


if __name__ == "__main__":
    unittest.main()
