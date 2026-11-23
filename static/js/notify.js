// =============================================================================
//  PART 3 of 3 · THE SCREENS  (templates/ + static/)
//  This file shows PHONE NOTIFICATIONS: asks permission once, shows each
//  notification only once per day, and uses sw.js so buttons work.
//  See TEAM_GUIDE.md for who explains which part.
// =============================================================================
// Phone / desktop notifications, shared by the elder screen and the caregiver dashboard.
// Uses the service worker when possible: Android Chrome only allows notifications
// (and buttons on them, like "I took it") through a service worker.
(function () {
  "use strict";
  const ICON = "/static/img/icon.svg";
  let reg = null;
  const ready = ("serviceWorker" in navigator)
    ? navigator.serviceWorker.register("/sw.js").then((r) => navigator.serviceWorker.ready.then(() => (reg = r))).catch(() => null)
    : Promise.resolve(null);

  // Remember what we already notified about today, so a page reload doesn't repeat it.
  const dayKey = () => "sanakh-notified-" + new Date().toISOString().slice(0, 10);
  function seen() {
    try { return new Set(JSON.parse(localStorage.getItem(dayKey()) || "[]")); } catch (e) { return new Set(); }
  }
  function markSeen(key) {
    const s = seen(); s.add(key);
    try { localStorage.setItem(dayKey(), JSON.stringify([...s])); } catch (e) { /* storage off: fine */ }
  }

  window.SanakhNotify = {
    supported: () => "Notification" in window,
    permission: () => ("Notification" in window ? Notification.permission : "unsupported"),

    // Must be called from a tap (browsers only ask for permission after a user action).
    async request() {
      if (!("Notification" in window)) return "unsupported";
      if (Notification.permission === "default") {
        try { await Notification.requestPermission(); } catch (e) { /* old Safari */ }
      }
      return Notification.permission;
    },

    alreadySent: (key) => seen().has(key),
    markSent: markSeen,

    // Show a notification once per key. `actions` (buttons) only work through the service worker.
    async show(key, title, options) {
      if (!("Notification" in window) || Notification.permission !== "granted") return false;
      if (key && seen().has(key)) return false;
      if (key) markSeen(key);
      const opts = { icon: ICON, badge: ICON, ...options };
      await ready;
      try {
        if (reg) { await reg.showNotification(title, opts); return true; }
        delete opts.actions;
        new Notification(title, opts);   // desktop fallback without a service worker
        return true;
      } catch (e) { return false; }
    },
  };
})();
