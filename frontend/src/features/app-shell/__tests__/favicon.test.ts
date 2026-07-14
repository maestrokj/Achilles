import { afterEach, describe, expect, it } from "vitest";

import { applyFaviconAccent, buildFaviconSvg } from "../favicon";

describe("buildFaviconSvg", () => {
  it("spans a gradient around the accent", () => {
    const svg = buildFaviconSvg("#6366f1", false);
    expect(svg).toContain('stop-color="#797bf3"');
    expect(svg).toContain('stop-color="#4d50bc"');
  });

  it("picks the glyph ink by accent luminance", () => {
    expect(buildFaviconSvg("#6366f1", false)).toContain('stroke="#f8fafc"');
    expect(buildFaviconSvg("#fde047", true)).toContain('stroke="#1e293b"');
  });
});

describe("applyFaviconAccent", () => {
  afterEach(() => {
    document.querySelector('link[rel="icon"]')?.remove();
  });

  it("rewrites the icon link as a data URI", () => {
    const link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);

    applyFaviconAccent("#6366f1", false);

    expect(link.href).toBe(
      `data:image/svg+xml,${encodeURIComponent(buildFaviconSvg("#6366f1", false))}`,
    );
  });

  it("is a no-op without an icon link", () => {
    expect(() => {
      applyFaviconAccent("#6366f1", false);
    }).not.toThrow();
  });
});
