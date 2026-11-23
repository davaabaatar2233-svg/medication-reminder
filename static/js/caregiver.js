// =============================================================================
//  PART 3 of 3 · THE SCREENS  (templates/ + static/)
//  This file runs the FAMILY'S DASHBOARD: today's doses with the reason for
//  each status, and a phone notification when a dose is missed or late.
//  See TEAM_GUIDE.md for who explains which part.
// =============================================================================
// Caregiver dashboard: live "Today" list, refreshed every 20 seconds.
(function () {
  "use strict";
  const CODE = document.body.dataset.code;
  const LABEL = (status) => t(`st.${status}`);

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Notify this device when a dose is missed, or taken late after a missed alert.
  // Events that already happened before the page was opened are not announced.
  let firstLoad = true;
  function notifyCaregiver(data) {
    data.doses.forEach((d) => {
      let key = null, title = "", body = "";
      if (d.status === "missed") {
        key = `cg:${CODE}:${d.id}:missed`;
        title = t("nt.cg_missed_title", { elder: data.elder, name: d.name });
        body = t("nt.cg_missed_body", { time: d.time });
      } else if (d.status === "taken_late") {
        key = `cg:${CODE}:${d.id}:late`;
        title = t("nt.cg_late_title", { elder: data.elder, name: d.name });
        body = t("nt.cg_late_body", { time: d.taken_at });
      }
      if (!key) return;
      if (firstLoad) { window.SanakhNotify.markSent(key); return; }
      window.SanakhNotify.show(key, title, { body, tag: `cg-${d.id}`, renotify: true, data: { url: location.pathname } });
    });
    firstLoad = false;
  }

  function renderNotifyButton() {
    const b = document.getElementById("cg-notify");
    const p = window.SanakhNotify.permission();
    b.textContent = t({ granted: "nt.on", denied: "nt.blocked", unsupported: "nt.unsupported" }[p] || "nt.cg_enable");
    b.disabled = p !== "default";
  }
  document.getElementById("cg-notify").addEventListener("click", async () => {
    const p = await window.SanakhNotify.request();
    renderNotifyButton();
    if (p === "granted") window.SanakhNotify.show(null, "Sanakh", { body: t("nt.test"), tag: "sanakh-test" });
  });
  renderNotifyButton();

  // WHAT THIS DOES: every 20 s, fetch today's doses and redraw the list
  // (time, medicine, the Brain's reason, and a coloured status badge).
  async function load() {
    try {
      const res = await fetch(`/api/e/${CODE}/today`, { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      notifyCaregiver(data);
      document.getElementById("now").textContent = `· ${data.now}`;
      const list = document.getElementById("today");
      list.replaceChildren();
      if (data.doses.length === 0) {
        list.append(el("li", "muted", t("cg.no_doses")));
        return;
      }
      data.doses.forEach((d) => {
        const li = el("li", "today-item");
        li.append(el("span", "item-time", d.time));
        const main = el("span", "item-name", d.name);
        if (d.dose) main.append(el("span", "muted small", ` · ${d.dose}`));
        main.append(el("span", "why", d.reason));   // the rule's explanation
        li.append(main);
        let label = LABEL(d.status);
        if (d.taken_at) label = `${LABEL(d.status)} ${d.taken_at}`;
        if (d.status === "missed") {   // say what really happened to the Telegram alert
          const key = { sent: "cg.alert_sent", failed: "cg.alert_failed", retry: "cg.alert_retry", not_configured: "cg.alert_off" }[d.alert_status];
          if (key) label = t(key);
        }
        li.append(el("span", `badge badge-${d.status}`, label));
        list.append(li);
      });
    } catch (e) { /* try again next time */ }
  }

  load();
  setInterval(load, 20000);
})();
