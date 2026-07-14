import { useEffect } from "react";
import { useLocation } from "react-router-dom";

/** Bring a `#section` deep-link into view and pulse it once on arrival.
 *
 * React-router does not scroll to the URL hash on its own, and admin cards mount
 * behind a skeleton while their data loads — so a naive one-shot scroll fires
 * before the target exists. This polls briefly until the element with the hash id
 * appears (or the window lapses), then scrolls its scroll-parent to it and adds
 * the `.section-highlight` class for a gentle ring/tint pulse. The class clears
 * itself when the animation ends (or after a fallback timeout for reduced motion).
 *
 * Call once from a page that owns hash targets; give each target `id` + `scroll-mt-*`. */
export function useHashTarget() {
  const { hash } = useLocation();

  useEffect(() => {
    if (!hash) return;
    const id = decodeURIComponent(hash.slice(1));

    let cancelled = false;
    let timer: number | undefined;
    let attempts = 0;

    const clearHighlight = (el: Element) => {
      el.classList.remove("section-highlight");
    };

    const tryScroll = () => {
      if (cancelled) return;
      const el = document.getElementById(id);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        el.classList.add("section-highlight");
        el.addEventListener(
          "animationend",
          () => {
            clearHighlight(el);
          },
          { once: true },
        );
        // Reduced motion suppresses the animation → clear the class on a timer too.
        timer = window.setTimeout(() => {
          clearHighlight(el);
        }, 2400);
        return;
      }
      // Wait out the skeleton → content swap, then give up (~2.5s total).
      if (attempts++ < 20) timer = window.setTimeout(tryScroll, 120);
    };

    const raf = requestAnimationFrame(tryScroll);
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [hash]);
}
