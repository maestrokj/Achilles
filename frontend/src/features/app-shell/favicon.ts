/** Redraws the favicon in the org accent: the same mark as the static
 * /favicon.svg, rebuilt as an SVG data URI once branding is known. The static
 * file stays baked in the seed accent (#6366f1) for the pre-React frame. */

const GLYPH_ON_DARK_ACCENT = "#f8fafc";
const GLYPH_ON_LIGHT_ACCENT = "#1e293b";
// Gradient span around the accent: a lighter top and darker bottom keep the
// mark readable on both the light and dark tab strips.
const TOP_LIGHTEN = 0.14;
const BOTTOM_DARKEN = 0.22;

/** Mixes a #rrggbb hex toward white (amount > 0) or black (amount < 0). */
function shade(hex: string, amount: number): string {
  const target = amount > 0 ? 255 : 0;
  const ratio = Math.abs(amount);
  const channel = (offset: number) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16);
    return Math.round(value + (target - value) * ratio)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${channel(1)}${channel(3)}${channel(5)}`;
}

export function buildFaviconSvg(accent: string, lightAccent: boolean): string {
  const glyph = lightAccent ? GLYPH_ON_LIGHT_ACCENT : GLYPH_ON_DARK_ACCENT;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">` +
    `<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="${shade(accent, TOP_LIGHTEN)}"/>` +
    `<stop offset="1" stop-color="${shade(accent, -BOTTOM_DARKEN)}"/>` +
    `</linearGradient></defs>` +
    `<rect x="2" y="2" width="60" height="60" rx="15" fill="url(#g)"/>` +
    `<g fill="none" stroke="${glyph}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round">` +
    `<path d="M20.5 45 32 20l11.5 25"/><path d="M26.5 37.5h11"/>` +
    `</g></svg>`
  );
}

export function applyFaviconAccent(accent: string, lightAccent: boolean): void {
  const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) return;
  const href = `data:image/svg+xml,${encodeURIComponent(buildFaviconSvg(accent, lightAccent))}`;
  if (link.href !== href) link.href = href;
}
