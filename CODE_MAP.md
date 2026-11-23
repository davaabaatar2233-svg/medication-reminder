# Code map · Кодын зураглал

What every part of Sanakh does, in the order to read it.
Sanakh-ийн хэсэг бүр юу хийдэг вэ: уншиж эхлэх дарааллаар.

```
sanakh/
│
├── app.py ......................... 🚪 START · web server and every page / API route
│
├── algorithm/ ..................... 🧠 THE ALGORITHM (no database, no web)
│   └── rules.py ................... dose status, double-dose check, missed rule, alerts, simulate()
│
├── data/ .......................... 💾 DATABASE
│   └── database.py ................ tables, save/read doses, apply the rules, monthly log, prices, PIN, export, delete
│
├── services/ ...................... 🔧 HELPERS
│   ├── alerts.py .................. Telegram messages + missed-dose checker (every minute)
│   ├── firewall.py ................ blocks robots, too many requests, PIN guessing · хамгаалалт
│   └── translate.py ............... picks the language, t("key") → text, English fallback
│
├── languages/ ..................... 🌐 ALL TEXT, one file per language
│   └── en.json mn.json ru.json zh.json ko.json ja.json es.json fr.json de.json
│
├── templates/ ..................... 📄 PAGES (HTML)
│   ├── base.html .................. shared frame: language menu, messages
│   ├── index.html ................. home: create / open a profile
│   ├── caregiver.html ............. caregiver dashboard
│   ├── elder.html ................. elder screen (big button)
│   ├── log.html ................... monthly log
│   ├── pharmacies.html ............ pharmacy finder (map + cheapest prices)
│   ├── unlock.html ................ caregiver PIN
│   ├── privacy.html ............... privacy page
│   ├── simulator.html ............. simulator + 2-minute tour
│   └── 404.html ................... not found
│
├── static/ ........................ 🎨 FILES THE BROWSER LOADS
│   ├── css/style.css .............. all styling
│   ├── img/icon.svg ............... app icon
│   ├── js/elder.js ................ elder screen: reminders, speech, "I took it", notifications
│   ├── js/caregiver.js ............ dashboard: live "Today" list, notifications
│   ├── js/log.js .................. monthly log: calendar + day detail
│   ├── js/pharmacies.js ........... pharmacy finder: location, map, list, price reports
│   ├── js/simulator.js ............ simulator + tour
│   ├── js/notify.js ............... shared notification helper
│   ├── js/i18n.js ................. t() in the browser + date names
│   └── sw.js ...................... service worker: install to home screen, "I took it" on notifications
│
├── tests/ ......................... ✅ 46 AUTOMATIC TESTS
│   ├── test_rules.py .............. the algorithm
│   ├── test_app.py ................ pages, API, database, monthly log
│   └── test_translations.py ....... every language complete
│
├── requirements.txt ............... Python packages (Flask, gunicorn, tzdata)
├── render.yaml .................... deploy settings for Render
└── README.md ...................... full documentation
```

## How a press of "I took it" travels through the code
## “Би уусан” товч дарахад код хэрхэн ажилладаг вэ

| # | Where · Хаана | What happens · Юу болох |
|---|---|---|
| 1 | `static/js/elder.js` → `take()` | The browser sends `POST /api/e/<code>/doses/<id>/take` · Хөтөч сервер рүү хүсэлт илгээнэ |
| 2 | `app.py` → `api_take()` | Finds the profile and calls the database · Профайлыг олж, өгөгдлийн санг дуудна |
| 3 | `data/database.py` → `take_dose()` | Reads this dose and the last dose of the same medicine · Энэ тун болон ижил эмийн сүүлийн тунг уншина |
| 4 | `algorithm/rules.py` → `check_confirmation()` | **Decides:** ok / duplicate / too_close / too_early, with a reason · **Шийднэ:** зөвшөөрөх эсвэл хориглох, шалтгааны хамт |
| 5 | `data/database.py` | Saves with `WHERE taken_at IS NULL` (never twice) and logs an event · Давхардалгүй хадгалж, үйл явдлыг бүртгэнэ |
| 6 | `services/alerts.py` | Double dose → Telegram alert to the caregiver · Давхар тун бол асран хамгаалагчид мэдэгдэнэ |
| 7 | `static/js/elder.js` | Shows "Well done" or "Stop. Already taken" and speaks it · Үр дүнг харуулж, дуугаар хэлнэ |

## How a missed dose is found · Алгассан тунг хэрхэн илрүүлэх вэ

1. `services/alerts.py` → `run_check()` runs every minute.
2. `data/database.py` → `find_new_missed()` finds doses where `algorithm/rules.py` → `dose_status()` says **missed** (*now > dose time + 30 min*). It only **finds** them and logs one `missed` event.
3. `run_check()` sends the Telegram alert, and **only then** `record_alert()` saves the real result: `sent`, `retry` (failed, try again next minute), `failed` (after 5 tries) or `not_configured`.

Минут тутам тун бүрийг шалгана. *Одоо > уух цаг + 30 мин* бол **алгассан**. Telegram мэдэгдлийг илгээсний **дараа** л бодит үр дүнг (илгээсэн / дахин оролдох / амжилтгүй / тохируулаагүй) хадгална.

## ⚠ Why the code says "allowed", never "safe" · Яагаад “аюулгүй” биш “зөвшөөрнө” гэдэг вэ

The minimum gap (`min_gap()` in `algorithm/rules.py`) is a simple teaching rule: half the time between a medicine's scheduled doses. Real safe intervals depend on the medicine and the person, and only a doctor or pharmacist can decide them. So the result is called `allowed_after`, and every message says the app *allows* the next confirmation.

Хамгийн бага зай нь сургалтын энгийн дүрэм (товлосон тунгуудын хоорондох хугацааны тал). Эмийн жинхэнэ аюулгүй хугацааг зөвхөн эмч, эм зүйч тогтооно.

## How the pharmacy finder works · Эмийн сан хайгч хэрхэн ажилладаг вэ

1. `static/js/pharmacies.js` asks the browser for the location (or uses central Ulaanbaatar).
2. It asks OpenStreetMap's free Overpass service for pharmacies within 1.5 km, widening to 4 km and then 10 km if fewer than 3 are found.
3. It asks Sanakh `GET /api/prices` for the cheapest price people reported in the last 90 days (`data/database.py` → `cheapest_prices()`).
4. The list is sorted by price (pharmacies with a reported price first), otherwise by distance. Each pharmacy gets 🧭 Directions, 📞 Call and ＋ Add a price.
5. "Add a price" sends `POST /api/prices`; `add_price()` checks the pharmacy id, medicine name and price before saving.

Байршил → ойролцоох эмийн сангууд (OpenStreetMap) → хэрэглэгчдийн мэдээлсэн хамгийн хямд үнэ → үнээр, эсвэл зайгаар эрэмбэлнэ.
