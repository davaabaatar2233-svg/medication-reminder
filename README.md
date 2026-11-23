# Sanakh: a voice-first medication buddy

**Spoken medication reminders for elders that alert family when a dose is missed and stop accidental double doses.**

*Sanakh* (санах) means "to remember" in Mongolian.

## The problem

Older people often take several medicines a day. They forget doses, or forget that they already took one and take it twice. Most reminder apps assume good eyesight, confident phone skills and a good memory. Family members living elsewhere have no way to know whether a dose was missed.

## Understand it in 2 minutes

Open **/simulator** and press **▶ Start the tour**. Five steps answer the five key questions. Each step sets up the simulator and highlights where the answer appears:

| # | Question | What the step shows |
|---|---|---|
| 1 | When it is time for a medicine, how does the system remind? | the dose turns **due**; the log shows a spoken reminder that repeats every 5 min |
| 2 | When the user confirms "I took it", how is it recorded? | the press is checked, then saved with its exact time (atomic database write) |
| 3 | When the same dose is confirmed again, how is the earlier record checked? | the second press is **blocked** ("already taken at 08:05") and the caregiver is alerted |
| 4 | If a dose is not confirmed in time, how is it detected and reported? | after time + grace the dose is **missed** and one Telegram alert is sent |
| 5 | When the time or settings change, how do the decisions change? | grace raised to 60 min, so a 20:45 press is on time; drag it back to 30 and it becomes late, with an alert |

## ⚠ Not a medical device

Sanakh is a **student learning project**. The minimum gap between two doses of the same medicine is a simple **teaching rule** (half the time between that medicine's scheduled doses). It is **not** a medical safety interval, so the app never says a dose is "safe". It says the app *allows* the next confirmation and tells people to ask their doctor or pharmacist. Every page shows this notice.

## The algorithm (algorithm/rules.py)

All decisions are made by pure Python functions in `algorithm/rules.py`. The live app and the interactive **simulator** (`/simulator`) both call them, so the simulator shows exactly what the real app does. Every decision returns a plain-language **reason**.

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
| A dose becomes **missed** | ⚠️ "Grandma has not confirmed Metformin (due 08:00)", sent **once** |
| A **double dose is attempted** (duplicate or too close) | ⚠️ sent immediately, as a sign of confusion |
| A missed dose is **taken late** | ✅ follow-up, so the family can relax |

An **on-hold** dose (the elder was told to wait) does **not** trigger a missed alert at its normal deadline. That avoids a false alarm.

### Time zones
Each profile stores the elder's own IANA time zone (detected from the browser when the profile is created, editable on the dashboard). Reminders, missed-dose checks and alerts all use **the clock where the elder lives**, so 08:00 means 08:00 for them even if the caregiver is in another country. The background checker evaluates every elder against their own local time.

### 4. Interactive simulation (`/simulator`)
Choose a scenario or build your own: edit the schedule, add "I took it" presses at any time, change `EARLY`, `GRACE` and `GAP_RATIO`, and drag or play the clock. By default the clock is **live**: it shows the viewer's real current time in their own time zone; press **Now** to return to it. You see each dose's status and reason, every blocked press, every caregiver alert and a full decision log.

## Features

| For the elder (phone or tablet) | For the caregiver (any browser) |
|---|---|
| Big clock and one large card per dose | Add medicines with a dose, times and a note |
| Spoken reminder and chime, repeated every 5 minutes | Live "Today" view with the algorithm's reason for each dose |
| One big **"I took it"** button | Telegram alerts (rules above) |
| Red **"Stop. Already taken"** / **"Wait until 13:20"** screens | 7-day adherence chart |
| 🔊 "What have I taken today?" read aloud | Share code to link the devices |
| Installable to the home screen (PWA) | Link to the simulator |
| **Phone notification** with an “✓ I took it” button | **Browser notification** when a dose is missed or taken late |
| Clock shown in **their own time zone** | Per-person time zone (auto-detected, editable) |

## Monthly log

Dashboard → **📅 Monthly log** (`/c/<code>/log?month=YYYY-MM`):
- A calendar coloured by adherence (green = all on time, yellow = some late or missed, red = mostly missed, ✋ = a double dose was blocked), with ← → to change month.
- A month summary: % taken, on time, late, missed and double doses blocked.
- Tap a day to see every dose and every **event**. Every press, including blocked double-dose and too-soon attempts, and every missed-dose alert is stored in the `events` table.
- **⬇ Download CSV** opens in Excel or Google Sheets, e.g. to show a doctor.
- "Fill the last 30 days with demo history" creates realistic sample data for demos.

## Pharmacy finder

`/pharmacies` (linked from the elder screen, the dashboard and the home page) helps older people find a pharmacy nearby and the cheapest place to buy a medicine:
- **📍 Use my location** → the nearest pharmacies from **OpenStreetMap** (free Overpass API, no key), on a map and in a big-text list with distance and walking time.
- Type a medicine (the elder's own medicines are suggested) to sort by the **cheapest price reported by users** in the last 90 days. There is no public price data for Mongolian pharmacies, so prices are crowd-sourced with "＋ Add a price".
- Big **🧭 Directions** (Google Maps, walking) and **📞 Call** buttons.
- A reminder to ask about the **health insurance discount** on essential medicines at contracted pharmacies.
- Limits: pharmacy coverage depends on OpenStreetMap, and price reports are not moderated yet, so the page tells users to check prices at the pharmacy.

## Reliable alerts

A missed-dose alert counts as **sent only when Telegram answers `{"ok": true}`** (`services/alerts.py` → `run_check()` → `data/database.py` → `record_alert()`):

| What happened | Recorded as | Dashboard shows |
|---|---|---|
| Telegram confirmed | `sent` | Missed · alert sent |
| Network or Telegram error | `retry`, tried again every minute; `failed` after 5 tries | Missed · retrying alert / alert FAILED |
| No bot token or no chat ID | `not_configured` | Missed · Telegram not set up |

Every outcome is also written to the event log (`alert_sent`, `alert_failed`, `alert_off`), so the monthly log never claims an alert that wasn't delivered.

## Privacy

- **Caregiver PIN** (6 digits, stored only as a salted hash): the dashboard, history, settings, export and delete need it; the elder screen only needs the share code. After 5 wrong PINs the profile waits 15 minutes. A device stays unlocked for 30 days, or until "🔒 Lock this device".
- **Share codes** are 8 characters (about 850 billion possibilities).
- **⬇ Download all data** (JSON) and **Delete this profile** (type the share code to confirm) on the dashboard.
- **/privacy** explains what is stored, what is never stored, who can see it and what leaves the app.
- The pharmacy finder sends only a **rounded location (≈100 m)** to OpenStreetMap; nothing about location is stored.
- Security headers: no caching of health pages, no framing, no referrer leaks; the session cookie is HttpOnly and https-only on Render; a random secret key is created if `SECRET_KEY` isn't set.

## Firewall (services/firewall.py)

Runs before every page. Per visitor address, per minute:

| What | Limit | Then |
|---|---|---|
| Any page | 120 | 429 "please wait a minute" |
| Saving (POST) | 30 | 429 |
| PIN unlock, create / open profile | 10 | 429 (stops code and PIN guessing) |
| Simulator | 300 | 429 (Play at Fast speed uses ~120) |

- Robot paths (`/wp-admin`, `/.env`, `.php`, `../` …), odd methods (PUT, DELETE …) and bodies over 64 KB are refused.
- **5 broken rules → that address is blocked for 10 minutes.** Other visitors are not affected.
- On Render it reads the visitor's real address from Render's proxy (one hop only, so a fake header can't dodge a block).
- Turn it off with the environment variable `FIREWALL=0`. Messages are translated into all 9 languages.

## Notifications

`static/js/notify.js` + `static/sw.js` (service worker):
- **Elder:** when a dose becomes *due*, a notification appears with a **✓ I took it** button. The button calls the same API as the big button, so the double-dose rules still apply. If the dose becomes *missed*, the notification is replaced by a "late" one.
- **Caregiver:** press "🔔 Notify me on this device" on the dashboard to get a notification when a dose is missed or taken late.
- Each notification is shown once (remembered per day), so reloading doesn't repeat it.
- Limits: browser notifications appear while Sanakh is open, including in the background. True push to a closed app would need Web Push, so Telegram covers the caregiver when the app is closed. On iPhone, notifications need the app added to the Home Screen (iOS 16.4+).

## Languages

The whole app is in **9 languages**: English, Монгол, Русский, 中文, 한국어, 日本語, Español, Français and Deutsch. That covers every screen, the algorithm's explanations, spoken reminders and Telegram alerts.

- All text lives in `languages/<lang>.json` (one key → one sentence with `{placeholders}`). The same files are used by Python (`services/translate.py`) and by JavaScript (`static/js/i18n.js`).
- The language is picked automatically from the browser and can be changed with the 🌐 menu (remembered in a cookie).
- The elder screen defaults to the profile's language. Telegram alerts use the "Language of Telegram alerts" setting.
- `tests/test_translations.py` checks that every language has every key, with the same placeholders and HTML tags as English.
- Spoken reminders use the device's voice for that language. Most phones have no Mongolian voice yet, so Mongolian text shows correctly but may be read by another voice or not at all.
- Translations were machine-assisted. Native speakers should review them, especially before real use.

## Tech

Python 3.11+, Flask, SQLite, Jinja templates, vanilla JavaScript (Web Speech API, Web Audio API), service worker / web app manifest, Telegram Bot API, gunicorn on Render.

## Project layout

See **[CODE_MAP.md](CODE_MAP.md)** for every file and what it does (English + Mongolian), plus how a press of "I took it" travels through the code.

```
app.py            web server + routes          (start here)
algorithm/        the decision rules           (rules.py)
data/             database                     (database.py)
services/         Telegram alerts, translations
languages/        all text, 9 languages
templates/        HTML pages
static/           css/, js/, img/, sw.js
tests/            32 automatic tests
```

## Run locally

> **Install the packages first.** The web tests need Flask: run `pip install -r requirements.txt` before `python -m unittest discover tests`. Without it, only the algorithm and translation tests can run.

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
| `APP_TIMEZONE` | `Asia/Ulaanbaatar` | fallback time zone (each profile normally stores its own) |
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
