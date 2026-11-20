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
from zoneinfo import ZoneInfo

from flask import current_app, g

import engine

SCHEMA = """
CREATE TABLE IF NOT EXISTS elders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    caregiver_name   TEXT NOT NULL DEFAULT '',
    code             TEXT NOT NULL UNIQUE,      -- share code used in URLs
    telegram_chat_id TEXT NOT NULL DEFAULT '',
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
    hold_until  TEXT,                           -- set by the too-close check: safe time
    UNIQUE (medicine_id, day, time)
);
"""

# No 0/O or 1/I/L so codes are easy to read aloud and type.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def now():
    """Current local time in the app's time zone, without tzinfo."""
    tz = ZoneInfo(current_app.config["TIMEZONE"])
    return datetime.now(tz).replace(tzinfo=None, microsecond=0)


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
    try:  # databases created before hold_until existed
        db.execute("ALTER TABLE doses ADD COLUMN hold_until TEXT")
    except sqlite3.OperationalError:
        pass
    db.commit()


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
            raise ValueError(f"“{part}” is not a time. Use 24-hour format like 08:00 or 20:30.")
    if not times:
        raise ValueError("Add at least one time, like 08:00.")
    return sorted(times)


def scheduled_datetime(dose):
    return datetime.strptime(f"{dose['day']} {dose['time']}", "%Y-%m-%d %H:%M")


def settings():
    cfg = current_app.config
    return engine.Settings(cfg["EARLY_MINUTES"], cfg["GRACE_MINUTES"], cfg["GAP_RATIO"])


def _taken_dt(dose):
    return datetime.fromisoformat(dose["taken_at"]) if dose["taken_at"] else None


def _hold_dt(dose):
    return datetime.fromisoformat(dose["hold_until"]) if dose["hold_until"] else None


def dose_status_reason(dose, current):
    """(status, reason) from the shared rules in engine.py."""
    return engine.dose_status(scheduled_datetime(dose), _taken_dt(dose), current, settings(), _hold_dt(dose))


def dose_status(dose, current):
    return dose_status_reason(dose, current)[0]


# --------------------------------------------------------------------------- #
# Elders (one profile = one person taking medicine + their caregiver)
# --------------------------------------------------------------------------- #
def create_elder(name, caregiver_name=""):
    db = get_db()
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        try:
            db.execute(
                "INSERT INTO elders (name, caregiver_name, code, created_at) VALUES (?, ?, ?, ?)",
                (name, caregiver_name, code, now().isoformat(sep=" ")),
            )
            db.commit()
            return code
        except sqlite3.IntegrityError:  # code clash, try another
            continue


def get_elder(code):
    return get_db().execute(
        "SELECT * FROM elders WHERE code = ?", ((code or "").strip().upper(),)
    ).fetchone()


def update_settings(elder_id, name, caregiver_name, telegram_chat_id):
    db = get_db()
    db.execute(
        "UPDATE elders SET name = ?, caregiver_name = ?, telegram_chat_id = ? WHERE id = ?",
        (name, caregiver_name, telegram_chat_id.strip(), elder_id),
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
        (elder_id, name, dose, ",".join(times), note, now().strftime("%Y-%m-%d %H:%M")),
    )
    db.commit()


def remove_medicine(elder_id, medicine_id):
    """Stop a medicine. Past history is kept; untaken doses from today on are removed."""
    db = get_db()
    db.execute("UPDATE medicines SET active = 0 WHERE id = ? AND elder_id = ?", (medicine_id, elder_id))
    db.execute(
        "DELETE FROM doses WHERE medicine_id = ? AND taken_at IS NULL AND day >= ?",
        (medicine_id, now().date().isoformat()),
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
        "alerted": bool(row["alerted_at"]),
    }


def today_doses(elder_id):
    current = now()
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


def take_dose(elder_id, dose_id):
    """Record that a dose was taken, using the double-dose rules in engine.py.

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
    current = now()

    # Latest confirmation of any OTHER dose of the same medicine (also yesterday's).
    last = db.execute(
        "SELECT MAX(taken_at) FROM doses WHERE medicine_id = ? AND id != ? AND taken_at IS NOT NULL",
        (row["medicine_id"], dose_id),
    ).fetchone()[0]
    last_taken = datetime.fromisoformat(last) if last else None
    gap, _ = engine.min_gap(row["med_times"].split(","), settings())

    decision = engine.check_confirmation(scheduled_datetime(row), _taken_dt(row), current,
                                         last_taken, gap, settings(), row["name"])
    if decision.result == "too_close":
        # The elder is told to wait, so pause the missed-dose clock until the safe time.
        db.execute("UPDATE doses SET hold_until = ? WHERE id = ?",
                   (decision.safe_after.isoformat(sep=" "), dose_id))
        db.commit()
        row = db.execute(query, (dose_id, elder_id)).fetchone()
    if decision.result != "ok":
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
        decision = engine.check_confirmation(scheduled_datetime(row), _taken_dt(row), current,
                                             last_taken, gap, settings(), row["name"])
    return decision.result, _dose_dict(row, current), decision


def find_new_missed():
    """Find doses past their grace period that nobody has been alerted about,
    mark them as alerted, and return them (with elder info for the message)."""
    db = get_db()
    current = now()
    today = current.date()
    for elder in db.execute("SELECT id FROM elders").fetchall():
        ensure_doses(elder["id"], today.isoformat())

    rows = db.execute(
        """SELECT d.*, m.name, m.dose, e.name AS elder_name, e.code,
                  e.caregiver_name, e.telegram_chat_id
           FROM doses d
           JOIN medicines m ON m.id = d.medicine_id
           JOIN elders e ON e.id = m.elder_id
           WHERE d.taken_at IS NULL AND d.alerted_at IS NULL AND d.day >= ?""",
        ((today - timedelta(days=1)).isoformat(),),
    ).fetchall()

    missed = [dict(r) for r in rows if dose_status(r, current) == "missed"]
    for r in missed:
        db.execute("UPDATE doses SET alerted_at = ? WHERE id = ?", (current.isoformat(sep=" "), r["id"]))
    db.commit()
    return missed


def history(elder_id, days=7):
    """Taken / scheduled counts per day for the last `days` days (today = doses due so far)."""
    db = get_db()
    current = now()
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
