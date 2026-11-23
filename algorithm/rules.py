"""Sanakh decision rules (the algorithm), as pure functions.

No database, no Flask, no clock. Every function gets the times it needs as
arguments, so the SAME rules run in the live app (data/database.py) and in the
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
   to wait, so the deadline moves to  allowed_time + GRACE  (no false "missed" alert).

2. DOUBLE DOSE DETECTION, two checks when "I took it" is pressed:
   a. duplicate : this exact dose was already confirmed -> blocked
   b. too close : the same medicine was confirmed less than MIN_GAP ago -> blocked
      MIN_GAP = GAP_RATIO x (shortest interval between this medicine's doses)
      ⚠ MIN_GAP is a simple TEACHING RULE invented for this student project. It is
        NOT a medical safety interval: real minimum gaps depend on the medicine and
        the person, and only a doctor or pharmacist can say when a dose is safe.
        So the app never says "safe"; it says the app "allows" the next confirmation.
      e.g. 08:00 and 20:00 -> interval 12 h -> min gap 6 h (with ratio 0.5)
   (A confirmation more than EARLY min before the dose time is "too early".)

3. CAREGIVER ALERTS are sent when
   - a dose becomes missed (once per dose),
   - a double dose is attempted (duplicate or too close),
   - a missed dose is taken late (follow-up so the family can relax).

4. EXPLANATIONS: every status and decision returns a reason string.
   Reasons come from languages/<lang>.json: each function takes `t`, a translate
   function t(key, **params). The default is English.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from services import translate

EN = translate.translator("en")


@dataclass(frozen=True)
class Settings:
    early_minutes: int = 30     # confirmation allowed from this long before the dose time
    grace_minutes: int = 30     # after the dose time, wait this long before "missed"
    gap_ratio: float = 0.5      # min gap between doses = ratio x shortest dose interval


@dataclass
class Decision:
    result: str                     # ok | duplicate | too_early | too_close
    reason: str
    allowed_after: datetime = None     # for too_close: earliest time the app allows the next confirmation (an app rule, not medical advice)
    alert_caregiver: bool = False


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def hm(dt):
    return dt.strftime("%H:%M")


def minutes_of(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def duration(minutes, t=EN):
    """150 -> '2 h 30 min' (in the chosen language)"""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return t("dur.hm", h=h, m=m)
    return t("dur.h", h=h) if h else t("dur.m", m=m)


# --------------------------------------------------------------------------- #
# Rule 2b helper: minimum gap between doses of one medicine
# --------------------------------------------------------------------------- #
def min_gap(times, settings):
    """Return (gap_minutes, shortest_interval_minutes) for a medicine's daily times.

    The interval wraps around midnight: 08:00 and 20:00 -> 12 h both ways.
    A once-a-day medicine has a 24 h interval.
    """
    mins = sorted({minutes_of(x) for x in times})
    if len(mins) <= 1:
        interval = 24 * 60
    else:
        interval = min(b - a for a, b in zip(mins, mins[1:] + [mins[0] + 24 * 60]))
    return round(interval * settings.gap_ratio), interval


def gap_reason(times, settings, t=EN):
    gap, interval = min_gap(times, settings)
    if gap == 0:
        return t("gap.off")
    return t("gap.reason", interval=duration(interval, t), pct=round(settings.gap_ratio * 100), gap=duration(gap, t))


# --------------------------------------------------------------------------- #
# Rule 1: status of a dose at a given moment
# --------------------------------------------------------------------------- #
def deadline_of(scheduled, settings, hold_until=None):
    """When an unconfirmed dose becomes missed."""
    deadline = scheduled + timedelta(minutes=settings.grace_minutes)
    if hold_until is not None:
        deadline = max(deadline, hold_until + timedelta(minutes=settings.grace_minutes))
    return deadline


def dose_status(scheduled, taken_at, now, settings, hold_until=None, t=EN):
    """Return (status, reason).

    status: upcoming | open | on_hold | due | missed | taken | taken_late
    hold_until: set when a confirmation was blocked as "too close" (the earliest allowed time).
    """
    opens = scheduled - timedelta(minutes=settings.early_minutes)
    deadline = deadline_of(scheduled, settings, hold_until)

    if taken_at is not None and taken_at <= now:
        if taken_at <= deadline:
            return "taken", t("rs.taken", taken=hm(taken_at), deadline=hm(deadline))
        return "taken_late", t("rs.taken_late", taken=hm(taken_at), deadline=hm(deadline))
    if now < opens:
        return "upcoming", t("rs.upcoming", opens=hm(opens), early=settings.early_minutes, time=hm(scheduled))
    if hold_until is not None and now < hold_until:
        return "on_hold", t("rs.on_hold", hold=hm(hold_until), deadline=hm(deadline))
    if now < scheduled:
        return "open", t("rs.open", time=hm(scheduled))
    base = hm(hold_until) if hold_until is not None and hold_until > scheduled else hm(scheduled)
    if now <= deadline:
        return "due", t("rs.due", deadline=hm(deadline), base=base, grace=settings.grace_minutes)
    return "missed", t("rs.missed", deadline=hm(deadline), base=base, grace=settings.grace_minutes)


# --------------------------------------------------------------------------- #
# Rule 2: should a confirmation be accepted?
# --------------------------------------------------------------------------- #
def check_confirmation(scheduled, taken_at, now, last_taken, gap_minutes, settings, medicine=None, t=EN):
    """Decide what happens when "I took it" is pressed at `now`.

    scheduled   : datetime of the dose being confirmed
    taken_at    : when THIS dose was already confirmed, or None
    last_taken  : latest confirmation of ANY other dose of the same medicine, or None
    gap_minutes : minimum allowed time between two confirmations (see min_gap)
    """
    medicine = medicine or t("med.this")
    if taken_at is not None:
        return Decision("duplicate", t("dec.duplicate", time=hm(scheduled), medicine=medicine, taken=hm(taken_at)),
                        alert_caregiver=True)

    opens = scheduled - timedelta(minutes=settings.early_minutes)
    if now < opens:
        return Decision("too_early", t("dec.too_early", time=hm(scheduled), opens=hm(opens)))

    if last_taken is not None and gap_minutes > 0:
        since = now - last_taken
        if since < timedelta(minutes=gap_minutes):
            allowed = last_taken + timedelta(minutes=gap_minutes)
            return Decision(
                "too_close",
                t("dec.too_close", medicine=medicine, last=hm(last_taken),
                  since=duration(since.total_seconds() // 60, t), gap=duration(gap_minutes, t), allowed=hm(allowed)),
                allowed_after=allowed,
                alert_caregiver=True,
            )

    if last_taken is None:
        return Decision("ok", t("dec.ok_first", now=hm(now)))
    return Decision("ok", t("dec.ok_after", now=hm(now), last=hm(last_taken), gap=duration(gap_minutes, t)))


# --------------------------------------------------------------------------- #
# Rule 3: caregiver alert texts
# --------------------------------------------------------------------------- #
def alert_text(kind, elder, medicine, dose_time, at=None, allowed_after=None, t=EN):
    """kind: missed | duplicate | too_close | taken_late"""
    if kind not in ("missed", "duplicate", "too_close", "taken_late"):
        raise ValueError(kind)
    text = t(f"al.{kind}", elder=elder, medicine=medicine, time=dose_time, at=at)
    if allowed_after:
        text += " " + t("al.allowed_after", allowed=allowed_after)
    return text


# --------------------------------------------------------------------------- #
# Simulation: replay one day with the rules above
# --------------------------------------------------------------------------- #
def simulate(medicines, attempts, now, settings, elder=None, t=EN):
    """Replay a day and return every decision with its reason.

    medicines : [{"name": "Metformin", "times": ["08:00", "20:00"]}, ...]
    attempts  : [{"mi": 0, "dose_time": "08:00", "at": "08:05"}, ...]
                mi = index of the medicine; "at" = when "I took it" was pressed
    now       : "HH:MM", the moment we look at the day
    """
    elder = elder or t("sim.elder")
    day = datetime(2000, 1, 1)
    at = lambda hhmm: day + timedelta(minutes=minutes_of(hhmm))  # noqa: E731
    now_dt = at(now)

    meds, doses, gaps = [], {}, {}
    for mi, med in enumerate(medicines):
        gap, _ = min_gap(med["times"], settings)
        gaps[mi] = gap
        meds.append({"name": med["name"], "times": med["times"], "min_gap_minutes": gap,
                     "min_gap": duration(gap, t) if gap else t("gap.off_short"),
                     "gap_reason": gap_reason(med["times"], settings, t)})
        for x in sorted(set(med["times"])):
            doses[(mi, x)] = {"mi": mi, "medicine": med["name"], "time": x, "taken_at": None, "hold_until": None}

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
            out_attempts.append({**record, "result": "pending", "label": t("res.pending"),
                                 "reason": t("res.pending_reason", at=a["at"])})
            continue
        decision = check_confirmation(at(dose["time"]), dose["taken_at"], when, last_taken.get(a["mi"]),
                                      gaps[a["mi"]], settings, dose["medicine"], t)
        if decision.result == "ok":
            dose["taken_at"] = when
            last_taken[a["mi"]] = when
        elif decision.result == "too_close":
            dose["hold_until"] = decision.allowed_after   # elder was told to wait -> don't call it missed yet
        label = t(f"res.{decision.result}")
        out_attempts.append({**record, "result": decision.result, "label": label, "reason": decision.reason})
        event(when, "confirm" if decision.result == "ok" else "blocked",
              t("ev.press", medicine=dose["medicine"], time=dose["time"], label=label), decision.reason)
        if decision.alert_caregiver:
            allowed = hm(decision.allowed_after) if decision.allowed_after else None
            event(when, "alert", alert_text(decision.result, elder, dose["medicine"], dose["time"], a["at"], allowed, t),
                  t("rule.double"))

    # Status of every dose at `now`, plus reminders and missed-dose alerts.
    out_doses = []
    for (mi, x), dose in sorted(doses.items(), key=lambda kv: (minutes_of(kv[0][1]), kv[0][0])):
        scheduled = at(x)
        hold = dose["hold_until"]
        deadline = deadline_of(scheduled, settings, hold)
        taken = dose["taken_at"]
        status, reason = dose_status(scheduled, taken, now_dt, settings, hold, t)
        name = dose["medicine"]

        if scheduled <= now_dt and (taken is None or taken >= scheduled):
            event(scheduled, "reminder", t("ev.reminder", medicine=name, time=x),
                  t("ev.reminder_why", deadline=hm(deadline)))
        if hold is not None and hold <= now_dt and (taken is None or taken >= hold):
            event(hold, "reminder", t("ev.resume", medicine=name, time=x), t("ev.resume_why", deadline=hm(deadline)))
        if now_dt > deadline and (taken is None or taken > deadline):
            event(deadline, "missed", t("ev.missed", medicine=name, time=x),
                  t("ev.missed_why_hold" if hold else "ev.missed_why", deadline=hm(deadline), grace=settings.grace_minutes))
            event(deadline, "alert", alert_text("missed", elder, name, x, t=t), t("rule.missed"))
        if status == "taken_late":
            event(taken, "alert", alert_text("taken_late", elder, name, x, hm(taken), t=t), t("rule.late"))

        out_doses.append({
            "mi": mi, "medicine": name, "time": x, "status": status, "reason": reason,
            "taken_at": hm(taken) if taken else None,
            "opens": hm(scheduled - timedelta(minutes=settings.early_minutes)),
            "deadline": hm(deadline),
            "hold_until": hm(hold) if hold else None,
            # minutes from midnight, for drawing the timeline (deadline may pass midnight)
            "t_open": minutes_of(x) - settings.early_minutes,
            "t_dose": minutes_of(x),
            "t_deadline": minutes_of(x) + int((deadline - scheduled).total_seconds() // 60),
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
