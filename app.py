"""Sanakh: a voice-first medication buddy for elders and their families.

Run locally:   python app.py           then open http://localhost:5000
"""
import logging
import os
from datetime import timedelta

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)

import alerts
import engine
import models

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "instance", "sanakh.db")),
        TIMEZONE=os.environ.get("APP_TIMEZONE", "Asia/Ulaanbaatar"),
        # A dose can be confirmed from EARLY_MINUTES before its time.
        EARLY_MINUTES=int(os.environ.get("EARLY_MINUTES", 30)),
        # After GRACE_MINUTES without confirmation, the dose is "missed" and the caregiver is alerted.
        GRACE_MINUTES=int(os.environ.get("GRACE_MINUTES", 30)),
        # Min time between two doses of one medicine = GAP_RATIO x shortest interval in its schedule.
        GAP_RATIO=float(os.environ.get("GAP_RATIO", 0.5)),
        CRON_KEY=os.environ.get("CRON_KEY", ""),
        START_CHECKER=os.environ.get("START_CHECKER", "1") == "1",
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    app.teardown_appcontext(models.close_db)
    with app.app_context():
        models.init_db()

    register_routes(app)

    if app.config["START_CHECKER"]:
        alerts.start_background_checker(app)
    return app


def load_elder(code):
    elder = models.get_elder(code)
    if elder is None:
        abort(404)
    return elder


def register_routes(app):
    # ------------------------------------------------------------------ home
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/create")
    def create():
        name = request.form.get("name", "").strip()
        caregiver = request.form.get("caregiver_name", "").strip()
        if not name:
            flash("Please enter the name of the person taking the medicine.", "error")
            return redirect(url_for("index"))
        code = models.create_elder(name, caregiver)
        flash(f"Profile created. Share code: {code}", "ok")
        return redirect(url_for("caregiver", code=code))

    @app.post("/open")
    def open_profile():
        code = request.form.get("code", "").strip().upper()
        if models.get_elder(code) is None:
            flash("No profile with that code. Check the letters and try again.", "error")
            return redirect(url_for("index"))
        view = "elder" if request.form.get("view") == "elder" else "caregiver"
        return redirect(url_for(view, code=code))

    # ------------------------------------------------------------- caregiver
    @app.get("/c/<code>")
    def caregiver(code):
        elder = load_elder(code)
        return render_template(
            "caregiver.html",
            elder=elder,
            medicines=models.list_medicines(elder["id"]),
            history=models.history(elder["id"]),
            telegram_ready=bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
            grace=app.config["GRACE_MINUTES"],
        )

    @app.post("/c/<code>/medicines")
    def add_medicine(code):
        elder = load_elder(code)
        name = request.form.get("name", "").strip()
        if not name:
            flash("Medicine name is required.", "error")
            return redirect(url_for("caregiver", code=elder["code"]))
        try:
            times = models.parse_times(request.form.get("times", ""))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("caregiver", code=elder["code"]))
        models.add_medicine(elder["id"], name, request.form.get("dose", "").strip(),
                            times, request.form.get("note", "").strip())
        flash(f"Added {name} at {', '.join(times)}.", "ok")
        return redirect(url_for("caregiver", code=elder["code"]))

    @app.post("/c/<code>/medicines/<int:medicine_id>/remove")
    def remove_medicine(code, medicine_id):
        elder = load_elder(code)
        models.remove_medicine(elder["id"], medicine_id)
        flash("Medicine removed.", "ok")
        return redirect(url_for("caregiver", code=elder["code"]))

    @app.post("/c/<code>/demo-dose")
    def demo_dose(code):
        """For demos: add a medicine due 1 minute from now."""
        elder = load_elder(code)
        due = (models.now() + timedelta(minutes=1)).strftime("%H:%M")
        models.add_medicine(elder["id"], "Vitamin D (demo)", "1 tablet", [due], "Demo dose")
        flash(f"Demo dose added for {due}. Open the elder screen to see the reminder.", "ok")
        return redirect(url_for("caregiver", code=elder["code"]))

    @app.post("/c/<code>/settings")
    def settings(code):
        elder = load_elder(code)
        models.update_settings(
            elder["id"],
            request.form.get("name", "").strip() or elder["name"],
            request.form.get("caregiver_name", "").strip(),
            request.form.get("telegram_chat_id", ""),
        )
        flash("Settings saved.", "ok")
        return redirect(url_for("caregiver", code=elder["code"]))

    @app.post("/c/<code>/test-telegram")
    def test_telegram(code):
        elder = load_elder(code)
        ok = alerts.send_telegram(elder["telegram_chat_id"],
                                  f"👋 Sanakh alerts for {elder['name']} are working.")
        flash("Test message sent. Check Telegram." if ok else
              "Could not send. Check the chat ID, that you pressed Start in the bot, "
              "and that TELEGRAM_BOT_TOKEN is set on the server.", "ok" if ok else "error")
        return redirect(url_for("caregiver", code=elder["code"]))

    # ----------------------------------------------------------------- elder
    @app.get("/e/<code>")
    def elder(code):
        return render_template("elder.html", elder=load_elder(code))

    @app.get("/e/<code>/manifest.webmanifest")
    def manifest(code):
        """Per-person manifest so 'Add to Home screen' opens straight to their screen."""
        elder = load_elder(code)
        return jsonify({
            "name": f"Sanakh · {elder['name']}",
            "short_name": "Sanakh",
            "start_url": url_for("elder", code=elder["code"]),
            "scope": "/",
            "display": "standalone",
            "background_color": "#fffaf0",
            "theme_color": "#1f6f5c",
            "icons": [{"src": url_for("static", filename="icon.svg"),
                       "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
        }), 200, {"Content-Type": "application/manifest+json"}

    @app.get("/sw.js")
    def service_worker():
        return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")

    # ------------------------------------------------------------------- API
    @app.get("/api/e/<code>/today")
    def api_today(code):
        elder = load_elder(code)
        return jsonify({
            "elder": elder["name"],
            "now": models.now().strftime("%H:%M"),
            "doses": models.today_doses(elder["id"]),
        })

    @app.post("/api/e/<code>/doses/<int:dose_id>/take")
    def api_take(code, dose_id):
        elder = load_elder(code)
        result, dose, decision = models.take_dose(elder["id"], dose_id)
        if result == "not_found":
            return jsonify({"result": result}), 404
        at = models.now().strftime("%H:%M")
        if result == "ok" and dose["late"]:
            # Caregiver was already told it was missed; tell them it's now taken.
            alerts.send_telegram(elder["telegram_chat_id"],
                                 engine.alert_text("taken_late", elder["name"], dose["name"], dose["time"], dose["taken_at"]))
        elif decision.alert_caregiver:
            extra = f"Safe after {decision.safe_after:%H:%M}." if decision.safe_after else ""
            alerts.send_telegram(elder["telegram_chat_id"],
                                 engine.alert_text(result, elder["name"], dose["name"], dose["time"], at, extra))
        return jsonify({
            "result": result,
            "reason": decision.reason,
            "safe_after": f"{decision.safe_after:%H:%M}" if decision.safe_after else None,
            "dose": dose,
        })

    # ------------------------------------------------------------- simulator
    @app.get("/simulator")
    def simulator():
        return render_template("simulator.html")

    @app.post("/api/simulate")
    def api_simulate():
        """Replay a day with the same rules as the live app. Nothing is saved."""
        data = request.get_json(silent=True) or {}
        try:
            raw = data.get("settings", {})
            settings = engine.Settings(
                early_minutes=max(0, min(120, int(raw.get("early_minutes", 30)))),
                grace_minutes=max(1, min(240, int(raw.get("grace_minutes", 30)))),
                gap_ratio=max(0.0, min(1.0, float(raw.get("gap_ratio", 0.5)))),
            )
            medicines = []
            for med in data.get("medicines", [])[:6]:
                name = str(med.get("name", "")).strip()[:40] or "Medicine"
                times = models.parse_times(str(med.get("times", "")))[:8]
                medicines.append({"name": name, "times": times})
            attempts = []
            for a in data.get("attempts", [])[:40]:
                attempts.append({"mi": int(a["mi"]),
                                 "dose_time": models.parse_times(str(a["dose_time"]))[0],
                                 "at": models.parse_times(str(a["at"]))[0]})
            now = models.parse_times(str(data.get("now", "12:00")))[0]
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(engine.simulate(medicines, attempts, now, settings))

    @app.route("/api/check-missed", methods=["GET", "POST"])
    def api_check_missed():
        """Called by an external cron (cron-job.org) every few minutes."""
        key = app.config["CRON_KEY"]
        if key and request.args.get("key") != key:
            abort(403)
        missed = alerts.run_check()
        return jsonify({"alerted": len(missed)})

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("404.html"), 404


app = create_app()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
