import { afterEach, describe, expect, it } from "vitest";

import {
  formatBytes,
  formatDateTime,
  formatNumber,
  setOrgDateTimePrefs,
  setUserDateTimePrefs,
} from "@/lib/format";

const ISO = "2026-07-04T12:30:00Z";

afterEach(() => {
  setOrgDateTimePrefs({});
  setUserDateTimePrefs({});
});

describe("formatDateTime with org prefs", () => {
  it("renders each backend date format layout", () => {
    setOrgDateTimePrefs({ timeZone: "UTC", dateFormat: "DD.MM.YYYY" });
    expect(formatDateTime(ISO, "en-US")).toContain("04.07.2026");
    setOrgDateTimePrefs({ timeZone: "UTC", dateFormat: "MM/DD/YYYY" });
    expect(formatDateTime(ISO, "en-US")).toContain("07/04/2026");
    setOrgDateTimePrefs({ timeZone: "UTC", dateFormat: "YYYY-MM-DD" });
    expect(formatDateTime(ISO, "en-US")).toContain("2026-07-04");
  });

  it("honors the org timezone and does not serve a stale formatter", () => {
    setOrgDateTimePrefs({ timeZone: "UTC", dateFormat: "YYYY-MM-DD" });
    expect(formatDateTime(ISO, "en-US")).toContain("12:30");
    setOrgDateTimePrefs({ timeZone: "Asia/Tokyo", dateFormat: "YYYY-MM-DD" });
    expect(formatDateTime(ISO, "en-US")).toContain("21:30");
  });

  it("falls back to the browser locale without prefs", () => {
    expect(formatDateTime(ISO, "en-US")).toContain("26");
  });
});

describe("personal prefs over org defaults (personal → org → browser)", () => {
  it("renders in the personal timezone, outranking the org zone", () => {
    setOrgDateTimePrefs({ timeZone: "Africa/Lusaka", dateFormat: "YYYY-MM-DD" });
    expect(formatDateTime(ISO, "en-US")).toContain("14:30"); // CAT (UTC+2)
    setUserDateTimePrefs({ timeZone: "Europe/Moscow" });
    expect(formatDateTime(ISO, "en-US")).toContain("15:30"); // MSK (UTC+3)
  });

  it("falls through to the org value when a personal field is unset", () => {
    setOrgDateTimePrefs({ timeZone: "UTC", dateFormat: "DD.MM.YYYY" });
    // Personal sets only the timezone; the org date layout still applies.
    setUserDateTimePrefs({ timeZone: "Asia/Tokyo" });
    const out = formatDateTime(ISO, "en-US");
    expect(out).toContain("04.07.2026");
    expect(out).toContain("21:30");
  });
});

describe("formatNumber", () => {
  it("groups digits per locale", () => {
    expect(formatNumber(1234567, "en-US")).toBe("1,234,567");
  });
});

describe("formatBytes", () => {
  it("scales through the binary units", () => {
    expect(formatBytes(512)).toBe("512");
    expect(formatBytes(2048)).toBe("2 kB");
    expect(formatBytes(1.5 * 1024 ** 2)).toBe("1.5 MB");
    expect(formatBytes(2 * 1024 ** 3)).toBe("2.00 GB");
  });
});
