// =============================================================================
//  PART 3 of 3 · THE SCREENS  (templates/ + static/)
//  This file runs IN THE BACKGROUND of the phone: keeps the app's files for
//  offline use, and handles taps on notifications ("✓ I took it").
//  See TEAM_GUIDE.md for who explains which part.
// =============================================================================
// Service worker: lets phones install Sanakh to the home screen, and handles
// notifications (including the "✓ I took it" button on a reminder).
// It caches the app shell (CSS, JS, icon) but always fetches dose data live.
const CACHE = "sanakh-v3";
const SHELL = ["/static/css/style.css", "/static/js/elder.js", "/static/js/notify.js", "/static/js/i18n.js", "/static/img/icon.svg"];

// WHAT THIS DOES (install / activate / fetch): save the CSS, JS and icon so
// the screen still opens with a bad connection; dose data is always fetched live.
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || !url.pathname.startsWith("/static/")) return;
  // Network first, cache as fallback, so updates show up straight away.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy));
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});

// Tapping a notification. data = { url, code, id, msgs } is set by the page that showed it;
// msgs holds the already-translated texts for every possible result.
self.addEventListener("notificationclick", (event) => {
  const note = event.notification;
  const data = note.data || {};
  note.close();

  if (event.action === "take" && data.code && data.id) {
    // Same API as the big button, so the same double-dose rules apply on the server.
    const msgs = data.msgs || {};
    event.waitUntil(
      fetch(`/api/e/${data.code}/doses/${data.id}/take`, { method: "POST" })
        .then((r) => r.json())
        .then((res) => {
          const time = (res.result === "too_close" ? res.allowed_after : res.dose && res.dose.taken_at) || "";
          const text = (msgs[res.result] || "").replace("{time}", time);
          return self.registration.showNotification(msgs.title || "Sanakh", {
            body: text, tag: note.tag, icon: "/static/img/icon.svg", data: { url: data.url },
          });
        })
        .catch(() => self.registration.showNotification(msgs.title || "Sanakh", {
          body: msgs.fail || "", tag: note.tag, icon: "/static/img/icon.svg", data: { url: data.url },
        }))
    );
    return;
  }

  // Otherwise open (or focus) the right screen.
  const target = data.url || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (new URL(c.url).pathname === target && "focus" in c) return c.focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
