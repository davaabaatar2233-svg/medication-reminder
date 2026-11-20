# Sanakh: a voice-first medication buddy

**Spoken medication reminders for elders that alert family when a dose is missed and stop accidental double doses.**

*Sanakh* (санах) means "to remember" in Mongolian.

## The problem

Older people often take several medicines a day. They forget doses, or forget that they already took one and take it twice. Most reminder apps assume good eyesight, confident phone skills and a good memory. Family members living elsewhere have no way to know whether a dose was missed.

## The algorithm (engine.py)

All decisions are made by pure Python functions in `engine.py`. The live app and the interactive **simulator** (`/simulator`) both call them, so the simulator shows exactly what the real app does. Every decision returns a plain-language **reason**.

### 1. Missed-dose detection
Each dose moves through states as time passes:

```
upcoming ──(time − EARLY)──▶ open ──(time)──▶ due ──(deadline)──▶ missed
                               │               │
                               └── "I took it" ┴──▶ taken   (after the deadline: taken_late)
deadline = time + GRACE        (or safe_time + GRACE if the dose is on hold, see 2b)
```

### 2. Double-dose detection, checked every time "I took it" is pressed
| Check | Rule | Result |
|---|---|---|
| a. Duplicate | this exact dose already has `taken_at` | **blocked**, shows "you already took it at 08:02" |
| b. Too close | same medicine confirmed less than `MIN_GAP` ago | **blocked**, dose goes **on hold** until the safe time |
| c. Too early | more than `EARLY` minutes before the dose time | blocked |

`MIN_GAP = GAP_RATIO × shortest interval between the medicine's doses`.
For example, 08:00 and 20:00 gives a 12 h interval, so with a ratio of 50% the minimum gap is 6 h. Four doses a day (4 h interval) gives 2 h.
The database update uses `WHERE taken_at IS NULL`, so even two devices tapping at the same moment can't record a dose twice.

### 3. Caregiver alerts
| When | Message |
|---|---|
| A dose becomes **missed** | ⚠️ "Grandma has not confirmed Metformin (due 08:00)" — sent **once** |
| A **double dose is attempted** (duplicate or too close) | ⚠️ sent immediately, as a sign of confusion |
| A missed dose is **taken late** | ✅ follow-up, so the family can relax |

An **on-hold** dose (the elder was told to wait) does **not** trigger a missed alert at its normal deadline. That avoids a false alarm.

### 4. Interactive simulation (`/simulator`)
Choose a scenario or build your own: edit the schedule, add "I took it" presses at any time, change `EARLY`, `GRACE` and `GAP_RATIO`, and drag or play the clock. You see each dose's status and reason, every blocked press, every caregiver alert and a full decision log.

## Features

| For the elder (phone or tablet) | For the caregiver (any browser) |
|---|---|
| Big clock and one large card per dose | Add medicines with a dose, times and a note |
| Spoken reminder and chime, repeated every 5 minutes | Live "Today" view with the algorithm's reason for each dose |
| One big **"I took it"** button | Telegram alerts (rules above) |
| Red **"Stop. Already taken"** / **"Wait until 13:20"** screens | 7-day adherence chart |
| 🔊 "What have I taken today?" read aloud | Share code to link the devices |
| Installable to the home screen (PWA) | Link to the simulator |

## Tech

Python 3.11+, Flask, SQLite, Jinja templates, vanilla JavaScript (Web Speech API, Web Audio API), service worker / web app manifest, Telegram Bot API, gunicorn on Render.

## Project layout

```
engine.py           THE ALGORITHM: status, double-dose, missed, alerts, simulate()
app.py              Flask routes + app setup (+ /simulator and /api/simulate)
models.py           SQLite schema; applies engine.py rules to stored doses
alerts.py           Telegram sending + background missed-dose checker
templates/          index, caregiver dashboard, elder screen, simulator
static/simulator.js simulator UI (calls /api/simulate on every change)
static/elder.js     elder screen: polling, speech, confirm, double-dose warning
static/caregiver.js live "Today" list on the dashboard
static/sw.js        service worker (installable app)
tests/              27 tests: engine rules (test_engine.py) + app/API (test_app.py)
```

## Run locally

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py                   # open http://localhost:5000
python -m unittest discover tests
```

To test alerts quickly, run with `GRACE_MINUTES=1 python app.py` and press **"Add a demo dose due in 1 minute"** on the dashboard.

### Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `APP_TIMEZONE` | `Asia/Ulaanbaatar` | time zone for schedules |
| `GRACE_MINUTES` | `30` | minutes after the dose time before it counts as missed |
| `EARLY_MINUTES` | `30` | how early a dose can be confirmed |
| `GAP_RATIO` | `0.5` | min gap between doses = ratio × shortest dose interval |
| `TELEGRAM_BOT_TOKEN` | – | from @BotFather; alerts are off without it |
| `CRON_KEY` | – | protects `/api/check-missed` |
| `DATABASE_PATH` | `instance/sanakh.db` | SQLite file |
| `SECRET_KEY` | dev value | Flask session key; set it in production |

## Deploy (Render, free)

1. Push this repo to GitHub (public).
2. render.com → **New → Blueprint** → choose the repo. `render.yaml` sets everything up.
3. Add `TELEGRAM_BOT_TOKEN` in the Render dashboard.
4. The free plan sleeps when idle, which pauses the background checker. Create a free job at **cron-job.org** that calls
   `https://<your-app>.onrender.com/api/check-missed?key=<CRON_KEY>` every 5 minutes. This keeps the app awake and runs the check.

**Telegram setup:** message @BotFather → `/newbot` → copy the token. Each caregiver presses **Start** in your bot, gets their chat ID from @userinfobot and pastes it into the dashboard.

## Limitations and next steps

- Anyone with the share code can open the profile. Real use needs caregiver accounts and login.
- On Render's free plan, the SQLite file resets when the app is redeployed. Use a persistent disk or Postgres for real use.
- Speech is in English; browsers have little or no Mongolian text-to-speech. Next step: pre-recorded Mongolian audio clips.
- Ideas: prescription-photo import, refill reminders, several caregivers per elder.

*This is a reminder tool, not medical advice. Always follow your doctor's or pharmacist's instructions.*
