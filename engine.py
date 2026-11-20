"""Sanakh decision engine: the rules, as pure functions.

No database, no Flask, no clock. Every function gets the times it needs as
arguments, so the SAME rules run in the live app (models.py) and in the
interactive simulator (/simulator). Every decision comes with a plain-language
reason, so the algorithm can always explain itself.

The four rules
--------------
1. MISSED DOSE DETECTION
   Each dose moves through states as time passes:
       upcoming -> open (EARLY min before) -> due (at its time) -> missed (after GRACE min)
   A dose is "missed" when  now > deadline  and it has not been confirmed, where
       deadline = scheduled + GRACE
   If the dose was put ON HOLD by the too-close check (rule 2b), the elder was told
   to wait, so the deadline moves to  safe_time + GRACE  (no false "missed" alert).

2. DOUBLE DOSE DETECTION, two checks when "I took it" is pressed:
   a. duplicate : this exact dose was already confirmed -> blocked
   b. too close : the same medicine was confirmed less than MIN_GAP ago -> blocked
      MIN_GAP = GAP_RATIO x (shortest interval between this medicine's doses)
      e.g. 08:00 and 20:00 -> interval 12 h -> min gap 6 h (with ratio 0.5)
   (A confirmation more than EARLY min before the dose time is "too early".)

3. CAREGIVER ALERTS are sent when
   - a dose becomes missed (once per dose),
   - a double dose is attempted (duplicate or too close),
   - a missed dose is taken late (follow-up so the family can relax).

4. EXPLANATIONS: every status and decision returns a reason string.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Settings:
    early_minutes: int = 30     # confirmation allowed from this long before the dose time
    grace_minutes: int = 30     # after the dose time, wait this long before "missed"
    gap_ratio: float = 0.5      # min gap between doses = ratio x shortest dose interval


@dataclass
class Decision:
    result: str                     # ok | duplicate | too_early | too_close
    reason: str
    safe_after: datetime = None     # for too_close: when the next dose is safe
    alert_caregiver: bool = False


RESULT_LABELS = {
    "ok": "Recorded",
    "duplicate": "Blocked: already taken",
    "too_close": "Blocked: too soon after last dose",
    "too_early": "Blocked: too early",
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def hm(dt):
    return dt.strftime("%H:%M")


def minutes_of(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def duration(minutes):
    """150 -> '2 h 30 min'"""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


# --------------------------------------------------------------------------- #
# Rule 2b helper: minimum gap between doses of one medicine
# --------------------------------------------------------------------------- #
def min_gap(times, settings):
    """Return (gap_minutes, shortest_interval_minutes) for a medicine's daily times.

    The interval wraps around midnight: 08:00 and 20:00 -> 12 h both ways.
    A once-a-day medicine has a 24 h interval.
    """
    mins = sorted({minutes_of(t) for t in times})
    if len(mins) <= 1:
        interval = 24 * 60
    else:
        interval = min(b - a for a, b in zip(mins, mins[1:] + [mins[0] + 24 * 60]))
    return round(interval * settings.gap_ratio), interval


def gap_reason(times, settings):
    gap, interval = min_gap(times, settings)
    pct = round(settings.gap_ratio * 100)
    if gap == 0:
        return "Too-close check is off (gap ratio 0%)."
    return (f"Shortest time between scheduled doses is {duration(interval)}; "
            f"{pct}% of that = at least {duration(gap)} between confirmations.")


# --------------------------------------------------------------------------- #
# Rule 1: status of a dose at a given moment
# --------------------------------------------------------------------------- #
def deadline_of(scheduled, settings, hold_until=None):
    """When an unconfirmed dose becomes missed."""
    deadline = scheduled + timedelta(minutes=settings.grace_minutes)
    if hold_until is not None:
        deadline = max(deadline, hold_until + timedelta(minutes=settings.grace_minutes))
    return deadline


def dose_status(scheduled, taken_at, now, settings, hold_until=None):
    """Return (status, reason).

    status: upcoming | open | on_hold | due | missed | taken | taken_late
    hold_until: set when a confirmation was blocked as "too close" (safe time).
    """
    opens = scheduled - timedelta(minutes=settings.early_minutes)
    deadline = deadline_of(scheduled, settings, hold_until)

    if taken_at is not None and taken_at <= now:
        if taken_at <= deadline:
            return "taken", f"Confirmed at {hm(taken_at)}, before the {hm(deadline)} deadline."
        return "taken_late", (f"Confirmed at {hm(taken_at)}, after the {hm(deadline)} deadline. "
                              "The caregiver was alerted, then told it was taken.")
    if now < opens:
        return "upcoming", (f"Not yet. It can be confirmed from {hm(opens)} "
                            f"({settings.early_minutes} min before {hm(scheduled)}).")
    if hold_until is not None and now < hold_until:
        return "on_hold", (f"Paused for safety: the previous dose was too recent. "
                           f"Safe from {hm(hold_until)}; counts as missed only if not confirmed by {hm(deadline)}.")
    if now < scheduled:
        return "open", f"Can be confirmed now. The spoken reminder starts at {hm(scheduled)}."
    base = hm(hold_until) if hold_until is not None and hold_until > scheduled else hm(scheduled)
    if now <= deadline:
        return "due", (f"Time reached and not confirmed. Reminding until {hm(deadline)} "
                       f"({base} + {settings.grace_minutes} min grace).")
    return "missed", (f"Not confirmed by {hm(deadline)} "
                      f"({base} + {settings.grace_minutes} min grace). Caregiver alerted.")


# --------------------------------------------------------------------------- #
# Rule 2: should a confirmation be accepted?
# --------------------------------------------------------------------------- #
def check_confirmation(scheduled, taken_at, now, last_taken, gap_minutes, settings, medicine="this medicine"):
    """Decide what happens when "I took it" is pressed at `now`.

    scheduled   : datetime of the dose being confirmed
    taken_at    : when THIS dose was already confirmed, or None
    last_taken  : latest confirmation of ANY other dose of the same medicine, or None
    gap_minutes : minimum allowed time between two confirmations (see min_gap)
    """
    if taken_at is not None:
        return Decision(
            "duplicate",
            f"The {hm(scheduled)} {medicine} dose was already confirmed at {hm(taken_at)}. "
            "Confirming it again would be a double dose.",
            alert_caregiver=True,
        )

    opens = scheduled - timedelta(minutes=settings.early_minutes)
    if now < opens:
        return Decision(
            "too_early",
            f"The {hm(scheduled)} dose can only be confirmed from {hm(opens)}.",
        )

    if last_taken is not None and gap_minutes > 0:
        since = now - last_taken
        if since < timedelta(minutes=gap_minutes):
            safe = last_taken + timedelta(minutes=gap_minutes)
            return Decision(
                "too_close",
                f"{medicine} was last taken at {hm(last_taken)}, only "
                f"{duration(since.total_seconds() // 60)} ago. Doses must be at least "
                f"{duration(gap_minutes)} apart, so this one is safe after {hm(safe)}.",
                safe_after=safe,
                alert_caregiver=True,
            )

    if last_taken is None:
        why = "no earlier dose recorded"
    else:
        why = f"last dose was at {hm(last_taken)}, more than {duration(gap_minutes)} ago"
    return Decision("ok", f"Recorded at {hm(now)}: not taken yet, window open, {why}.")


# --------------------------------------------------------------------------- #
# Rule 3: caregiver alert texts
# --------------------------------------------------------------------------- #
def alert_text(kind, elder, medicine, dose_time, at=None, extra=""):
    if kind == "missed":
        return f"⚠️ {elder} has not confirmed {medicine} (due {dose_time}). Please call or check on them."
    if kind == "duplicate":
        return f"⚠️ {elder} tried to confirm {medicine} ({dose_time}) again at {at}. It was already taken: possible double dose. {extra}".strip()
    if kind == "too_close":
        return f"⚠️ {elder} tried to take {medicine} at {at}, too soon after the last dose. {extra}".strip()
    if kind == "taken_late":
        return f"✅ Update: {elder} took {medicine} at {at} (due {dose_time})."
    raise ValueError(kind)


# --------------------------------------------------------------------------- #
# Simulation: replay one day with the rules above
# --------------------------------------------------------------------------- #
def simulate(medicines, attempts, now, settings, elder="Grandma"):
    """Replay a day and return every decision with its reason.

    medicines : [{"name": "Metformin", "times": ["08:00", "20:00"]}, ...]
    attempts  : [{"mi": 0, "dose_time": "08:00", "at": "08:05"}, ...]
                mi = index of the medicine; "at" = when "I took it" was pressed
    now       : "HH:MM", the moment we look at the day
    """
    day = datetime(2000, 1, 1)
    at = lambda hhmm: day + timedelta(minutes=minutes_of(hhmm))  # noqa: E731
    now_dt = at(now)

    meds, doses, gaps = [], {}, {}
    for mi, med in enumerate(medicines):
        gap, interval = min_gap(med["times"], settings)
        gaps[mi] = gap
        meds.append({"name": med["name"], "times": med["times"], "min_gap_minutes": gap,
                     "min_gap": duration(gap) if gap else "off", "gap_reason": gap_reason(med["times"], settings)})
        for t in sorted(set(med["times"])):
            doses[(mi, t)] = {"mi": mi, "medicine": med["name"], "time": t, "taken_at": None, "hold_until": None}

    events, out_attempts, last_taken = [], [], {}

    def event(when, kind, text, why=""):
        events.append({"at": hm(when), "kind": kind, "text": text, "why": why, "_t": when})

    # Replay confirmations in time order.
    for a in sorted(attempts, key=lambda a: minutes_of(a["at"])):
        dose = doses.get((a["mi"], a["dose_time"]))
        if dose is None:
            continue
        when = at(a["at"])
        record = {"mi": a["mi"], "medicine": dose["medicine"], "dose_time": a["dose_time"], "at": a["at"]}
        if when > now_dt:
            out_attempts.append({**record, "result": "pending", "label": "Not happened yet",
                                 "reason": f"Happens at {a['at']}; move the time slider past it."})
            continue
        decision = check_confirmation(at(dose["time"]), dose["taken_at"], when,
                                      last_taken.get(a["mi"]), gaps[a["mi"]], settings, dose["medicine"])
        if decision.result == "ok":
            dose["taken_at"] = when
            last_taken[a["mi"]] = when
        elif decision.result == "too_close":
            dose["hold_until"] = decision.safe_after   # elder was told to wait -> don't call it missed yet
        label = RESULT_LABELS[decision.result]
        out_attempts.append({**record, "result": decision.result, "label": label, "reason": decision.reason})
        event(when, "confirm" if decision.result == "ok" else "blocked",
              f"{dose['medicine']} {dose['time']}: “I took it” pressed → {label}", decision.reason)
        if decision.alert_caregiver:
            extra = f"Safe after {hm(decision.safe_after)}." if decision.safe_after else ""
            event(when, "alert", alert_text(decision.result, elder, dose["medicine"], dose["time"], a["at"], extra),
                  "Rule: a blocked double-dose attempt alerts the caregiver immediately.")

    # Status of every dose at `now`, plus reminders and missed-dose alerts.
    out_doses = []
    for (mi, t), dose in sorted(doses.items(), key=lambda kv: (minutes_of(kv[0][1]), kv[0][0])):
        scheduled = at(t)
        hold = dose["hold_until"]
        deadline = deadline_of(scheduled, settings, hold)
        taken = dose["taken_at"]
        status, reason = dose_status(scheduled, taken, now_dt, settings, hold)

        if scheduled <= now_dt and (taken is None or taken >= scheduled):
            event(scheduled, "reminder", f"{dose['medicine']} {t}: spoken reminder starts",
                  f"Repeats every 5 min until confirmed or until {hm(deadline)}.")
        if hold is not None and hold <= now_dt and (taken is None or taken >= hold):
            event(hold, "reminder", f"{dose['medicine']} {t}: safe time reached, reminder resumes",
                  f"The dose was on hold after a too-soon press. Missed only if not confirmed by {hm(deadline)}.")
        if now_dt > deadline and (taken is None or taken > deadline):
            event(deadline, "missed", f"{dose['medicine']} {t}: marked MISSED",
                  f"No confirmation by {hm(deadline)} ({settings.grace_minutes} min grace"
                  f"{' after the safe time' if hold else ''}).")
            event(deadline, "alert", alert_text("missed", elder, dose["medicine"], t),
                  "Rule: a missed dose alerts the caregiver once.")
        if status == "taken_late":
            event(taken, "alert", alert_text("taken_late", elder, dose["medicine"], t, hm(taken)),
                  "Rule: a missed dose taken late sends a follow-up.")

        out_doses.append({
            "mi": mi, "medicine": dose["medicine"], "time": t, "status": status, "reason": reason,
            "taken_at": hm(taken) if taken else None,
            "opens": hm(scheduled - timedelta(minutes=settings.early_minutes)),
            "deadline": hm(deadline),
            "hold_until": hm(hold) if hold else None,
            # minutes from midnight, for drawing the timeline (deadline may pass midnight)
            "t_open": minutes_of(t) - settings.early_minutes,
            "t_dose": minutes_of(t),
            "t_deadline": minutes_of(t) + int((deadline - scheduled).total_seconds() // 60),
        })

    order = {"reminder": 0, "confirm": 1, "blocked": 1, "missed": 2, "alert": 3}
    events.sort(key=lambda e: (e["_t"], order[e["kind"]]))
    for e in events:
        del e["_t"]

    return {
        "now": now,
        "settings": {"early_minutes": settings.early_minutes, "grace_minutes": settings.grace_minutes,
                     "gap_ratio": settings.gap_ratio},
        "medicines": meds,
        "doses": out_doses,
        "attempts": out_attempts,
        "events": events,
        "alerts": [e for e in events if e["kind"] == "alert"],
    }
