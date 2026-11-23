# Team guide: who explains what

Sanakh is split into **3 parts**, one per person. Every file starts with a comment saying
which part it belongs to, and every section inside says in plain words what it does.

Search the code for `PART 1`, `PART 2` or `PART 3` to find your files.

```
   PART 3 · THE SCREENS             PART 2 · THE SERVER              PART 1 · THE BRAIN
   what people see and tap   ──▶    receives the request,     ──▶    decides (rules.py)
   (HTML, CSS, JavaScript)          checks safety, sends alerts      and remembers (database.py)
```

---

## PART 1 · The Brain 🧠 (decides and remembers)

**Files:** `algorithm/rules.py`, `data/database.py`, `tests/test_rules.py`

**In one sentence:** "Every decision the app makes (is this dose due, missed, a double dose?) is made
by a few small Python functions, and every decision comes with a reason in plain words."

| Section | What to say |
|---|---|
| `rules.py` → Rule 1 `dose_status()` | A dose moves through states: upcoming → open → due → missed. Missed = 30 min after its time with no "I took it". |
| `rules.py` → Rule 2 `check_confirmation()` | When "I took it" is pressed: already taken? → blocked. Same medicine too soon? → blocked, wait until a time. Too early? → blocked. |
| `rules.py` → `min_gap()` | Min gap = half the time between doses (08:00 & 20:00 → 6 h). A **teaching rule**, not medical advice. |
| `rules.py` → Rule 3 `alert_text()` | The text of the message the family gets. |
| `rules.py` → `simulate()` | Replays a whole day with the same rules, used by the simulator page. |
| `database.py` | SQLite tables (people, medicines, doses, events). Saves a dose with `WHERE taken_at IS NULL`, so two taps at once can never record it twice. |

**Demo:** open `/simulator`, press *Start the tour*.

---

## PART 2 · The Server 🚪 (connects everything, keeps it safe)

**Files:** `app.py`, `services/alerts.py`, `services/firewall.py`, `services/translate.py`,
`languages/*.json`, `render.yaml`, `tests/test_app.py`, `tests/test_firewall.py`

**In one sentence:** "The Flask server receives every click from the screens, asks the Brain what to do,
and tells the family on Telegram when something goes wrong."

| Section | What to say |
|---|---|
| `app.py` → settings | Grace 30 min, early 30 min, gap ratio 50%: all changeable. |
| `app.py` → home / caregiver / elder | One URL per page. Caregiver pages need a 6-digit PIN, the elder screen doesn't. |
| `app.py` → API `api_take()` | The "I took it" request: asks the Brain, then alerts the family if needed. |
| `alerts.py` → `run_check()` | Every minute: find missed doses → send Telegram. Counts as sent only if Telegram says OK; retries 5 times. |
| `firewall.py` | Blocks robots, too many requests, PIN guessing (max 10 tries a minute; an address that keeps breaking the rules is blocked for 10 min). |
| `translate.py` + `languages/` | 9 languages; the same text files are used by Python and the browser. |

**Demo:** create a profile → *Add a demo dose due in 1 minute* → show the Telegram / missed alert.

---

## PART 3 · The Screens 📱 (what elders and families see)

**Files:** `templates/*.html`, `static/css/style.css`, `static/js/*.js`, `static/sw.js`

**In one sentence:** "One giant button for the elder, with spoken reminders; a live dashboard for the family;
and phone notifications with an 'I took it' button."

| Section | What to say |
|---|---|
| `elder.js` | Big clock, one big card per dose, speaks reminders every 5 min, red "Stop, already taken" screen. |
| `caregiver.js` | Dashboard refreshes every 20 s and shows *why* each dose has its status. |
| `notify.js` + `sw.js` | Phone notifications; the "✓ I took it" button on the notification uses the same rules. |
| `simulator.js` | Interactive simulator + 2-minute tour (calls the Brain through `/api/simulate`). |
| `log.js` | Monthly calendar, green / yellow / red, download as CSV for the doctor. |
| `pharmacies.js` | Nearest pharmacies on a map (OpenStreetMap) + cheapest reported price. |

**Demo:** open the elder screen, press *Start*, wait for the reminder, press *I took it* twice.

---

## The hand-off: one press of "I took it" crosses all 3 parts

Good way to pass the microphone between you:

1. **Part 3** · `elder.js` → `take()` sends `POST /api/e/<code>/doses/<id>/take`
2. **Part 2** · `app.py` → `api_take()` finds the profile
3. **Part 1** · `database.py` → `take_dose()` → `rules.py` → `check_confirmation()` **decides**
4. **Part 1** · `database.py` saves it (never twice)
5. **Part 2** · `alerts.py` → Telegram message if it was a double dose
6. **Part 3** · `elder.js` shows "Well done ✓" or "Stop ✋" and says it out loud
