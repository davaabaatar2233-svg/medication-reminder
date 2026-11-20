"""Caregiver alerts: Telegram messages and the background missed-dose checker.

Uses only the Python standard library (urllib), no extra packages.
"""
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request

import engine
import models

log = logging.getLogger("sanakh.alerts")


def send_telegram(chat_id, text):
    """Send a Telegram message. Returns True on success, never raises."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            return bool(json.load(resp).get("ok"))
    except Exception as exc:  # network down, wrong token, blocked bot...
        log.warning("Telegram send failed: %s", exc)
        return False


def missed_message(dose):
    return engine.alert_text("missed", dose["elder_name"], dose["name"], dose["time"])


def run_check():
    """Find newly missed doses and alert each caregiver. Returns the missed doses."""
    missed = models.find_new_missed()
    for dose in missed:
        sent = send_telegram(dose["telegram_chat_id"], missed_message(dose))
        log.info("Missed dose %s for %s (telegram sent: %s)", dose["id"], dose["elder_name"], sent)
    return missed


def start_background_checker(app, interval_seconds=60):
    """Run run_check() every minute in a daemon thread.

    On free hosting that sleeps when idle, also point an external cron
    (e.g. cron-job.org) at /api/check-missed so checks keep happening.
    """
    def loop():
        while True:
            try:
                with app.app_context():
                    run_check()
            except Exception:
                log.exception("Missed-dose check failed")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=loop, name="missed-dose-checker", daemon=True)
    thread.start()
    return thread
