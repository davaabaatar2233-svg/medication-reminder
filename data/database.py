"""Database access and medication logic for Sanakh.

All times are stored as local wall-clock strings in the app's time zone
(config TIMEZONE, default Asia/Ulaanbaatar):
  doses.day       "YYYY-MM-DD"
  doses.time      "HH:MM"
  doses.taken_at  "YYYY-MM-DD HH:MM:SS"
"""
import secrets
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, g
from werkzeug.security import check_password_hash, generate_password_hash

from algorithm import rules
from services import translate

SCHEMA = """
CREATE TABLE IF NOT EXISTS elders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    caregiver_name   TEXT NOT NULL DEFAULT '',
    code             TEXT NOT NULL UNIQUE,      -- share code used in URLs
    telegram_chat_id TEXT NOT NULL DEFAULT '',
    timezone         TEXT NOT NULL DEFAULT '',  -- IANA name, e.g. Asia/Ulaanbaatar ('' = app default)
    language         TEXT NOT NULL DEFAULT 'en', -- language for the elder screen and Telegram alerts
    pin_hash         TEXT NOT NULL DEFAULT '',  -- caregiver PIN, stored only as a salted hash
    pin_fails        INTEGER NOT NULL DEFAULT 0,-- wrong PIN tries in a row
    pin_locked_until TEXT NOT NULL DEFAULT '',  -- after too many wrong tries (UTC)
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS medicines (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id   INTEGER NOT NULL REFERENCES elders(id),
    name       TEXT NOT NULL,
    dose       TEXT NOT NULL DEFAULT '',        -- e.g. "500 mg, 1 tablet"
    times      TEXT NOT NULL,                   -- e.g. "08:00,20:00"
    note       TEXT NOT NULL DEFAULT '',        -- e.g. "after food"
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL                    -- "YYYY-MM-DD HH:MM"
);

-- One row per scheduled dose per day, created on demand by ensure_doses().
CREATE TABLE IF NOT EXISTS doses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id INTEGER NOT NULL REFERENCES medicines(id),
    day         TEXT NOT NULL,
    time        TEXT NOT NULL,
    taken_at    TEXT,                           -- NULL until confirmed
    alerted_at  TEXT,                           -- when the caregiver was alerted
    hold_until  TEXT,                           -- set by the too-close check: earliest allowed time
    alert_status   TEXT NOT NULL DEFAULT '',    -- '' | retry | sent | failed | not_configured
    alert_attempts INTEGER NOT NULL DEFAULT 0,  -- Telegram send attempts for the missed-dose alert
    UNIQUE (medicine_id, day, time)
);

-- Everything that happened to a dose, for the monthly log:
-- taken | taken_late | duplicate | too_close | too_early | missed
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id INTEGER NOT NULL REFERENCES elders(id),
    dose_id  INTEGER NOT NULL REFERENCES doses(id),
    day      TEXT NOT NULL,                     -- the dose's day, YYYY-MM-DD
    at       TEXT NOT NULL,                     -- when it happened, YYYY-MM-DD HH:MM:SS
    kind     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_day ON events (elder_id, day);

-- Medicine prices reported by users at a pharmacy (pharmacies come from OpenStreetMap).
CREATE TABLE IF NOT EXISTS prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    osm_id      TEXT NOT NULL,                  -- e.g. "node/123456" in OpenStreetMap
    pharmacy    TEXT NOT NULL,
    medicine    TEXT NOT NULL,                  -- normalised: lower case, single spaces
    label       TEXT NOT NULL,                  -- as typed, for display
    price       INTEGER NOT NULL,               -- Mongolian tögrög (₮)
    reported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_by_medicine ON prices (medicine, osm_id);
"""

# No 0/O or 1/I/L so codes are easy to read aloud and type.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8          # 31^8 ≈ 850 billion codes: not guessable (older profiles keep their 6-letter code)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def valid_timezone(name):
    """True if `name` is a real IANA time zone such as 'Asia/Ulaanbaatar'."""
    if not name or "/" not in name and name != "UTC":
        return False
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def timezone_of(elder):
    """The elder's own time zone, or the app default if none is saved."""
    tz = elder["timezone"] if elder is not None and "timezone" in elder.keys() else ""
    return tz if valid_timezone(tz) else current_app.config["TIMEZONE"]


def now(tz_name=None):
    """Current wall-clock time in `tz_name` (default: the app's time zone), without tzinfo.

    Every elder has their own time zone, so "08:00" always means 08:00 where
    the person taking the medicine actually is.
    """
    tz = ZoneInfo(tz_name if valid_timezone(tz_name) else current_app.config["TIMEZONE"])
    return datetime.now(tz).replace(tzinfo=None, microsecond=0)


def elder_now(elder_id):
    elder = get_db().execute("SELECT * FROM elders WHERE id = ?", (elder_id,)).fetchone()
    return now(timezone_of(elder))


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    for migration in ("ALTER TABLE doses ADD COLUMN hold_until TEXT",
                      "ALTER TABLE elders ADD COLUMN timezone TEXT NOT NULL DEFAULT ''",
                      "ALTER TABLE elders ADD COLUMN language TEXT NOT NULL DEFAULT 'en'",
                      "ALTER TABLE doses ADD COLUMN alert_status TEXT NOT NULL DEFAULT ''",
                      "ALTER TABLE doses ADD COLUMN alert_attempts INTEGER NOT NULL DEFAULT 0",
                      "ALTER TABLE elders ADD COLUMN pin_hash TEXT NOT NULL DEFAULT ''",
                      "ALTER TABLE elders ADD COLUMN pin_fails INTEGER NOT NULL DEFAULT 0",
                      "ALTER TABLE elders ADD COLUMN pin_locked_until TEXT NOT NULL DEFAULT ''"):
        try:  # databases created before these columns existed
            db.execute(migration)
        except sqlite3.OperationalError:
            pass
    db.commit()


class TimeFormatError(ValueError):
    """A bad time like "8am". Carries a translation key so the message can be shown in any language."""
    def __init__(self, key, **params):
        super().__init__(translate.t("en", key, **params))
        self.key, self.params = key, params

    def message(self, lang):
        return translate.t(lang, self.key, **self.params)


def ui_t():
    """Translator for the language of the current page (English outside a request)."""
    return translate.translator(getattr(g, "lang", None) or translate.DEFAULT)


def parse_times(text):
    """Turn "8:00, 20:00" into ["08:00", "20:00"]. Raises ValueError."""
    times = set()
    for part in text.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            times.add(datetime.strptime(part, "%H:%M").strftime("%H:%M"))
        except ValueError:
            raise TimeFormatError("err.bad_time", part=part)
    if not times:
        raise TimeFormatError("err.no_time")
    return sorted(times)


def scheduled_datetime(dose):
    return datetime.strptime(f"{dose['day']} {dose['time']}", "%Y-%m-%d %H:%M")


def settings():
    cfg = current_app.config
    return rules.Settings(cfg["EARLY_MINUTES"], cfg["GRACE_MINUTES"], cfg["GAP_RATIO"])


def _taken_dt(dose):
    return datetime.fromisoformat(dose["taken_at"]) if dose["taken_at"] else None


def _hold_dt(dose):
    return datetime.fromisoformat(dose["hold_until"]) if dose["hold_until"] else None


def dose_status_reason(dose, current):
    """(status, reason) from the shared rules in rules.py."""
    return rules.dose_status(scheduled_datetime(dose), _taken_dt(dose), current, settings(), _hold_dt(dose), ui_t())


def dose_status(dose, current):
    return dose_status_reason(dose, current)[0]


# --------------------------------------------------------------------------- #
# Elders (one profile = one person taking medicine + their caregiver)
# --------------------------------------------------------------------------- #
def create_elder(name, caregiver_name="", timezone="", language="en"):
    language = translate.normalize(language) or translate.DEFAULT
    timezone = timezone if valid_timezone(timezone) else ""
    db = get_db()
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        try:
            db.execute(
                "INSERT INTO elders (name, caregiver_name, code, timezone, language, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, caregiver_name, code, timezone, language, now(timezone).isoformat(sep=" ")),
            )
            db.commit()
            return code
        except sqlite3.IntegrityError:  # code clash, try another
            continue


def get_elder(code):
    return get_db().execute(
        "SELECT * FROM elders WHERE code = ?", ((code or "").strip().upper(),)
    ).fetchone()


def update_settings(elder_id, name, caregiver_name, telegram_chat_id, timezone, language):
    db = get_db()
    db.execute(
        "UPDATE elders SET name = ?, caregiver_name = ?, telegram_chat_id = ?, timezone = ?, language = ? WHERE id = ?",
        (name, caregiver_name, telegram_chat_id.strip(), timezone, translate.normalize(language) or translate.DEFAULT, elder_id),
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Medicines
# --------------------------------------------------------------------------- #
def list_medicines(elder_id):
    return get_db().execute(
        "SELECT * FROM medicines WHERE elder_id = ? AND active = 1 ORDER BY name", (elder_id,)
    ).fetchall()


def add_medicine(elder_id, name, dose, times, note=""):
    db = get_db()
    db.execute(
        "INSERT INTO medicines (elder_id, name, dose, times, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (elder_id, name, dose, ",".join(times), note, elder_now(elder_id).strftime("%Y-%m-%d %H:%M")),
    )
    db.commit()


def remove_medicine(elder_id, medicine_id):
    """Stop a medicine. Past history is kept; untaken doses from today on are removed."""
    db = get_db()
    db.execute("UPDATE medicines SET active = 0 WHERE id = ? AND elder_id = ?", (medicine_id, elder_id))
    db.execute(
        "DELETE FROM doses WHERE medicine_id = ? AND taken_at IS NULL AND day >= ?",
        (medicine_id, elder_now(elder_id).date().isoformat()),
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Doses
# --------------------------------------------------------------------------- #
def ensure_doses(elder_id, day):
    """Create the dose rows for `day` from each active medicine's schedule.

    Times before the medicine was added are skipped, so adding a medicine at
    15:00 does not immediately create a "missed" 08:00 dose.
    """
    db = get_db()
    for med in list_medicines(elder_id):
        for t in med["times"].split(","):
            if f"{day} {t}" < med["created_at"]:
                continue
            db.execute(
                "INSERT OR IGNORE INTO doses (medicine_id, day, time) VALUES (?, ?, ?)",
                (med["id"], day, t),
            )
    db.commit()


def _dose_dict(row, current):
    status, reason = dose_status_reason(row, current)
    return {
        "id": row["id"],
        "name": row["name"],
        "dose": row["dose"],
        "note": row["note"],
        "time": row["time"],
        "status": status,
        "reason": reason,
        "taken_at": row["taken_at"][11:16] if row["taken_at"] else None,
        "late": status == "taken_late",
        "hold_until": row["hold_until"][11:16] if row["hold_until"] else None,
        "alerted": row["alert_status"] == "sent",
        "alert_status": row["alert_status"],      # '' | retry | sent | failed | not_configured
    }


def today_doses(elder_id):
    current = elder_now(elder_id)
    day = current.date().isoformat()
    ensure_doses(elder_id, day)
    rows = get_db().execute(
        """SELECT d.*, m.name, m.dose, m.note
           FROM doses d JOIN medicines m ON m.id = d.medicine_id
           WHERE m.elder_id = ? AND d.day = ?
           ORDER BY d.time, m.name""",
        (elder_id, day),
    ).fetchall()
    return [_dose_dict(r, current) for r in rows]


def log_event(elder_id, dose_id, day, at, kind):
    get_db().execute("INSERT INTO events (elder_id, dose_id, day, at, kind) VALUES (?, ?, ?, ?, ?)",
                     (elder_id, dose_id, day, at.isoformat(sep=" ") if hasattr(at, "isoformat") else at, kind))


def take_dose(elder_id, dose_id):
    """Record that a dose was taken, using the double-dose rules in rules.py.

    Returns (result, dose, decision); result is one of
      "ok" | "duplicate" | "too_close" | "too_early" | "not_found"
    """
    db = get_db()
    query = """SELECT d.*, m.name, m.dose, m.note, m.times AS med_times
               FROM doses d JOIN medicines m ON m.id = d.medicine_id
               WHERE d.id = ? AND m.elder_id = ?"""
    row = db.execute(query, (dose_id, elder_id)).fetchone()
    if row is None:
        return "not_found", None, None
    current = elder_now(elder_id)

    # Latest confirmation of any OTHER dose of the same medicine (also yesterday's).
    last = db.execute(
        "SELECT MAX(taken_at) FROM doses WHERE medicine_id = ? AND id != ? AND taken_at IS NOT NULL",
        (row["medicine_id"], dose_id),
    ).fetchone()[0]
    last_taken = datetime.fromisoformat(last) if last else None
    gap, _ = rules.min_gap(row["med_times"].split(","), settings())

    decision = rules.check_confirmation(scheduled_datetime(row), _taken_dt(row), current,
                                         last_taken, gap, settings(), row["name"], ui_t())
    if decision.result == "too_close":
        # The elder is told to wait, so pause the missed-dose clock until the allowed time.
        db.execute("UPDATE doses SET hold_until = ? WHERE id = ?",
                   (decision.allowed_after.isoformat(sep=" "), dose_id))
        db.commit()
        row = db.execute(query, (dose_id, elder_id)).fetchone()
    if decision.result != "ok":
        log_event(elder_id, dose_id, row["day"], current, decision.result)   # blocked attempts are logged too
        db.commit()
        return decision.result, _dose_dict(row, current), decision

    # "AND taken_at IS NULL" makes this safe if two devices tap at the same moment:
    # only one UPDATE can win; the other gets rowcount 0 and is treated as a duplicate.
    cur = db.execute(
        "UPDATE doses SET taken_at = ? WHERE id = ? AND taken_at IS NULL",
        (current.isoformat(sep=" "), dose_id),
    )
    db.commit()
    row = db.execute(query, (dose_id, elder_id)).fetchone()
    if cur.rowcount != 1:
        decision = rules.check_confirmation(scheduled_datetime(row), _taken_dt(row), current,
                                             last_taken, gap, settings(), row["name"], ui_t())
    dose = _dose_dict(row, current)
    log_event(elder_id, dose_id, row["day"], current,
              decision.result if decision.result != "ok" else dose["status"])   # taken | taken_late
    db.commit()
    return decision.result, dose, decision


ALERT_MAX_TRIES = 5      # a failed Telegram send is retried every minute, up to this many times


def find_new_missed():
    """Find missed doses whose caregiver alert still has to be sent (or re-sent).

    This only FINDS them. Nothing is marked as alerted here: services/alerts.py
    records the result only after Telegram has answered (see record_alert).
    """
    db = get_db()
    missed = []
    # Each elder is checked against the clock where THEY live.
    for elder in db.execute("SELECT * FROM elders").fetchall():
        current = now(timezone_of(elder))
        today = current.date()
        ensure_doses(elder["id"], today.isoformat())
        rows = db.execute(
            """SELECT d.*, m.name, m.dose, e.name AS elder_name, e.code,
                      e.caregiver_name, e.telegram_chat_id, e.language
               FROM doses d
               JOIN medicines m ON m.id = d.medicine_id
               JOIN elders e ON e.id = m.elder_id
               WHERE e.id = ? AND d.taken_at IS NULL AND d.alerted_at IS NULL
                 AND d.alert_status IN ('', 'retry') AND d.day >= ?""",
            (elder["id"], (today - timedelta(days=1)).isoformat()),
        ).fetchall()
        for r in rows:
            if dose_status(r, current) == "missed":
                if r["alert_status"] == "":                # first time we see it: log the missed dose once
                    log_event(elder["id"], r["id"], r["day"], current, "missed")
                missed.append({**dict(r), "now": current})
    db.commit()
    return missed


def record_alert(dose, outcome):
    """Save what really happened to a missed-dose alert.

    outcome: "sent"           Telegram confirmed it          -> done
             "not_configured" no bot token or chat ID        -> done, dashboard says so
             "failed"         Telegram/network error         -> retried next minute,
                                                                 marked "failed" after ALERT_MAX_TRIES
    """
    db = get_db()
    at = dose["now"].isoformat(sep=" ")
    tries = dose["alert_attempts"] + 1
    if outcome == "failed" and tries < ALERT_MAX_TRIES:
        status, done = "retry", False
    else:
        status, done = outcome, True
    db.execute("UPDATE doses SET alert_status = ?, alert_attempts = ?, alerted_at = ? WHERE id = ?",
               (status, tries, at if done else None, dose["id"]))
    kind = {"sent": "alert_sent", "not_configured": "alert_off"}.get(outcome, "alert_failed")
    if outcome != "failed" or done:                         # log retries only once, at the end
        log_event(_elder_id_of_dose(dose["id"]), dose["id"], dose["day"], at, kind)
    db.commit()
    return status


def _elder_id_of_dose(dose_id):
    return get_db().execute(
        "SELECT m.elder_id FROM doses d JOIN medicines m ON m.id = d.medicine_id WHERE d.id = ?", (dose_id,)
    ).fetchone()[0]


def history(elder_id, days=7):
    """Taken / scheduled counts per day for the last `days` days (today = doses due so far)."""
    db = get_db()
    current = elder_now(elder_id)
    out = []
    for i in range(days - 1, -1, -1):
        day = (current.date() - timedelta(days=i)).isoformat()
        rows = db.execute(
            """SELECT d.* FROM doses d JOIN medicines m ON m.id = d.medicine_id
               WHERE m.elder_id = ? AND d.day = ?""",
            (elder_id, day),
        ).fetchall()
        counted = [r for r in rows if dose_status(r, current) not in ("upcoming", "open", "on_hold")]
        taken = sum(1 for r in counted if r["taken_at"])
        out.append({"day": day, "taken": taken, "total": len(counted)})
    return out


# --------------------------------------------------------------------------- #
# Monthly log
# --------------------------------------------------------------------------- #
def month_log(elder_id, year, month):
    """Every dose and event of one month, day by day, plus a summary.

    Each day: {"day", "doses": [...], "events": [...], "taken", "total", "late", "missed"}
    Doses still to come (today later, future days) are not counted.
    """
    import calendar
    db = get_db()
    current = elder_now(elder_id)
    first = f"{year:04d}-{month:02d}-01"
    last = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    if first <= current.date().isoformat() <= last:
        ensure_doses(elder_id, current.date().isoformat())

    rows = db.execute(
        """SELECT d.*, m.name, m.dose FROM doses d JOIN medicines m ON m.id = d.medicine_id
           WHERE m.elder_id = ? AND d.day BETWEEN ? AND ? ORDER BY d.day, d.time, m.name""",
        (elder_id, first, last)).fetchall()
    events = db.execute(
        """SELECT e.*, d.time, m.name FROM events e
           JOIN doses d ON d.id = e.dose_id JOIN medicines m ON m.id = d.medicine_id
           WHERE e.elder_id = ? AND e.day BETWEEN ? AND ? ORDER BY e.at""",
        (elder_id, first, last)).fetchall()

    days = {}
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        key = f"{year:04d}-{month:02d}-{d:02d}"
        days[key] = {"day": key, "doses": [], "events": [], "taken": 0, "total": 0, "late": 0, "missed": 0}
    summary = {"scheduled": 0, "taken": 0, "on_time": 0, "late": 0, "missed": 0, "blocked": 0}

    for r in rows:
        status = dose_status(r, current)
        days[r["day"]]["doses"].append({
            "time": r["time"], "name": r["name"], "dose": r["dose"], "status": status,
            "taken_at": r["taken_at"][11:16] if r["taken_at"] else None,
        })
        if status in ("upcoming", "open", "on_hold", "due"):
            continue                          # not decided yet
        day = days[r["day"]]
        day["total"] += 1
        summary["scheduled"] += 1
        if status == "taken":
            day["taken"] += 1; summary["taken"] += 1; summary["on_time"] += 1
        elif status == "taken_late":
            day["taken"] += 1; day["late"] += 1; summary["taken"] += 1; summary["late"] += 1
        else:
            day["missed"] += 1; summary["missed"] += 1

    for e in events:
        days[e["day"]]["events"].append({"at": e["at"][11:16], "kind": e["kind"], "time": e["time"], "name": e["name"]})
        if e["kind"] in ("duplicate", "too_close"):
            summary["blocked"] += 1

    summary["adherence"] = round(100 * summary["taken"] / summary["scheduled"]) if summary["scheduled"] else None
    return {"month": f"{year:04d}-{month:02d}", "today": current.date().isoformat(),
            "days": list(days.values()), "summary": summary}


def fill_demo_month(elder_id, days=30, seed=7):
    """Fill past days with realistic demo history (for showing the monthly log).

    Uses each active medicine's schedule. Most doses on time, some late, a few
    missed, and a few blocked double-dose attempts. Days that already have data are left alone.
    """
    import random
    rnd = random.Random(seed + elder_id)
    db = get_db()
    today = elder_now(elder_id).date()
    meds = list_medicines(elder_id)
    for i in range(days, 0, -1):
        day = (today - timedelta(days=i)).isoformat()
        for med in meds:
            for t in med["times"].split(","):
                if db.execute("SELECT 1 FROM doses WHERE medicine_id = ? AND day = ? AND time = ?",
                              (med["id"], day, t)).fetchone():
                    continue
                scheduled = datetime.fromisoformat(f"{day} {t}")
                roll = rnd.random()
                taken = alerted = None
                if roll < 0.78:                                   # on time
                    taken = scheduled + timedelta(minutes=rnd.randint(-10, 25))
                elif roll < 0.90:                                 # late, after the alert
                    alerted = scheduled + timedelta(minutes=settings().grace_minutes)
                    taken = alerted + timedelta(minutes=rnd.randint(5, 90))
                else:                                             # missed
                    alerted = scheduled + timedelta(minutes=settings().grace_minutes)
                cur = db.execute(
                    "INSERT INTO doses (medicine_id, day, time, taken_at, alerted_at, alert_status, alert_attempts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (med["id"], day, t, taken.isoformat(sep=" ") if taken else None,
                     alerted.isoformat(sep=" ") if alerted else None, "sent" if alerted else "", 1 if alerted else 0))
                dose_id = cur.lastrowid
                if alerted:
                    log_event(elder_id, dose_id, day, alerted, "missed")
                    log_event(elder_id, dose_id, day, alerted, "alert_sent")
                if taken:
                    log_event(elder_id, dose_id, day, taken, "taken_late" if alerted else "taken")
                    if rnd.random() < 0.06:                       # forgot and pressed again
                        log_event(elder_id, dose_id, day, taken + timedelta(minutes=rnd.randint(30, 120)), "duplicate")
    db.commit()


# --------------------------------------------------------------------------- #
# Medicine prices (reported by users)
# --------------------------------------------------------------------------- #
import re as _re

OSM_ID = _re.compile(r"^(node|way|relation)/\d{1,15}$")
PRICE_DAYS = 90          # only reports from the last 90 days count


def normalise_medicine(name):
    """'  Metformin   500mg ' -> 'metformin 500mg' (so different spellings of spaces/case match)."""
    return " ".join((name or "").lower().split())


def add_price(osm_id, pharmacy, medicine, price):
    """Store one price report. Raises ValueError with a translation key if something is wrong."""
    if not OSM_ID.match(osm_id or ""):
        raise ValueError("ph.err_pharmacy")
    label = " ".join((medicine or "").split())
    if not 2 <= len(label) <= 60:
        raise ValueError("ph.err_medicine")
    try:
        price = int(price)
    except (TypeError, ValueError):
        raise ValueError("ph.err_price")
    if not 1 <= price <= 10_000_000:
        raise ValueError("ph.err_price")
    db = get_db()
    db.execute("INSERT INTO prices (osm_id, pharmacy, medicine, label, price, reported_at) VALUES (?, ?, ?, ?, ?, ?)",
               (osm_id, (pharmacy or "")[:80], normalise_medicine(label), label, price,
                datetime.utcnow().isoformat(sep=" ", timespec="seconds")))
    db.commit()


def cheapest_prices(medicine, osm_ids):
    """For one medicine: the cheapest recent report per pharmacy.

    Returns {osm_id: {"price", "reported_at", "reports"}} for the pharmacies in osm_ids.
    """
    med = normalise_medicine(medicine)
    ids = [i for i in osm_ids if OSM_ID.match(i)][:200]
    if not med or not ids:
        return {}
    since = (datetime.utcnow() - timedelta(days=PRICE_DAYS)).isoformat(sep=" ", timespec="seconds")
    marks = ",".join("?" * len(ids))
    rows = get_db().execute(
        f"""SELECT osm_id, MIN(price) AS price, MAX(reported_at) AS reported_at, COUNT(*) AS reports
            FROM prices WHERE medicine = ? AND reported_at >= ? AND osm_id IN ({marks})
            GROUP BY osm_id""", (med, since, *ids)).fetchall()
    return {r["osm_id"]: {"price": r["price"], "reported_at": r["reported_at"], "reports": r["reports"]} for r in rows}


# --------------------------------------------------------------------------- #
# Privacy: caregiver PIN, data export, deleting a profile
# --------------------------------------------------------------------------- #
PIN_RE = _re.compile(r"^\d{6}$")
PIN_MAX_FAILS = 5        # wrong tries before a pause
PIN_LOCK_MINUTES = 15


def valid_pin(pin):
    return bool(PIN_RE.match(pin or ""))


def set_pin(elder_id, pin):
    """Save the caregiver PIN as a salted hash (the PIN itself is never stored)."""
    if not valid_pin(pin):
        raise ValueError("pin.err_format")
    db = get_db()
    db.execute("UPDATE elders SET pin_hash = ?, pin_fails = 0, pin_locked_until = '' WHERE id = ?",
               (generate_password_hash(pin), elder_id))
    db.commit()


def check_pin(elder_id, pin):
    """Returns ("ok", 0) | ("wrong", tries_left) | ("locked", minutes_left).

    After PIN_MAX_FAILS wrong tries the profile is locked for PIN_LOCK_MINUTES,
    so a 6-digit PIN can't be guessed by trying them all.
    """
    db = get_db()
    row = db.execute("SELECT pin_hash, pin_fails, pin_locked_until FROM elders WHERE id = ?", (elder_id,)).fetchone()
    utc = datetime.utcnow()
    if row["pin_locked_until"]:
        until = datetime.fromisoformat(row["pin_locked_until"])
        if utc < until:
            return "locked", max(1, int((until - utc).total_seconds() // 60) + 1)
    if row["pin_hash"] and check_password_hash(row["pin_hash"], pin or ""):
        db.execute("UPDATE elders SET pin_fails = 0, pin_locked_until = '' WHERE id = ?", (elder_id,))
        db.commit()
        return "ok", 0
    fails = row["pin_fails"] + 1
    if fails >= PIN_MAX_FAILS:
        until = (utc + timedelta(minutes=PIN_LOCK_MINUTES)).isoformat(sep=" ", timespec="seconds")
        db.execute("UPDATE elders SET pin_fails = 0, pin_locked_until = ? WHERE id = ?", (until, elder_id))
        db.commit()
        return "locked", PIN_LOCK_MINUTES
    db.execute("UPDATE elders SET pin_fails = ? WHERE id = ?", (fails, elder_id))
    db.commit()
    return "wrong", PIN_MAX_FAILS - fails


def export_elder(elder_id):
    """Everything stored about one profile, for "Download all data". The PIN hash is left out."""
    db = get_db()
    rows = lambda sql: [dict(r) for r in db.execute(sql, (elder_id,)).fetchall()]  # noqa: E731
    elder = dict(db.execute("SELECT * FROM elders WHERE id = ?", (elder_id,)).fetchone())
    for private in ("pin_hash", "pin_fails", "pin_locked_until"):
        elder.pop(private, None)
    return {
        "exported_at": datetime.utcnow().isoformat(sep=" ", timespec="seconds") + " UTC",
        "profile": elder,
        "medicines": rows("SELECT * FROM medicines WHERE elder_id = ?"),
        "doses": rows("""SELECT d.*, m.name AS medicine FROM doses d JOIN medicines m ON m.id = d.medicine_id
                         WHERE m.elder_id = ? ORDER BY d.day, d.time"""),
        "events": rows("SELECT * FROM events WHERE elder_id = ? ORDER BY at"),
    }


def delete_elder(elder_id):
    """Permanently delete a profile and its whole history. (Price reports are anonymous and stay.)"""
    db = get_db()
    db.execute("DELETE FROM events WHERE elder_id = ?", (elder_id,))
    db.execute("DELETE FROM doses WHERE medicine_id IN (SELECT id FROM medicines WHERE elder_id = ?)", (elder_id,))
    db.execute("DELETE FROM medicines WHERE elder_id = ?", (elder_id,))
    db.execute("DELETE FROM elders WHERE id = ?", (elder_id,))
    db.commit()
