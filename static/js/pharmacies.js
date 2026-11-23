// Pharmacy finder: where am I → nearby pharmacies (OpenStreetMap) → cheapest reported price.
// The map is drawn here from OpenStreetMap tiles (no map library needed).
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const DEFAULT = { lat: 47.9185, lon: 106.9176 };          // central Ulaanbaatar, if location is not allowed
  const OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"];
  const RADII = [1500, 4000, 10000];                         // widen the search if few pharmacies are found
  const SHOW = 15;                                           // how many to list

  let here = null, radius = 0, places = [], prices = {}, selectedId = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  const status = (text) => { $("status").textContent = text; };
  const money = (n) => new Intl.NumberFormat(window.LANG || "en").format(n);

  // Distance in metres between two points (haversine formula).
  function distance(a, b) {
    const R = 6371000, rad = Math.PI / 180;
    const dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const fmtDist = (m) => (m < 1000 ? t("ph.m", { n: Math.round(m / 10) * 10 }) : t("ph.km", { n: (m / 1000).toFixed(1) }));
  const walkMin = (m) => Math.max(1, Math.round(m / 70));   // older people walk about 70 m a minute

  // ------------------------------------------------------------------ map
  // Web Mercator, the projection used by OpenStreetMap tiles.
  const TILE = 256;
  function toWorld(lat, lon, z) {
    const size = TILE * 2 ** z, r = (lat * Math.PI) / 180;
    return { x: ((lon + 180) / 360) * size, y: ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * size };
  }
  function fromWorld(x, y, z) {
    const size = TILE * 2 ** z, n = Math.PI - (2 * Math.PI * y) / size;
    return { lat: (180 / Math.PI) * Math.atan(Math.sinh(n)), lon: (x / size) * 360 - 180 };
  }

  const map = {
    box: $("map"), z: 15, center: { ...DEFAULT }, pins: [],
    draw() {
      const w = this.box.clientWidth, h = this.box.clientHeight;
      const c = toWorld(this.center.lat, this.center.lon, this.z);
      const left = c.x - w / 2, top = c.y - h / 2, n = 2 ** this.z;
      const layer = el("div", "map-layer");
      for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + w) / TILE); tx++) {
        for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + h) / TILE); ty++) {
          if (ty < 0 || ty >= n) continue;
          const img = el("img", "map-tile");
          img.alt = ""; img.draggable = false;
          img.src = `https://tile.openstreetmap.org/${this.z}/${((tx % n) + n) % n}/${ty}.png`;
          img.style.left = `${tx * TILE - left}px`;
          img.style.top = `${ty * TILE - top}px`;
          layer.append(img);
        }
      }
      this.pins.forEach((p) => {
        const at = toWorld(p.lat, p.lon, this.z);
        const pin = el(p.button ? "button" : "div", `pin ${p.cls || ""}`, p.label || "");
        if (p.button) { pin.type = "button"; pin.title = p.title || ""; pin.addEventListener("click", p.onClick); }
        pin.style.left = `${at.x - left}px`;
        pin.style.top = `${at.y - top}px`;
        layer.append(pin);
      });
      const controls = el("div", "map-controls");
      [["+", "ph.zoom_in", () => this.zoom(1)], ["−", "ph.zoom_out", () => this.zoom(-1)],
       ["⌖", "ph.recenter", () => { if (here) { this.center = { ...here }; this.draw(); } }]].forEach(([txt, key, fn]) => {
        const b = el("button", "map-btn", txt);
        b.type = "button"; b.title = t(key); b.setAttribute("aria-label", t(key));
        b.addEventListener("click", fn);
        controls.append(b);
      });
      this.box.replaceChildren(layer, controls);
    },
    zoom(d) { this.z = Math.min(18, Math.max(11, this.z + d)); this.draw(); },
    // Pick the closest zoom that still shows all the points.
    fit(points) {
      const w = this.box.clientWidth - 60, h = this.box.clientHeight - 60;
      for (let z = 17; z >= 11; z--) {
        const xy = points.map((p) => toWorld(p.lat, p.lon, z));
        const xs = xy.map((p) => p.x), ys = xy.map((p) => p.y);
        if (Math.max(...xs) - Math.min(...xs) <= w && Math.max(...ys) - Math.min(...ys) <= h) {
          const mid = fromWorld((Math.max(...xs) + Math.min(...xs)) / 2, (Math.max(...ys) + Math.min(...ys)) / 2, z);
          this.z = z; this.center = mid; break;
        }
      }
      this.draw();
    },
  };

  // Drag to move the map (mouse or finger).
  (function enableDrag() {
    let start = null;
    map.box.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button")) return;
      start = { x: e.clientX, y: e.clientY, c: toWorld(map.center.lat, map.center.lon, map.z) };
      map.box.setPointerCapture(e.pointerId);
    });
    map.box.addEventListener("pointermove", (e) => {
      if (!start) return;
      map.center = fromWorld(start.c.x - (e.clientX - start.x), start.c.y - (e.clientY - start.y), map.z);
      if (!map.pending) { map.pending = true; requestAnimationFrame(() => { map.pending = false; map.draw(); }); }
    });
    ["pointerup", "pointercancel"].forEach((ev) => map.box.addEventListener(ev, () => { start = null; }));
  })();

  // ------------------------------------------------------------------ data
  async function findPharmacies() {
    const r = RADII[radius];
    status(t("ph.searching"));
    // Privacy: only a rounded position (3 decimals ≈ 100 m) leaves this phone; exact distances are worked out here.
    const lat = here.lat.toFixed(3), lon = here.lon.toFixed(3);
    const query = `[out:json][timeout:20];nwr["amenity"="pharmacy"](around:${r},${lat},${lon});out center tags 80;`;
    let data = null;
    for (const url of OVERPASS) {                            // second server if the first is busy
      try {
        const res = await fetch(url, { method: "POST", body: "data=" + encodeURIComponent(query),
                                       headers: { "Content-Type": "application/x-www-form-urlencoded" } });
        if (res.ok) { data = await res.json(); break; }
      } catch (e) { /* try the next server */ }
    }
    if (!data) { status(t("ph.err_load")); return; }

    const lang = window.LANG || "en";
    places = data.elements.map((e) => {
      const tags = e.tags || {};
      return {
        id: `${e.type}/${e.id}`,
        lat: e.lat ?? (e.center && e.center.lat), lon: e.lon ?? (e.center && e.center.lon),
        name: tags[`name:${lang}`] || tags.name || tags["name:mn"] || tags["name:en"] || t("ph.unnamed"),
        phone: tags.phone || tags["contact:phone"] || "",
        hours: tags.opening_hours || "",
        street: [tags["addr:street"], tags["addr:housenumber"]].filter(Boolean).join(" "),
      };
    }).filter((p) => p.lat != null && p.lon != null);
    places.forEach((p) => { p.dist = distance(here, p); });

    if (places.length < 3 && radius < RADII.length - 1) { radius++; return findPharmacies(); }
    await loadPrices();
    render();
  }

  async function loadPrices() {
    prices = {};
    const med = $("medicine").value.trim();
    if (!med || !places.length) return;
    try {
      const ids = places.map((p) => p.id).join(",");
      prices = await (await fetch(`/api/prices?medicine=${encodeURIComponent(med)}&ids=${encodeURIComponent(ids)}`)).json();
    } catch (e) { prices = {}; }
  }

  // Cheapest reported price first; without prices, nearest first.
  function ordered() {
    return [...places].sort((a, b) => {
      const pa = prices[a.id], pb = prices[b.id];
      if (pa && pb) return pa.price - pb.price || a.dist - b.dist;
      if (pa) return -1;
      if (pb) return 1;
      return a.dist - b.dist;
    }).slice(0, SHOW);
  }

  function when(iso) {
    const days = Math.floor((Date.now() - new Date(iso.replace(" ", "T") + "Z")) / 86400000);
    return days <= 0 ? t("ph.today") : t("ph.days", { n: days });
  }

  // ------------------------------------------------------------------ list
  function render() {
    const list = $("list");
    list.replaceChildren();
    const med = $("medicine").value.trim();
    const shown = ordered();
    if (!shown.length) { status(t("ph.none")); map.pins = [{ ...here, cls: "me" }]; map.draw(); return; }
    status((here.approx ? t("ph.no_location") + " " : "") +
           t("ph.found", { n: places.length, km: (RADII[radius] / 1000).toFixed(1) }));
    const cheapest = Math.min(...shown.map((p) => (prices[p.id] ? prices[p.id].price : Infinity)));

    shown.forEach((p, i) => {
      const price = prices[p.id];
      const li = el("li", "ph-card" + (p.id === selectedId ? " on" : ""));
      li.id = `ph-${p.id.replace("/", "-")}`;
      const head = el("div", "ph-head");
      head.append(el("span", "ph-num", String(i + 1)), el("strong", "ph-name", p.name));
      if (price && price.price === cheapest) head.append(el("span", "badge badge-taken", t("ph.cheapest")));
      li.append(head);
      li.append(el("p", "ph-dist", `${fmtDist(p.dist)} · ${t("ph.walk", { n: walkMin(p.dist) })}`));
      if (p.street) li.append(el("p", "small muted", p.street));
      if (p.hours) li.append(el("p", "small muted", t("ph.hours", { hours: p.hours })));
      if (med) {
        li.append(price
          ? el("p", "ph-price", `${t("ph.price", { price: money(price.price), medicine: med })} · ${t("ph.reported", { when: when(price.reported_at) })}`)
          : el("p", "small muted", t("ph.no_price", { medicine: med })));
      }

      const actions = el("div", "ph-actions");
      const go = el("a", "btn btn-primary", t("ph.directions"));
      go.href = `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lon}&travelmode=walking`;
      go.target = "_blank"; go.rel = "noopener";
      actions.append(go);
      if (p.phone) {
        const call = el("a", "btn", t("ph.call"));
        call.href = `tel:${p.phone.split(";")[0].replace(/[^\d+]/g, "")}`;
        actions.append(call);
      }
      const add = el("button", "btn", t("ph.report"));
      add.type = "button";
      add.addEventListener("click", () => openReport(li, p));
      actions.append(add);
      li.append(actions);
      list.append(li);
    });

    map.pins = [{ ...here, cls: "me", title: "" },
      ...shown.map((p, i) => ({
        lat: p.lat, lon: p.lon, label: String(i + 1), button: true, title: p.name,
        cls: (prices[p.id] && prices[p.id].price === cheapest ? "cheap" : "") + (p.id === selectedId ? " on" : ""),
        onClick: () => select(p.id),
      }))];
    map.fit([here, ...shown.slice(0, 6)]);
  }

  function select(id) {
    selectedId = id;
    render();
    const card = $(`ph-${id.replace("/", "-")}`);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // Small form: "What did you pay?"
  function openReport(li, p) {
    if (li.querySelector(".ph-form")) return;
    const form = el("form", "ph-form");
    form.append(el("p", "", t("ph.report_title", { pharmacy: p.name })));
    const med = el("input"); med.required = true; med.value = $("medicine").value.trim();
    med.placeholder = t("ph.medicine_ph"); med.setAttribute("aria-label", t("ph.medicine")); med.setAttribute("list", "med-list");
    const price = el("input"); price.type = "number"; price.min = "1"; price.step = "1"; price.required = true;
    price.inputMode = "numeric"; price.placeholder = t("ph.price_label"); price.setAttribute("aria-label", t("ph.price_label"));
    const msg = el("p", "small");
    const save = el("button", "btn btn-primary", t("ph.save")); save.type = "submit";
    const cancel = el("button", "btn", t("ph.cancel")); cancel.type = "button";
    cancel.addEventListener("click", () => form.remove());
    const row = el("div", "ph-actions"); row.append(save, cancel);
    form.append(med, price, row, msg);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const res = await fetch("/api/prices", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ osm_id: p.id, pharmacy: p.name, medicine: med.value, price: price.value }),
      }).catch(() => null);
      const data = res ? await res.json().catch(() => ({})) : {};
      if (!res || !res.ok) { msg.className = "small error"; msg.textContent = data.error || t("ph.err_load"); return; }
      $("medicine").value = med.value;
      await loadPrices();
      render();
      status(t("ph.thanks"));   // after render(), which rewrites the status line
    });
    li.append(form);
    price.focus();
  }

  // ------------------------------------------------------------------ start
  function start(position) {
    here = position;
    radius = 0;
    map.center = { lat: here.lat, lon: here.lon };
    map.pins = [{ ...here, cls: "me" }];
    map.draw();
    findPharmacies();
  }

  $("locate").addEventListener("click", () => {
    if (!navigator.geolocation) return start({ ...DEFAULT, approx: true });
    status(t("ph.locating"));
    navigator.geolocation.getCurrentPosition(
      (pos) => start({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      () => start({ ...DEFAULT, approx: true }),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
  });

  // Changing the medicine re-sorts by its prices.
  let typing = null;
  $("medicine").addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(async () => { if (places.length) { await loadPrices(); render(); } }, 400);
  });
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", async () => {
    $("medicine").value = c.dataset.med;
    if (places.length) { await loadPrices(); render(); }
  }));

  map.draw();
  status(t("ph.start_hint"));
})();
