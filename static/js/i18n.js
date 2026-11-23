// =============================================================================
//  PART 3 of 3 · THE SCREENS  (templates/ + static/)
//  This file TRANSLATES in the browser: t("key") -> text in the page's language.
//  See TEAM_GUIDE.md for who explains which part.
// =============================================================================
// Translations in the browser. The page sets window.I18N (strings for the current
// language, from languages/<lang>.json) and window.LANG before this file runs.
(function () {
  "use strict";
  // t("el.next", { name: "Metformin", time: "20:00" }) -> "Next: Metformin at 20:00"
  window.t = function (key, params) {
    const dict = window.I18N || {};
    const text = dict[key] !== undefined ? dict[key] : key;
    if (!params) return text;
    return text.replace(/\{(\w+)\}/g, (m, k) => (params[k] === undefined || params[k] === null ? m : String(params[k])));
  };

  // Dates in the page's language. Browsers lack some languages' month names
  // (Chrome shows Mongolian November as "M11"), so a language can supply its own
  // names with the optional "date.*" keys; otherwise the browser's Intl is used.
  const own = (key) => (window.I18N || {})[key];
  const list = (key) => own(key).split(",");
  window.fmtDate = {
    monthYear(year, month) {   // month 1-12
      if (own("date.months")) return t("date.month_year", { year, month: list("date.months")[month - 1] });
      return new Intl.DateTimeFormat(window.LANG, { month: "long", year: "numeric" }).format(new Date(year, month - 1, 15));
    },
    weekdayShort(i) {          // 0 = Monday
      if (own("date.weekdays_short")) return list("date.weekdays_short")[i];
      return new Intl.DateTimeFormat(window.LANG, { weekday: "short" }).format(new Date(2024, 0, 1 + i));
    },
    dayLong(date, timeZone) {  // e.g. "Wednesday, November 4"
      if (own("date.months")) {
        const parts = {};
        new Intl.DateTimeFormat("en-US", { timeZone, year: "numeric", month: "numeric", day: "numeric", weekday: "short" })
          .formatToParts(date).forEach((p) => { parts[p.type] = p.value; });
        const wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(parts.weekday);
        return t("date.day_long", { month: list("date.months")[+parts.month - 1], day: +parts.day,
                                    weekday: list("date.weekdays_long")[wd] });
      }
      return new Intl.DateTimeFormat(window.LANG, { timeZone, weekday: "long", day: "numeric", month: "long" }).format(date);
    },
  };

  // Language menu: go to /lang/<code>, which saves the choice and comes back here.
  // (The browser-only preview replaces this with an in-page switch.)
  document.addEventListener("DOMContentLoaded", () => {
    const menu = document.getElementById("lang-menu");
    if (!menu || window.SANAKH_PREVIEW) return;
    menu.addEventListener("change", () => {
      location.href = `/lang/${menu.value}?next=${encodeURIComponent(location.pathname)}`;
    });
  });
})();
