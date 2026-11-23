// =============================================================================
//  PART 3 of 3 · THE SCREENS  (templates/ + static/)
//  This file draws the MONTHLY LOG: a green / yellow / red calendar and,
//  when you tap a day, every dose and event of that day.
//  See TEAM_GUIDE.md for who explains which part.
// =============================================================================
// Monthly log: calendar coloured by how many doses were taken, plus the detail of one day.
(function () {
  "use strict";
  const LOG = window.MONTH_LOG;
  const $ = (id) => document.getElementById(id);
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  const [year, month] = LOG.month.split("-").map(Number);
  const asDate = (iso) => new Date(iso + "T12:00:00");   // noon: no time-zone edge cases

  // Month name and weekday names in the page's language.
  const title = window.fmtDate.monthYear(year, month);
  $("month-title").textContent = title.charAt(0).toUpperCase() + title.slice(1);   // "ноябрь" -> "Ноябрь"

  // ---------------------------------------------------------------- summary
  const s = LOG.summary;
  const tiles = [
    [s.adherence === null ? "–" : `${s.adherence}%`, t("log.adherence")],
    [s.on_time, t("log.on_time")],
    [s.late, t("log.late")],
    [s.missed, t("log.missed")],
    [s.blocked, t("log.blocked")],
  ];
  tiles.forEach(([value, label], i) => {
    const tile = el("div", "tile" + (i === 3 && s.missed ? " tile-bad" : i === 4 && s.blocked ? " tile-warn" : ""));
    tile.append(el("div", "tile-value", String(value)), el("div", "tile-label", label));
    $("summary").append(tile);
  });

  // ---------------------------------------------------------------- calendar
  function level(day) {
    if (!day.total) return "none";
    if (day.taken === day.total && !day.late) return "good";
    if (day.taken / day.total >= 0.5) return "mixed";
    return "bad";
  }

  const cal = $("calendar");
  // Weekday header, Monday first.
  for (let i = 0; i < 7; i++) cal.append(el("div", "cal-head", window.fmtDate.weekdayShort(i)));
  const offset = (asDate(LOG.days[0].day).getDay() + 6) % 7;
  for (let i = 0; i < offset; i++) cal.append(el("div", "cal-empty"));

  let selected = null;
  LOG.days.forEach((day) => {
    const future = day.day > LOG.today;
    const cell = el("button", `cal-day lv-${future ? "future" : level(day)}${day.day === LOG.today ? " today" : ""}`);
    cell.type = "button";
    cell.append(el("span", "cal-num", String(+day.day.slice(8))));
    if (day.total) cell.append(el("span", "cal-count", `${day.taken}/${day.total}`));
    if (day.events.some((e) => e.kind === "duplicate" || e.kind === "too_close")) cell.append(el("span", "cal-flag", "✋"));
    cell.addEventListener("click", () => show(day, cell));
    cal.append(cell);
    if (day.day === LOG.today) selected = [day, cell];
  });

  // ---------------------------------------------------------------- one day
  const EVENT_LABEL = { taken: "st.taken", taken_late: "st.taken_late", duplicate: "res.duplicate",
                        too_close: "res.too_close", too_early: "res.too_early", missed: "st.missed",
                        alert_sent: "cg.alert_sent", alert_failed: "cg.alert_failed", alert_off: "cg.alert_off" };
  function show(day, cell) {
    document.querySelectorAll(".cal-day.on").forEach((n) => n.classList.remove("on"));
    cell.classList.add("on");
    $("day-title").textContent = window.fmtDate.dayLong(asDate(day.day));
    const box = $("day-detail");
    box.replaceChildren();
    if (!day.doses.length) { box.append(el("p", "muted", t("log.no_data"))); return; }

    const list = el("ul", "today");
    day.doses.forEach((d) => {
      const li = el("li", "today-item");
      li.append(el("span", "item-time", d.time));
      const main = el("span", "item-name", d.name);
      if (d.dose) main.append(el("span", "muted small", ` · ${d.dose}`));
      li.append(main, el("span", `badge badge-${d.status}`, d.taken_at ? `${t(`st.${d.status}`)} ${d.taken_at}` : t(`st.${d.status}`)));
      list.append(li);
    });
    box.append(list);

    if (day.events.length) {
      box.append(el("h3", "", t("log.events")));
      const ev = el("ol", "log");
      day.events.forEach((e) => {
        const li = el("li", `log-item log-${e.kind === "duplicate" || e.kind === "too_close" || e.kind === "too_early" ? "blocked" : e.kind === "missed" || e.kind === "alert_failed" ? "missed" : "confirm"}`);
        li.append(el("span", "item-time", e.at));
        li.append(el("p", "", `${e.name} ${e.time}: ${t(EVENT_LABEL[e.kind] || e.kind)}`));
        ev.append(li);
      });
      box.append(ev);
    }
  }
  if (selected) show(...selected);
})();
