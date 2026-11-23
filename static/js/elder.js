// =============================================================================
//  PART 3 of 3 · THE SCREENS  (templates/ + static/)
//  This file runs the ELDER'S SCREEN: big clock, one big card per dose,
//  spoken reminders, and the giant "I took it" button.
//  See TEAM_GUIDE.md for who explains which part.
// =============================================================================
// Elder screen: shows today's doses, speaks reminders, confirms doses,
// and warns before a double dose.
(function () {
  "use strict";

  const CODE = document.body.dataset.code;
  const POLL_MS = 15000;          // refresh from server every 15 s
  const REPEAT_MS = 5 * 60000;    // repeat a spoken reminder every 5 min until confirmed

  let doses = [];
  let audioReady = false;
  const lastSpoken = {};          // dose id -> time we last announced it

  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- sound
  // WHAT THIS DOES: plays a two-note chime and reads text out loud
  // (the browser's built-in voice), so the elder doesn't need to read.
  let audioCtx = null;
  function chime() {
    if (!audioReady) return;
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      [660, 880].forEach((freq, i) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        const at = audioCtx.currentTime + i * 0.35;
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, at);
        gain.gain.exponentialRampToValueAtTime(0.3, at + 0.05);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.6);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(at);
        osc.stop(at + 0.65);
      });
    } catch (e) { /* no audio available */ }
  }

  function speak(text) {
    if (!audioReady || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = window.SPEECH_LANG || "en-US";   // speak in the page's language (if the device has that voice)
    u.rate = 0.85;                // slower is clearer for older listeners
    window.speechSynthesis.speak(u);
  }

  // English voices read "8 02" as a time; other languages read "8:02" well.
  const sayTime = (hhmm) => (window.LANG === "en" ? hhmm.replace(/^0/, "").replace(":", " ") : hhmm.replace(/^0/, ""));
  const describe = (d) => d.dose ? `${d.name}, ${d.dose}` : d.name;

  // ---------------------------------------------------------------- clock
  // WHAT THIS DOES: the big clock at the top, in the elder's own time zone.
  function tickClock() {
    // Show the clock in the elder's own time zone (same clock the server uses for reminders).
    const now = new Date();
    const timeZone = document.body.dataset.tz || undefined;
    let opts = (o) => (timeZone ? { ...o, timeZone } : o);
    try { now.toLocaleTimeString([], opts({})); } catch (e) { opts = (o) => o; }   // unknown zone -> device time
    const loc = [window.LANG || "en"];
    $("clock-time").textContent = now.toLocaleTimeString(loc, opts({ hour: "2-digit", minute: "2-digit", hour12: false }));
    let tz = timeZone;
    try { now.toLocaleDateString([], { timeZone: tz }); } catch (e) { tz = undefined; }
    $("clock-date").textContent = window.fmtDate.dayLong(now, tz);
  }

  // ---------------------------------------------------------------- render
  // WHAT THIS DOES: draws the screen: the card for the dose that's due now
  // (with the big button) and the "Today" list underneath.
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;   // textContent = safe from HTML injection
    return node;
  }

  function render() {
    const isTaken = (d) => d.status === "taken" || d.status === "taken_late";
    const due = doses.filter((d) => ["open", "on_hold", "due", "missed"].includes(d.status));
    const dueBox = $("due");
    dueBox.replaceChildren();

    if (due.length === 0) {
      const next = doses.find((d) => d.status === "upcoming");
      const card = el("div", "due-card calm");
      card.append(el("p", "due-title", t("el.nothing")));
      card.append(el("p", "due-sub", next ? t("el.next", { name: next.name, time: next.time }) : t("el.all_done")));
      dueBox.append(card);
    }

    due.forEach((d) => {
      const card = el("div", "due-card" + (d.status === "missed" ? " late" : ""));
      const label = d.status === "on_hold" ? t("el.lbl_hold", { time: d.hold_until })
                                           : t(`el.lbl_${d.status}`, { time: d.time });
      card.append(el("p", "due-label", label));
      card.append(el("p", "due-title", d.name));
      if (d.dose) card.append(el("p", "due-sub", d.dose));
      if (d.note) card.append(el("p", "due-note", d.note));
      const btn = el("button", "btn btn-huge btn-go", t("el.took"));
      btn.type = "button";
      btn.addEventListener("click", () => take(d, btn));
      card.append(btn);
      dueBox.append(card);
    });

    const list = $("list");
    list.replaceChildren();
    doses.forEach((d) => {
      const li = el("li", "elder-item " + (isTaken(d) ? "taken" : d.status));
      li.append(el("span", "item-time", d.time));
      const main = el("span", "item-name", d.name);
      li.append(main);
      const state = isTaken(d) ? `✓ ${d.taken_at}` : t(`el.state_${d.status}`);
      li.append(el("span", "item-state", state));
      // Tapping a taken dose answers "did I already take this?" and blocks a double dose.
      if (isTaken(d)) {
        li.tabIndex = 0;
        li.setAttribute("role", "button");
        li.addEventListener("click", () => take(d));
      }
      list.append(li);
    });
  }

  // ---------------------------------------------------------------- reminders
  // WHAT THIS DOES: when a dose is due, chime + say "Time for ..." and repeat
  // every 5 minutes until "I took it" is pressed.
  function announce() {
    const now = Date.now();
    const toSay = doses.filter((d) =>
      (d.status === "due" || d.status === "missed") &&
      (!lastSpoken[d.id] || now - lastSpoken[d.id] > REPEAT_MS));
    if (toSay.length === 0) return;
    toSay.forEach((d) => { lastSpoken[d.id] = now; });
    chime();
    const names = toSay.map(describe).join(t("el.join"));
    setTimeout(() => speak(t("el.say_remind", { names })), 900);
  }

  // ---------------------------------------------------------------- notifications
  // WHAT THIS DOES: a phone notification with a "✓ I took it" button
  // (uses notify.js + sw.js).
  // One phone notification when a dose becomes due, another if it becomes late.
  // Its "✓ I took it" button calls the same API as the big button (handled in sw.js).
  function notifyDoses() {
    doses.forEach((d) => {
      if (d.status !== "due" && d.status !== "missed") return;
      const due = d.status === "due";
      window.SanakhNotify.show(`elder:${CODE}:${d.id}:${d.status}`,
        t(due ? "nt.due_title" : "nt.missed_title", { name: d.name }), {
          body: t(due ? "nt.due_body" : "nt.missed_body", { time: d.time }),
          tag: `dose-${d.id}`, renotify: true, requireInteraction: true,
          actions: [{ action: "take", title: t("el.took") }],
          data: {
            url: location.pathname, code: CODE, id: d.id,
            msgs: {   // translated here, filled in by the service worker
              title: d.name,
              ok: t("el.msg_good", { name: d.name, time: "{time}" }),
              duplicate: t("el.msg_dup", { name: d.name, time: "{time}" }),
              too_close: t("el.msg_close", { name: d.name, time: "{time}" }),
              too_early: t("el.msg_early", { name: d.name, time: d.time }),
              fail: t("el.msg_net"),
            },
          },
        });
    });
  }

  function renderNotifyButton() {
    const b = $("notify-btn");
    const p = window.SanakhNotify.permission();
    b.textContent = t({ granted: "nt.on", denied: "nt.blocked", unsupported: "nt.unsupported" }[p] || "nt.enable");
    b.disabled = p !== "default";
  }

  async function turnOnNotifications() {
    const p = await window.SanakhNotify.request();
    renderNotifyButton();
    if (p === "granted") {
      window.SanakhNotify.show(null, "Sanakh", { body: t("nt.test"), tag: "sanakh-test" });
      notifyDoses();
    }
  }

  // ---------------------------------------------------------------- messages
  // WHAT THIS DOES: the full-screen answer: green "Well done", red "Stop,
  // already taken", or yellow "Wait until ...".
  function showMessage(kind, icon, title, body) {
    const box = $("message");
    box.className = "message " + kind;
    $("message-icon").textContent = icon;
    $("message-title").textContent = title;
    $("message-body").textContent = body;
    box.hidden = false;
    $("message-ok").focus();
  }

  // ---------------------------------------------------------------- actions
  // WHAT THIS DOES: take() = pressing "I took it". Sends it to the server
  // (Part 2), which asks the Brain (Part 1), then shows + speaks the answer.
  // sayToday() = the "What have I taken today?" button.
  async function take(dose, btn) {
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/api/e/${CODE}/doses/${dose.id}/take`, { method: "POST" });
      const data = await res.json();
      const d = data.dose || dose;
      if (data.result === "ok") {
        showMessage("good", "✓", t("el.msg_good_t"), t("el.msg_good", { name: d.name, time: d.taken_at }));
        speak(t("el.say_good", { name: d.name, time: sayTime(d.taken_at) }));
      } else if (data.result === "duplicate") {
        showMessage("stop", "✋", t("el.msg_dup_t"), t("el.msg_dup", { name: d.name, time: d.taken_at }));
        speak(t("el.say_dup", { name: d.name, time: sayTime(d.taken_at) }));
      } else if (data.result === "too_close") {
        showMessage("stop", "✋", t("el.msg_close_t"), t("el.msg_close", { name: d.name, time: data.allowed_after }));
        speak(t("el.say_close", { name: d.name, time: sayTime(data.allowed_after) }));
      } else if (data.result === "too_early") {
        showMessage("wait", "⏳", t("el.msg_early_t"), t("el.msg_early", { name: d.name, time: d.time }));
        speak(t("el.say_early", { name: d.name, time: sayTime(d.time) }));
      }
    } catch (e) {
      showMessage("stop", "⚠", t("el.msg_net_t"), t("el.msg_net"));
    }
    await load();
  }

  function sayToday() {
    audioReady = true;
    const taken = doses.filter((d) => d.status === "taken" || d.status === "taken_late");
    const waiting = doses.filter((d) => d.status === "due" || d.status === "missed");
    const next = doses.find((d) => d.status === "upcoming");
    const parts = [];
    const sep = t("el.list_sep");
    parts.push(taken.length
      ? t("el.say_taken", { list: taken.map((d) => t("el.say_item", { name: d.name, time: sayTime(d.taken_at) })).join(sep) })
      : t("el.say_none"));
    if (waiting.length) parts.push(t("el.say_waiting", { list: waiting.map((d) => d.name).join(sep) }));
    if (next) parts.push(t("el.say_next", { name: next.name, time: sayTime(next.time) }));
    speak(parts.join(" "));
  }

  // ---------------------------------------------------------------- data
  // WHAT THIS DOES: asks the server for today's doses every 15 seconds.
  async function load() {
    try {
      const res = await fetch(`/api/e/${CODE}/today`, { cache: "no-store" });
      if (!res.ok) return;
      doses = (await res.json()).doses;
      render();
      announce();
      notifyDoses();
    } catch (e) { /* offline: keep last data, try again next poll */ }
  }

  // ---------------------------------------------------------------- start
  // WHAT THIS DOES: the "Start" tap (browsers only allow sound after a tap),
  // connects all the buttons, and starts the clock and the 15-second refresh.
  $("start-btn").addEventListener("click", () => {
    audioReady = true;
    $("start").hidden = true;
    speak(t("el.say_hello"));
    turnOnNotifications();   // the Start tap also asks once for notification permission
    announce();
  });
  $("ask-btn").addEventListener("click", sayToday);
  $("notify-btn").addEventListener("click", turnOnNotifications);
  renderNotifyButton();
  $("message-ok").addEventListener("click", () => {
    $("message").hidden = true;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  });

  // (The service worker is registered by notify.js.)

  tickClock();
  setInterval(tickClock, 1000);
  load();
  setInterval(load, POLL_MS);
})();
