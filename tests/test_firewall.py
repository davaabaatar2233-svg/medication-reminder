"""Firewall tests: robots are blocked, guessing is slowed down, normal use passes."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app import create_app  # noqa: E402
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"Install Flask first: python -m pip install -r requirements.txt ({e})")
from services import firewall  # noqa: E402


class FirewallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"DATABASE": os.path.join(self.tmp.name, "t.db"),
                               "START_CHECKER": False, "TESTING": True})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_normal_pages_pass(self):
        for path in ("/", "/simulator", "/privacy", "/static/css/style.css"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_robot_paths_blocked(self):
        for path in ("/wp-admin/", "/.env", "/index.php", "/.git/config", "/static/../app.py"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_repeat_offender_is_banned(self):
        for _ in range(firewall.STRIKES_TO_BAN):
            self.client.get("/.env")
        r = self.client.get("/")                       # even a normal page is now refused
        self.assertEqual(r.status_code, 403)
        self.assertIn("Retry-After", r.headers)

    def test_pin_guessing_is_slowed(self):
        codes = [self.client.post("/open", data={"code": "WRONG123"}).status_code
                 for _ in range(firewall.LIMIT_SENSITIVE + 1)]
        self.assertNotIn(429, codes[:firewall.LIMIT_SENSITIVE])
        self.assertEqual(codes[-1], 429)

    def test_simulator_play_fast_is_not_blocked(self):
        body = {"medicines": [{"name": "A", "times": "08:00, 20:00"}], "attempts": [], "now": "09:00"}
        for _ in range(150):                           # Fast speed for over a minute
            self.assertEqual(self.client.post("/api/simulate", json=body).status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_other_addresses_not_affected(self):
        for _ in range(firewall.STRIKES_TO_BAN):
            self.client.get("/.env")
        r = self.client.get("/", environ_base={"REMOTE_ADDR": "10.0.0.9"})
        self.assertEqual(r.status_code, 200)

    def test_bad_method_and_big_body(self):
        self.assertEqual(self.client.put("/").status_code, 405)
        r = self.client.post("/api/simulate", data=b"x" * (firewall.MAX_BODY + 1),
                             content_type="application/json")
        self.assertEqual(r.status_code, 413)

    def test_api_gets_json_and_translation(self):
        self.client.set_cookie("lang", "mn")
        r = self.client.get("/api/x.php")
        self.assertEqual(r.status_code, 404)
        self.assertIn("зөвшөөрөхгүй", r.get_json()["error"])

    def test_can_be_switched_off(self):
        self.app.config["FIREWALL"] = False
        self.assertNotEqual(self.client.get("/.env").status_code, 403)


if __name__ == "__main__":
    unittest.main()
