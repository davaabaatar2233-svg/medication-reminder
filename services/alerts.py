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

from algorithm import rules
from data import database
from services import translate

log = logging.getLogger("sanakh.alerts")


def telegram_configured(chat_id):
    """Can an alert be sent at all? Needs the bot token on the server and the caregiver's chat ID."""
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and bool((chat_id or "").strip())


def send_telegram(chat_id, text):
    """Send a Telegram message. Returns True ONLY if Telegram answered {"ok": true}. Never raises."""
    if not telegram_configured(chat_id):
        return False
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id.strip(), "text": text}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            return json.load(resp).get("ok") is True
    except Exception as exc:  # network down, wrong token, blocked bot, Telegram error...
        log.warning("Telegram send failed: %s", exc)
        return False


def missed_message(dose):
    """Missed-dose alert in the language saved on the profile."""
    t = translate.translator(dose.get("language") or translate.DEFAULT)
    return rules.alert_text("missed", dose["elder_name"], dose["name"], dose["time"], t=t)


def run_check():
    """Alert caregivers about missed doses. A dose counts as "alert sent" only if
    Telegram confirmed it; failures are retried every minute (see database.record_alert).
    Returns the doses that were handled, each with its "outcome"."""
    handled = []
    for dose in database.find_new_missed():
        if not telegram_configured(dose["telegram_chat_id"]):
            outcome = "not_configured"
        elif send_telegram(dose["telegram_chat_id"], missed_message(dose)):
            outcome = "sent"
        else:
            outcome = "failed"
        status = database.record_alert(dose, outcome)
        log.info("Missed dose %s for %s: alert %s", dose["id"], dose["elder_name"], status)
        handled.append({**dose, "outcome": outcome, "status": status})
    return handled


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
