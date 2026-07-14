/* Doc nav — scroll restoration for the in-page "← back" link.

   The back link is a forward navigation (a new history entry), so the browser
   opens the target at the top. This script makes it behave like the browser's
   Back button: every page saves its scroll position on leave, a click on
   `a.back` flags the target page, and the target restores the saved position
   on load. sessionStorage is used (per-tab, survives file:// where
   document.referrer is empty); all storage access is try-wrapped so the docs
   degrade to plain links when storage is unavailable.

   Loaded with `defer` by the hub and every module page. The same script also
   renders the EN/RU language switch (see the second block below). */

(() => {
  const FLAG = "doc-nav:restore";
  const key = (path) => "doc-nav:scroll:" + path;

  // Save this page's scroll position when leaving it.
  addEventListener("pagehide", () => {
    try {
      sessionStorage.setItem(key(location.pathname), String(scrollY));
    } catch {
      /* storage unavailable — degrade to a plain link */
    }
  });

  // A plain click on the back link asks the target page to restore.
  // Modified clicks (new tab / window) keep default behavior untouched.
  document.addEventListener("click", (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) {
      return;
    }
    const back = e.target.closest("a.back");
    if (!back) return;
    try {
      sessionStorage.setItem(FLAG, new URL(back.href).pathname);
    } catch {
      /* storage unavailable — degrade to a plain link */
    }
  });

  // Restore when this load was flagged by a back-link click; the flag is
  // single-use, so any other navigation lands at the top as usual.
  try {
    const flagged = sessionStorage.getItem(FLAG) === location.pathname;
    sessionStorage.removeItem(FLAG);
    if (flagged) {
      const y = Number(sessionStorage.getItem(key(location.pathname)));
      if (y > 0) scrollTo(0, y);
    }
  } catch {
    /* storage unavailable — degrade to a plain link */
  }
})();

/* Language switch — EN is the default (bare path), RU lives under /ru/.

   The current language is read from <html lang>; the counterpart URL is derived
   by adding or removing a single "ru" path segment, so the same file works both
   on GitHub Pages (/Achilles/…) and from the local file system (…/docs/…),
   whatever the base prefix is. The button is injected here so no page markup
   has to change. */
(() => {
  const isRu = document.documentElement.lang === "ru";

  // EN path -> RU path: insert "ru" before the content root, or (on the home
  // page, which has no content segment) before the trailing file name.
  const toRu = (p) => {
    const m = p.match(/\/(architecture|presentation|get-started)\//);
    if (m) return p.slice(0, m.index) + "/ru" + p.slice(m.index);
    return p.replace(/\/([^/]*)$/, (_, last) => (last ? "/ru/" + last : "/ru/"));
  };
  // RU path -> EN path: drop the first "ru" segment.
  const toEn = (p) => p.replace(/\/ru(?=\/|$)/, "") || "/";

  const here = location.pathname;
  const enHref = (isRu ? toEn(here) : here) + location.hash;
  const ruHref = (isRu ? here : toRu(here)) + location.hash;

  const style = document.createElement("style");
  style.textContent =
    ".lang-switch{position:fixed;top:12px;right:12px;z-index:1000;display:flex;" +
    "border:2px solid var(--border,#ddd);border-radius:999px;overflow:hidden;" +
    "font:600 13px/1 system-ui,-apple-system,sans-serif;" +
    "background:var(--surface,#fff);box-shadow:0 2px 10px rgba(0,0,0,.10)}" +
    ".lang-switch a{padding:6px 13px;color:var(--text-dim,#555);" +
    "text-decoration:none;letter-spacing:.02em}" +
    ".lang-switch a[aria-current]{background:var(--accent,#b86030);color:#fff}" +
    ".lang-switch a:not([aria-current]):hover{background:var(--bg,#f0f0f0);color:var(--text,#222)}";
  document.head.appendChild(style);

  const link = (href, label, active) =>
    `<a href="${href}"${active ? ' aria-current="page"' : ""}>${label}</a>`;
  const box = document.createElement("nav");
  box.className = "lang-switch";
  box.setAttribute("aria-label", isRu ? "Язык" : "Language");
  box.innerHTML = link(enHref, "EN", !isRu) + link(ruHref, "RU", isRu);
  document.body.appendChild(box);
})();
