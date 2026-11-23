"""Sanakh: a voice-first medication buddy for elders and their families.

Run locally:   python app.py           then open http://localhost:5000
"""
import logging
import os
from datetime import timedelta
from zoneinfo import available_timezones

from flask import (Flask, abort, flash, g, jsonify, make_response, redirect,
                   render_template, request, send_from_directory, session, url_for)

from algorithm import rules
from data import database
from services import alerts, firewall, translate

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Real IANA names like "Asia/Ulaanbaatar", for the time-zone picker.
TIMEZONES = sorted(tz for tz in available_timezones() if "/" in tz and not tz.startswith(("Etc/", "SystemV/", "posix/", "right/")))


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        # Signs the session cookie that remembers which dashboards this device unlocked.
        SECRET_KEY=os.environ.get("SECRET_KEY") or None,     # None -> a random key saved in instance/
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),        # stay unlocked on this device for 30 days
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),  # https-only cookie on Render
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
        # Firewall (services/firewall.py): rate limits, blocked robot paths, size limit.
        FIREWALL=os.environ.get("FIREWALL", "1") == "1",
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _saved_secret_key(os.path.dirname(app.config["DATABASE"]))
    app.teardown_appcontext(database.close_db)
    firewall.protect(app, behind_proxy=bool(os.environ.get("RENDER")))   # runs before every route
    with app.app_context():
        database.init_db()

    register_routes(app)

    if app.config["START_CHECKER"]:
        alerts.start_background_checker(app)
    return app


def _saved_secret_key(folder):
    """A random secret key, created once and kept in a file, so nobody can forge
    the session cookie even if SECRET_KEY was never set on the server."""
    import secrets
    path = os.path.join(folder, "secret_key")
    try:
        with open(path) as f:
            key = f.read().strip()
            if len(key) >= 32:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


def is_unlocked(elder):
    """Has this device entered the caregiver PIN for this profile?"""
    return elder["code"] in session.get("unlocked", [])


def mark_unlocked(elder):
    codes = [c for c in session.get("unlocked", []) if c != elder["code"]][-19:]   # keep the cookie small
    session["unlocked"] = codes + [elder["code"]]
    session.permanent = True


def _(key, **params):
    """Translate into the language of the current page."""
    return translate.t(g.lang, key, **params)


def error_text(exc):
    return exc.message(g.lang) if isinstance(exc, database.TimeFormatError) else str(exc)


def elder_t(elder):
    """Translator for the elder profile's language (used for Telegram alerts)."""
    return translate.translator(elder["language"] or translate.DEFAULT)


def load_elder(code):
    elder = database.get_elder(code)
    if elder is None:
        abort(404)
    return elder


def register_routes(app):
    # -------------------------------------------------------------- language
    @app.before_request
    def pick_language():
        """?lang=xx  >  saved choice (cookie)  >  browser language  >  English."""
        g.lang_explicit = translate.normalize(request.args.get("lang")) or translate.normalize(request.cookies.get("lang"))
        g.lang = g.lang_explicit or translate.normalize(
            request.accept_languages.best_match(list(translate.LANGUAGES))) or translate.DEFAULT

    # Caregiver pages show health information: they need the PIN (the elder screen doesn't).
    OPEN_CAREGIVER_ENDPOINTS = {"unlock"}

    @app.before_request
    def require_caregiver_pin():
        if not request.path.startswith("/c/") or request.endpoint in OPEN_CAREGIVER_ENDPOINTS:
            return None
        code = (request.view_args or {}).get("code")
        elder = database.get_elder(code) if code else None
        if elder is None:
            return None                           # the route itself answers 404
        if not is_unlocked(elder):
            return redirect(url_for("unlock", code=elder["code"], next=request.full_path.rstrip("?")))
        return None

    @app.after_request
    def privacy_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")          # share codes never leak in links
        resp.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
        if request.path.startswith(("/c/", "/e/", "/api/e/")):
            resp.headers["Cache-Control"] = "no-store"                      # health data isn't cached
        return resp

    @app.context_processor
    def translation_helpers():
        return {
            "_": lambda key, **p: translate.t_html(g.lang, key, **p),
            "lang": g.lang,
            "languages": translate.LANGUAGES,
            "speech_lang": translate.speech_tag(g.lang),
            "i18n_json": translate.js_strings(g.lang),
        }

    @app.get("/lang/<code>")
    def set_language(code):
        lang = translate.normalize(code) or translate.DEFAULT
        target = request.args.get("next", "/")
        if not target.startswith("/") or target.startswith("//"):   # only same-site paths
            target = "/"
        resp = make_response(redirect(target))
        resp.set_cookie("lang", lang, max_age=365 * 24 * 3600, samesite="Lax")
        return resp

    # ------------------------------------------------------------------ home
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/create")
    def create():
        name = request.form.get("name", "").strip()
        caregiver = request.form.get("caregiver_name", "").strip()
        pin, pin2 = request.form.get("pin", ""), request.form.get("pin2", "")
        if not name:
            flash(_("fl.need_name"), "error")
            return redirect(url_for("index"))
        if not database.valid_pin(pin):
            flash(_("pin.err_format"), "error")
            return redirect(url_for("index"))
        if pin != pin2:
            flash(_("pin.err_match"), "error")
            return redirect(url_for("index"))
        # The browser sends its own time zone (hidden field filled by JavaScript).
        code = database.create_elder(name, caregiver, request.form.get("timezone", "").strip(), g.lang)
        elder = database.get_elder(code)
        database.set_pin(elder["id"], pin)
        mark_unlocked(elder)                     # the person who created it doesn't need to type it again
        flash(_("fl.created", code=code), "ok")
        return redirect(url_for("caregiver", code=code))

    @app.post("/open")
    def open_profile():
        code = request.form.get("code", "").strip().upper()
        if database.get_elder(code) is None:
            flash(_("fl.no_code"), "error")
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
            medicines=database.list_medicines(elder["id"]),
            history=database.history(elder["id"]),
            telegram_ready=bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
            timezone=database.timezone_of(elder),
            timezones=TIMEZONES,
            grace=app.config["GRACE_MINUTES"],
            elder_lang=translate.normalize(elder["language"]) or translate.DEFAULT,
        )

    @app.post("/c/<code>/medicines")
    def add_medicine(code):
        elder = load_elder(code)
        name = request.form.get("name", "").strip()
        if not name:
            flash(_("fl.med_name"), "error")
            return redirect(url_for("caregiver", code=elder["code"]))
        try:
            times = database.parse_times(request.form.get("times", ""))
        except ValueError as exc:
            flash(error_text(exc), "error")
            return redirect(url_for("caregiver", code=elder["code"]))
        database.add_medicine(elder["id"], name, request.form.get("dose", "").strip(),
                            times, request.form.get("note", "").strip())
        flash(_("fl.added", name=name, times=", ".join(times)), "ok")
        return redirect(url_for("caregiver", code=elder["code"]))

    @app.post("/c/<code>/medicines/<int:medicine_id>/remove")
    def remove_medicine(code, medicine_id):
        elder = load_elder(code)
        database.remove_medicine(elder["id"], medicine_id)
        flash(_("fl.removed"), "ok")
        return redirect(url_for("caregiver", code=elder["code"]))

    @app.post("/c/<code>/demo-dose")
    def demo_dose(code):
        """For demos: add a medicine due 1 minute from now."""
        elder = load_elder(code)
        due = (database.elder_now(elder["id"]) + timedelta(minutes=1)).strftime("%H:%M")
        database.add_medicine(elder["id"], _("demo.med"), _("demo.dose"), [due], _("demo.note"))
        flash(_("fl.demo", time=due), "ok")
        return redirect(url_for("caregiver", code=elder["code"]))

    # ----------------------------------------------------------- monthly log
    def _month_arg(elder):
        """?month=YYYY-MM, default: this month in the elder's time zone."""
        raw = request.args.get("month", "")
        try:
            year, month = (int(x) for x in raw.split("-"))
            if 2000 <= year <= 2100 and 1 <= month <= 12:
                return year, month
        except ValueError:
            pass
        today = database.elder_now(elder["id"]).date()
        return today.year, today.month

    @app.get("/c/<code>/log")
    def month_log(code):
        elder = load_elder(code)
        year, month = _month_arg(elder)
        prev_m = f"{year - (month == 1):04d}-{12 if month == 1 else month - 1:02d}"
        next_m = f"{year + (month == 12):04d}-{1 if month == 12 else month + 1:02d}"
        return render_template("log.html", elder=elder, log=database.month_log(elder["id"], year, month),
                               prev_m=prev_m, next_m=next_m, timezone=database.timezone_of(elder))

    @app.get("/c/<code>/log.csv")
    def month_log_csv(code):
        """The month as a spreadsheet (opens in Excel / Google Sheets), e.g. to show a doctor."""
        import csv
        import io
        elder = load_elder(code)
        year, month = _month_arg(elder)
        log = database.month_log(elder["id"], year, month)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "time", "medicine", "dose", "status", "taken_at", "events"])
        for day in log["days"]:
            for d in day["doses"]:
                ev = "; ".join(f"{e['at']} {e['kind']}" for e in day["events"]
                               if e["time"] == d["time"] and e["name"] == d["name"])
                w.writerow([day["day"], d["time"], d["name"], d["dose"], d["status"], d["taken_at"] or "", ev])
        filename = f"sanakh-{elder['code']}-{log['month']}.csv"
        # UTF-8 with BOM so Excel shows Mongolian/Chinese/etc. names correctly
        return ("\ufeff" + buf.getvalue(), 200,
                {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/c/<code>/demo-month")
    def demo_month(code):
        elder = load_elder(code)
        database.fill_demo_month(elder["id"])
        flash(_("fl.demo_month"), "ok")
        return redirect(url_for("month_log", code=elder["code"]))

    # ------------------------------------------------------ privacy and PIN
    @app.route("/c/<code>/unlock", methods=["GET", "POST"])
    def unlock(code):
        elder = load_elder(code)
        has_pin = bool(elder["pin_hash"])
        target = request.values.get("next", "")
        if not target.startswith(f"/c/{elder['code']}"):
            target = url_for("caregiver", code=elder["code"])
        if request.method == "POST":
            pin = request.form.get("pin", "")
            if not has_pin:                                   # older profile: set a PIN now
                if not database.valid_pin(pin):
                    flash(_("pin.err_format"), "error")
                elif pin != request.form.get("pin2", ""):
                    flash(_("pin.err_match"), "error")
                else:
                    database.set_pin(elder["id"], pin)
                    mark_unlocked(elder)
                    return redirect(target)
            else:
                result, n = database.check_pin(elder["id"], pin)
                if result == "ok":
                    mark_unlocked(elder)
                    return redirect(target)
                flash(_("pin.err_locked", minutes=n) if result == "locked" else _("pin.err_wrong", left=n), "error")
        return render_template("unlock.html", elder=elder, has_pin=has_pin, next=target)

    @app.post("/c/<code>/lock")
    def lock(code):
        elder = load_elder(code)
        session["unlocked"] = [c for c in session.get("unlocked", []) if c != elder["code"]]
        flash(_("pv.locked"), "ok")
        return redirect(url_for("index"))

    @app.get("/c/<code>/export.json")
    def export(code):
        import json
        elder = load_elder(code)
        body = json.dumps(database.export_elder(elder["id"]), ensure_ascii=False, indent=2)
        return body, 200, {"Content-Type": "application/json; charset=utf-8",
                           "Content-Disposition": f'attachment; filename="sanakh-{elder["code"]}-data.json"'}

    @app.post("/c/<code>/delete")
    def delete_profile(code):
        elder = load_elder(code)
        if request.form.get("confirm", "").strip().upper() != elder["code"]:
            flash(_("pv.delete_wrong"), "error")
            return redirect(url_for("caregiver", code=elder["code"]) + "#privacy")
        database.delete_elder(elder["id"])
        session["unlocked"] = [c for c in session.get("unlocked", []) if c != elder["code"]]
        flash(_("pv.deleted"), "ok")
        return redirect(url_for("index"))

    @app.get("/privacy")
    def privacy():
        return render_template("privacy.html")

    @app.post("/c/<code>/settings")
    def settings(code):
        elder = load_elder(code)
        timezone = request.form.get("timezone", "").strip()
        if not database.valid_timezone(timezone):
            flash(_("fl.bad_tz", tz=timezone), "error")
            return redirect(url_for("caregiver", code=elder["code"]))
        database.update_settings(
            elder["id"],
            request.form.get("name", "").strip() or elder["name"],
            request.form.get("caregiver_name", "").strip(),
            request.form.get("telegram_chat_id", ""),
            timezone,
            request.form.get("language", elder["language"]),
        )
        flash(_("fl.saved"), "ok")
        return redirect(url_for("caregiver", code=elder["code"]))

    @app.post("/c/<code>/test-telegram")
    def test_telegram(code):
        elder = load_elder(code)
        ok = alerts.send_telegram(elder["telegram_chat_id"], elder_t(elder)("tg.test", name=elder["name"]))
        flash(_("fl.test_ok") if ok else _("fl.test_fail"), "ok" if ok else "error")
        return redirect(url_for("caregiver", code=elder["code"]))

    # ----------------------------------------------------------------- elder
    @app.get("/e/<code>")
    def elder(code):
        elder = load_elder(code)
        if not g.lang_explicit:   # nobody chose a language on this device: use the profile's
            g.lang = translate.normalize(elder["language"]) or g.lang
        return render_template("elder.html", elder=elder, timezone=database.timezone_of(elder))

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
            "icons": [{"src": url_for("static", filename="img/icon.svg"),
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
            "timezone": database.timezone_of(elder),
            "now": database.elder_now(elder["id"]).strftime("%H:%M"),
            "doses": database.today_doses(elder["id"]),
        })

    @app.post("/api/e/<code>/doses/<int:dose_id>/take")
    def api_take(code, dose_id):
        elder = load_elder(code)
        result, dose, decision = database.take_dose(elder["id"], dose_id)
        if result == "not_found":
            return jsonify({"result": result}), 404
        at = database.elder_now(elder["id"]).strftime("%H:%M")
        t = elder_t(elder)   # alerts go out in the profile's language
        message = None
        if result == "ok" and dose["late"]:
            # Caregiver was already told it was missed; tell them it's now taken.
            message = rules.alert_text("taken_late", elder["name"], dose["name"], dose["time"], dose["taken_at"], t=t)
        elif decision.alert_caregiver:
            allowed = f"{decision.allowed_after:%H:%M}" if decision.allowed_after else None
            message = rules.alert_text(result, elder["name"], dose["name"], dose["time"], at, allowed, t=t)
        if message:
            # Record what really happened, so the log never claims an alert that wasn't delivered.
            if not alerts.telegram_configured(elder["telegram_chat_id"]):
                kind = "alert_off"
            else:
                kind = "alert_sent" if alerts.send_telegram(elder["telegram_chat_id"], message) else "alert_failed"
            database.log_event(elder["id"], dose_id, database.elder_now(elder["id"]).date().isoformat(),
                               database.elder_now(elder["id"]), kind)
            database.get_db().commit()
        return jsonify({
            "result": result,
            "reason": decision.reason,
            "allowed_after": f"{decision.allowed_after:%H:%M}" if decision.allowed_after else None,
            "dose": dose,
        })

    # ---------------------------------------------------- pharmacy finder
    @app.get("/pharmacies")
    def pharmacies():
        """Map of nearby pharmacies with user-reported prices. ?code= adds the elder's medicines."""
        elder = database.get_elder(request.args.get("code", ""))
        meds = [m["name"] for m in database.list_medicines(elder["id"])] if elder else []
        return render_template("pharmacies.html", elder=elder, medicines=meds)

    @app.get("/api/prices")
    def api_prices():
        ids = [i for i in request.args.get("ids", "").split(",") if i]
        return jsonify(database.cheapest_prices(request.args.get("medicine", ""), ids))

    @app.post("/api/prices")
    def api_add_price():
        data = request.get_json(silent=True) or {}
        try:
            database.add_price(data.get("osm_id"), data.get("pharmacy"), data.get("medicine"), data.get("price"))
        except ValueError as exc:
            return jsonify({"error": _(str(exc))}), 400
        return jsonify({"ok": True}), 201

    # ------------------------------------------------------------- simulator
    @app.get("/simulator")
    def simulator():
        return render_template("simulator.html")

    @app.post("/api/simulate")
    def api_simulate():
        """Replay a day with the same rules as the live app. Nothing is saved."""
        data = request.get_json(silent=True) or {}
        lang = translate.normalize(data.get("lang")) or g.lang
        try:
            raw = data.get("settings", {})
            settings = rules.Settings(
                early_minutes=max(0, min(120, int(raw.get("early_minutes", 30)))),
                grace_minutes=max(1, min(240, int(raw.get("grace_minutes", 30)))),
                gap_ratio=max(0.0, min(1.0, float(raw.get("gap_ratio", 0.5)))),
            )
            medicines = []
            for med in data.get("medicines", [])[:6]:
                name = str(med.get("name", "")).strip()[:40] or translate.t(lang, "sim.med_ph")
                times = database.parse_times(str(med.get("times", "")))[:8]
                medicines.append({"name": name, "times": times})
            attempts = []
            for a in data.get("attempts", [])[:40]:
                attempts.append({"mi": int(a["mi"]),
                                 "dose_time": database.parse_times(str(a["dose_time"]))[0],
                                 "at": database.parse_times(str(a["at"]))[0]})
            now = database.parse_times(str(data.get("now", "12:00")))[0]
        except database.TimeFormatError as exc:
            return jsonify({"error": exc.message(lang)}), 400
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(rules.simulate(medicines, attempts, now, settings, t=translate.translator(lang)))

    @app.route("/api/check-missed", methods=["GET", "POST"])
    def api_check_missed():
        """Called by an external cron (cron-job.org) every few minutes."""
        key = app.config["CRON_KEY"]
        if key and request.args.get("key") != key:
            abort(403)
        handled = alerts.run_check()
        return jsonify({"handled": len(handled),
                        "sent": sum(1 for d in handled if d["outcome"] == "sent"),
                        "failed": sum(1 for d in handled if d["outcome"] == "failed")})

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("404.html"), 404


app = create_app()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
