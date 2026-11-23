// =============================================================================
//  PART 3 of 3 · THE SCREENS  (templates/ + static/)
//  This file runs the SIMULATOR page: you change the schedule, button presses
//  or the clock, and it shows what the Brain (Part 1) decides, with reasons.
//  See TEAM_GUIDE.md for who explains which part.
// =============================================================================
// Interactive simulator: every change is sent to /api/simulate, which runs
// the same Python rules as the live app (algorithm/rules.py) and returns the decisions.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DAY = 24 * 60;

  // ------------------------------------------------------------ scenarios
  const PRESETS = [
    {
      title: "sim.p1_t", hint: "sim.p1_h",
      now: "21:00",
      medicines: [{ name: "Metformin", times: "08:00, 20:00" }, { name: "Amlodipine", times: "08:00" }],
      attempts: [{ mi: 0, dose_time: "08:00", at: "08:05" }, { mi: 1, dose_time: "08:00", at: "08:06" },
                 { mi: 0, dose_time: "20:00", at: "20:10" }],
    },
    {
      title: "sim.p2_t", hint: "sim.p2_h",
      now: "10:00",
      medicines: [{ name: "Metformin", times: "08:00, 20:00" }],
      attempts: [{ mi: 0, dose_time: "08:00", at: "08:02" }, { mi: 0, dose_time: "08:00", at: "09:15" }],
    },
    {
      title: "sim.p3_t", hint: "sim.p3_h",
      now: "22:00",
      medicines: [{ name: "Metformin", times: "08:00, 20:00" }],
      attempts: [{ mi: 0, dose_time: "08:00", at: "08:04" }, { mi: 0, dose_time: "20:00", at: "21:40" }],
    },
    {
      title: "sim.p4_t", hint: "sim.p4_h",
      now: "14:00",
      medicines: [{ name: "Paracetamol", times: "08:00, 12:00, 16:00, 20:00" }],
      attempts: [{ mi: 0, dose_time: "08:00", at: "11:20" }, { mi: 0, dose_time: "12:00", at: "12:05" },
                 { mi: 0, dose_time: "12:00", at: "13:30" }],
    },
  ];

  // ------------------------------------------------------------ 2-minute tour
  // One step per question: a ready-made scene + where on the page the answer shows up.
  const MET = [{ name: "Metformin", times: "08:00, 20:00" }];
  const DEFAULTS = { early_minutes: 30, grace_minutes: 30, gap_ratio: 0.5 };
  const TOUR = [
    { scene: { medicines: MET, attempts: [], now: "08:10", settings: DEFAULTS },
      look: "doses", where: "sim.status_now" },
    { scene: { medicines: MET, attempts: [{ mi: 0, dose_time: "08:00", at: "08:05" }], now: "09:00", settings: DEFAULTS },
      look: "log", where: "sim.log" },
    { scene: { medicines: MET, attempts: [{ mi: 0, dose_time: "08:00", at: "08:05" }, { mi: 0, dose_time: "08:00", at: "09:15" }],
               now: "10:00", settings: DEFAULTS },
      look: "alerts", where: "sim.alerts" },
    { scene: { medicines: MET, attempts: [{ mi: 0, dose_time: "08:00", at: "08:04" }], now: "21:00", settings: DEFAULTS },
      look: "alerts", where: "sim.alerts" },
    { scene: { medicines: MET, attempts: [{ mi: 0, dose_time: "08:00", at: "08:04" }, { mi: 0, dose_time: "20:00", at: "20:45" }],
               now: "21:30", settings: { ...DEFAULTS, grace_minutes: 60 } },
      look: "s-grace", where: "sim.h4" },
  ];
  let tourStep = -1;   // -1 = not started

  function tourTarget(step) {
    const node = $(TOUR[step].look);
    return node ? (node.closest(".card") || node) : null;
  }

  function renderTour() {
    const box = $("tour");
    box.replaceChildren();
    const head = el("div", "tour-head");
    head.append(el("h2", "", t("tour.title")), el("p", "small muted", t("tour.sub")));
    box.append(head);

    const dots = el("div", "tour-dots");
    TOUR.forEach((_, i) => {
      const d = el("button", "tour-dot" + (i === tourStep ? " on" : i < tourStep ? " done" : ""), String(i + 1));
      d.type = "button";
      d.title = t(`tour.q${i + 1}`);
      d.addEventListener("click", () => goStep(i));
      dots.append(d);
    });
    box.append(dots);

    if (tourStep < 0) {
      const go = el("button", "btn btn-primary", t("tour.start"));
      go.type = "button";
      go.addEventListener("click", () => goStep(0));
      box.append(go);
      return;
    }
    const n = tourStep + 1;
    box.append(el("p", "small muted tour-count", t("tour.step", { n })),
               el("h3", "tour-q", t(`tour.q${n}`).replace(/^\d+\.\s*/, "")),
               el("p", "tour-a", t(`tour.a${n}`)));
    const nav = el("div", "row tour-nav");
    const back = el("button", "btn", t("tour.back"));
    back.type = "button"; back.disabled = tourStep === 0;
    back.addEventListener("click", () => goStep(tourStep - 1));
    const look = el("button", "btn", t("tour.look", { where: t(TOUR[tourStep].where) }));
    look.type = "button";
    look.addEventListener("click", () => {
      const target = tourTarget(tourStep);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    const last = tourStep === TOUR.length - 1;
    const next = el("button", "btn btn-primary", last ? t("tour.again") : t("tour.next"));
    next.type = "button";
    next.addEventListener("click", () => goStep(last ? 0 : tourStep + 1));
    nav.append(back, look, next);
    box.append(nav);
  }

  function goStep(i) {
    tourStep = i;
    const sc = TOUR[i].scene;
    stop();
    state.medicines = clone(sc.medicines);
    state.attempts = clone(sc.attempts);
    state.settings = clone(sc.settings);
    state.now = toMin(sc.now);
    state.live = false;
    activePreset = -1;
    renderPresets();
    renderEditors();
    run();
    renderTour();
    document.querySelectorAll(".tour-hl").forEach((n) => n.classList.remove("tour-hl"));
    const target = tourTarget(i);
    if (target) target.classList.add("tour-hl");
  }

  const state = {
    medicines: [],
    attempts: [],
    settings: { early_minutes: 30, grace_minutes: 30, gap_ratio: 0.5 },
    now: 12 * 60,
    live: true,          // follow the viewer's real clock, in their own time zone
  };

  // ------------------------------------------------------------ the viewer's own clock
  let TZ = "";
  try { TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* old browser */ }
  function localMinutes() {
    const d = new Date();               // the browser gives local time in the viewer's time zone
    return d.getHours() * 60 + d.getMinutes();
  }
  function goLive() {
    stop();
    state.live = true;
    state.now = localMinutes();
    $("now").value = state.now;
    run();
  }
  function goManual() { state.live = false; }

  // ------------------------------------------------------------ helpers
  const pad = (n) => String(n).padStart(2, "0");
  const toHM = (m) => `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`;
  const toMin = (hm) => { const [h, m] = hm.split(":").map(Number); return h * 60 + m; };
  const pct = (m) => `${(Math.max(0, Math.min(DAY, m)) / DAY) * 100}%`;
  const clone = (x) => JSON.parse(JSON.stringify(x));

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  // "8:00, 20:00" -> ["08:00", "20:00"] (invalid parts are skipped)
  function parseTimes(text) {
    const out = new Set();
    String(text).split(/[,;\s]+/).forEach((p) => {
      const m = /^(\d{1,2}):(\d{2})$/.exec(p.trim());
      if (m && +m[1] < 24 && +m[2] < 60) out.add(`${pad(+m[1])}:${m[2]}`);
    });
    return [...out].sort();
  }

  // ------------------------------------------------------------ input editors
  let activePreset = -1;
  function renderPresets() {
    const box = $("presets");
    box.replaceChildren();
    PRESETS.forEach((p, i) => {
      const b = el("button", "preset" + (i === activePreset ? " active" : ""), "");
      b.type = "button";
      b.append(el("strong", "", t(p.title)), el("span", "small muted", t(p.hint)));
      b.addEventListener("click", () => loadPreset(i));
      box.append(b);
    });
  }

  // The four rules, with the current setting values in bold.
  function renderRules() {
    const s = state.settings;
    const b = (text) => `<b>${text}</b>`;   // only numbers go in here, so this HTML is safe
    const params = { grace: b(t("unit.min", { n: s.grace_minutes })), gap: b(`${Math.round(s.gap_ratio * 100)}%`),
                     early: b(t("unit.min", { n: s.early_minutes })) };
    $("rules").innerHTML = [1, 2, 3, 4].map((n) => `
      <div class="rule"><span class="rule-n">${n}</span><div><strong>${t(`sim.r${n}_t`)}</strong><br>${t(`sim.r${n}`, params)}</div></div>`).join("");
    $("l-early").innerHTML = t("sim.s_early", { v: params.early });
    $("l-grace").innerHTML = t("sim.s_grace", { v: params.grace });
    $("l-gap").innerHTML = t("sim.s_gap", { v: params.gap });
  }

  function loadPreset(i) {
    const p = PRESETS[i];
    state.medicines = clone(p.medicines);
    state.attempts = clone(p.attempts);
    state.now = toMin(p.now);
    state.live = false;
    activePreset = i;
    document.querySelectorAll(".tour-hl").forEach((n) => n.classList.remove("tour-hl"));
    document.querySelectorAll(".preset").forEach((b, j) => b.classList.toggle("active", i === j));
    renderEditors();
    run();
  }

  function renderEditors() {
    // medicines
    const meds = $("meds");
    meds.replaceChildren();
    state.medicines.forEach((m, mi) => {
      const row = el("div", "edit-row");
      const name = el("input"); name.value = m.name; name.placeholder = t("sim.med_ph"); name.ariaLabel = t("sim.med_ph");
      const times = el("input"); times.value = m.times; times.placeholder = "08:00, 20:00"; times.ariaLabel = t("sim.times");
      name.addEventListener("input", () => { m.name = name.value; refreshDoseOptions(); schedule(); });
      times.addEventListener("input", () => { m.times = times.value; refreshDoseOptions(); schedule(); });
      const del = el("button", "btn btn-small btn-ghost", "✕"); del.type = "button"; del.title = t("sim.remove");
      del.addEventListener("click", () => {
        state.medicines.splice(mi, 1);
        state.attempts = state.attempts.filter((a) => a.mi !== mi).map((a) => ({ ...a, mi: a.mi > mi ? a.mi - 1 : a.mi }));
        renderEditors(); run();
      });
      row.append(name, times, del);
      meds.append(row);
    });

    // attempts
    const box = $("attempts");
    box.replaceChildren();
    state.attempts.forEach((a, ai) => {
      const row = el("div", "edit-row attempt-row");
      row.dataset.index = ai;
      const sel = el("select", "dose-select"); sel.ariaLabel = t("sim.which_dose");
      sel.addEventListener("change", () => {
        const [mi, t] = sel.value.split("|"); a.mi = +mi; a.dose_time = t; schedule();
      });
      const at = el("input"); at.type = "time"; at.value = a.at; at.ariaLabel = t("sim.pressed_at");
      at.addEventListener("input", () => { if (at.value) { a.at = at.value; schedule(); } });
      const del = el("button", "btn btn-small btn-ghost", "✕"); del.type = "button"; del.title = t("sim.remove");
      del.addEventListener("click", () => { state.attempts.splice(ai, 1); renderEditors(); run(); });
      const result = el("div", "attempt-result");
      row.append(el("span", "small muted", t("sim.dose")), sel, el("span", "small muted", t("sim.pressed_at")), at, del, result);
      box.append(row);
    });
    refreshDoseOptions();

    // settings
    const s = state.settings;
    $("s-early").value = s.early_minutes;
    $("s-grace").value = s.grace_minutes;
    $("s-gap").value = Math.round(s.gap_ratio * 100);
    $("now").value = state.now;
    showSettingLabels();
  }

  function refreshDoseOptions() {
    document.querySelectorAll(".attempt-row").forEach((row) => {
      const a = state.attempts[+row.dataset.index];
      const sel = row.querySelector("select");
      sel.replaceChildren();
      state.medicines.forEach((m, mi) => parseTimes(m.times).forEach((t) => {
        const o = el("option", "", `${m.name || window.t("sim.med_ph")} ${t}`);
        o.value = `${mi}|${t}`;
        sel.append(o);
      }));
      sel.value = `${a.mi}|${a.dose_time}`;
      if (sel.selectedIndex === -1 && sel.options.length) {   // dose no longer exists -> pick first
        sel.selectedIndex = 0;
        const [mi, t] = sel.value.split("|"); a.mi = +mi; a.dose_time = t;
      }
    });
  }

  function showSettingLabels() {
    const s = state.settings;
    renderRules();
    $("now-label").textContent = toHM(state.now);
    $("live-badge").hidden = !state.live;
    $("go-live").disabled = state.live;
    const offset = -new Date().getTimezoneOffset();
    const gmt = `GMT${offset >= 0 ? "+" : "−"}${Math.floor(Math.abs(offset) / 60)}${offset % 60 ? ":" + pad(Math.abs(offset) % 60) : ""}`;
    $("tz-label").textContent = t(state.live ? "sim.tz_live" : "sim.tz_manual", { tz: TZ || t("sim.this_device"), gmt });
  }

  // ------------------------------------------------------------ talk to Python
  let timer = null, requestNo = 0;
  function schedule() { clearTimeout(timer); timer = setTimeout(run, 150); }

  async function run() {
    showSettingLabels();
    const mine = ++requestNo;
    const body = {
      medicines: state.medicines.filter((m) => parseTimes(m.times).length)
        .map((m) => ({ name: m.name, times: m.times })),
      attempts: [],
      settings: state.settings,
      now: toHM(state.now),
      lang: window.LANG,
    };
    // medicines without valid times are left out, so re-map attempt indexes
    const keep = state.medicines.map((m) => parseTimes(m.times).length > 0);
    const newIndex = []; let k = 0;
    keep.forEach((ok, i) => { newIndex[i] = ok ? k++ : -1; });
    body.attempts = state.attempts.filter((a) => newIndex[a.mi] >= 0 && a.dose_time && a.at)
      .map((a) => ({ mi: newIndex[a.mi], dose_time: a.dose_time, at: a.at }));

    try {
      const res = await fetch("/api/simulate", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await res.json();
      if (mine !== requestNo) return;          // a newer request is on its way
      if (!res.ok) throw new Error(data.error || t("sim.failed"));
      $("error").hidden = true;
      renderResult(data, newIndex);
    } catch (e) {
      $("error").textContent = e.message;
      $("error").hidden = false;
    }
  }

  // ------------------------------------------------------------ outputs
  function renderResult(data, newIndex) {
    renderTimeline(data);

    $("status-time").textContent = t("sim.at", { time: data.now });
    const list = $("doses");
    list.replaceChildren();
    if (!data.doses.length) list.append(el("li", "muted", t("sim.no_meds")));
    data.doses.forEach((d) => {
      const li = el("li", "decision");
      li.append(el("span", "item-time", d.time));
      const main = el("div", "decision-main");
      main.append(el("strong", "", d.medicine), el("p", "why", d.reason));
      // Press "I took it" for this dose at the time shown on the clock.
      const origMi = newIndex.indexOf(d.mi);
      const press = el("button", "btn btn-small btn-go press-now", t("sim.press_now", { time: toHM(state.now) }));
      press.type = "button";
      press.addEventListener("click", () => {
        state.attempts.push({ mi: origMi, dose_time: d.time, at: toHM(state.now) });
        renderEditors();
        run();
      });
      main.append(press);
      li.append(main, el("span", `badge badge-${d.status}`, t(`st.${d.status}`)));
      list.append(li);
    });

    // attach each press result to its editor row (same order of equal presses)
    const pool = {};
    data.attempts.forEach((a) => { const key = `${a.mi}|${a.dose_time}|${a.at}`; (pool[key] = pool[key] || []).push(a); });
    document.querySelectorAll(".attempt-row").forEach((row) => {
      const a = state.attempts[+row.dataset.index];
      const box = row.querySelector(".attempt-result");
      const r = (pool[`${newIndex[a.mi]}|${a.dose_time}|${a.at}`] || []).shift();
      box.replaceChildren();
      if (!r) return;
      box.className = `attempt-result res-${r.result}`;
      box.append(el("strong", "", r.label), el("span", "", ` · ${r.reason}`));
    });

    $("alert-count").textContent = data.alerts.length;
    const alerts = $("alerts");
    alerts.replaceChildren();
    if (!data.alerts.length) alerts.append(el("li", "muted", t("sim.no_alerts")));
    data.alerts.forEach((a) => {
      const li = el("li", "alert-item");
      li.append(el("span", "item-time", a.at), el("div", "", ""));
      li.lastChild.append(el("p", "", a.text), el("p", "why", a.why));
      alerts.append(li);
    });

    const ICON = { reminder: "🔔", confirm: "✅", blocked: "⛔", missed: "⏰", alert: "📨" };
    const log = $("log");
    log.replaceChildren();
    if (!data.events.length) log.append(el("li", "muted", t("sim.no_events")));
    data.events.forEach((e) => {
      const li = el("li", `log-item log-${e.kind}`);
      li.append(el("span", "item-time", e.at), el("span", "log-icon", ICON[e.kind]));
      const body = el("div", "");
      body.append(el("p", "", e.text));
      if (e.why) body.append(el("p", "why", e.why));
      li.append(body);
      log.append(li);
    });
  }

  function renderTimeline(data) {
    const tl = $("timeline");
    tl.replaceChildren();
    const axis = el("div", "tl-axis");
    for (let h = 0; h <= 24; h += 3) {
      const t = el("span", "tl-tick" + (h % 6 ? " tl-minor" : ""), pad(h % 24) + ":00");
      t.style.left = pct(h * 60);
      axis.append(t);
    }
    tl.append(axis);

    data.medicines.forEach((m, mi) => {
      const row = el("div", "tl-row");
      row.append(el("div", "tl-label", m.name));
      const track = el("div", "tl-track");
      data.doses.filter((d) => d.mi === mi).forEach((d) => {
        const win = el("div", `tl-window st-${d.status}`);
        win.style.left = pct(d.t_open);
        win.style.width = `calc(${pct(d.t_deadline)} - ${pct(d.t_open)})`;
        win.title = `${d.time}: ${t(`st.${d.status}`)}. ${d.reason}`;
        track.append(win);
        const dot = el("div", "tl-dose");
        dot.style.left = pct(d.t_dose);
        track.append(dot);
      });
      data.attempts.filter((a) => a.mi === mi).forEach((a) => {
        const tick = el("div", `tl-press ${a.result === "ok" ? "ok" : a.result === "pending" ? "pending" : "bad"}`);
        tick.style.left = pct(toMin(a.at));
        tick.title = `${a.at}: ${a.label}. ${a.reason}`;
        track.append(tick);
      });
      row.append(track);
      row.append(el("div", "tl-gap small muted", t("sim.min_gap", { gap: m.min_gap })));
      tl.append(row);
    });

    const nowLine = el("div", "tl-now");
    nowLine.style.left = `calc(var(--tl-label) + (100% - var(--tl-label) - var(--tl-gap)) * ${state.now / DAY})`;
    tl.append(nowLine);
  }

  // ------------------------------------------------------------ controls
  $("add-med").addEventListener("click", () => {
    state.medicines.push({ name: t("sim.new_med"), times: "09:00" });
    renderEditors(); run();
  });
  $("add-attempt").addEventListener("click", () => {
    const m = state.medicines.findIndex((x) => parseTimes(x.times).length);
    if (m === -1) return;
    const t = parseTimes(state.medicines[m].times)[0];
    state.attempts.push({ mi: m, dose_time: t, at: toHM(state.now) });   // starts at the clock's time
    renderEditors(); run();
  });
  $("s-early").addEventListener("input", (e) => { state.settings.early_minutes = +e.target.value; schedule(); });
  $("s-grace").addEventListener("input", (e) => { state.settings.grace_minutes = +e.target.value; schedule(); });
  $("s-gap").addEventListener("input", (e) => { state.settings.gap_ratio = +e.target.value / 100; schedule(); });
  $("now").addEventListener("input", (e) => { state.now = +e.target.value; stop(); goManual(); schedule(); });
  $("go-live").addEventListener("click", goLive);
  // While live, keep the clock in step with real time.
  setInterval(() => {
    if (!state.live) return;
    const m = localMinutes();
    if (m !== state.now) { state.now = m; $("now").value = m; run(); }
  }, 10000);

  let playing = null;
  function stop() { clearInterval(playing); playing = null; $("play").textContent = t("sim.play"); }
  // Speed = "milliseconds per step|simulated minutes per step", e.g. "1000|5" = 5 min every second.
  function startTicking() {
    const [ms, step] = $("speed").value.split("|").map(Number);
    clearInterval(playing);
    playing = setInterval(() => {
      state.now = Math.min(DAY - 1, state.now + step);
      $("now").value = state.now;
      run();
      if (state.now >= DAY - 1) stop();
    }, ms);
  }
  $("play").addEventListener("click", () => {
    if (playing) return stop();
    goManual();
    if (state.now >= DAY - 10) state.now = 6 * 60;
    $("play").textContent = t("sim.pause");
    startTicking();
  });
  $("speed").addEventListener("change", () => { if (playing) startTicking(); });

  // Switch language without reloading (used by the browser-only preview, which has every language built in).
  window.sanakhRetranslate = function () {
    document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
    document.title = t("sim.page_title");
    $("play").textContent = playing ? t("sim.pause") : t("sim.play");
    renderPresets();
    renderEditors();
    renderTour();
    run();
  };

  renderPresets();
  renderTour();
  loadPreset(1);
  goLive();          // start on the viewer's real time in their own time zone
  // Opened from the home page's "2-minute tour" button (?tour=1): start the tour straight away.
  if (new URLSearchParams(location.search).has("tour")) {
    goStep(0);
    $("tour").scrollIntoView({ block: "start" });
  }
})();
