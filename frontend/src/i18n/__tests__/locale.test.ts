import { afterEach, describe, expect, it, vi } from "vitest";

import { clearStoredLocale, currentLocale, resolveLocale, setLocale } from "@/i18n";

const LOCALE_STORAGE_KEY = "achilles.locale";

/** i18next applies changeLanguage on a microtask, so assertions poll. */
async function expectLocale(expected: "en" | "ru") {
  await vi.waitFor(() => {
    expect(currentLocale()).toBe(expected);
  });
}

afterEach(async () => {
  clearStoredLocale();
  resolveLocale(null, "en");
  await expectLocale("en");
});

describe("resolveLocale — device → personal → org", () => {
  it("applies the personal locale over the org default", async () => {
    resolveLocale(1, "ru", "en");
    await expectLocale("ru");
  });

  it("falls back to the org default when the user has no personal locale", async () => {
    resolveLocale(1, null, "ru");
    await expectLocale("ru");
  });

  it("keeps a device override that belongs to the signed-in user", async () => {
    setLocale("ru", 1);
    await expectLocale("ru");

    resolveLocale(1, "en", "en");
    await expectLocale("ru");
  });

  it("discards a device override left by another user", async () => {
    setLocale("ru", 1);
    await expectLocale("ru");

    // User 2 signs in at the same browser: they get their own language, and the
    // stale override is dropped rather than re-applied on the next resolve.
    resolveLocale(2, "en", "en");
    await expectLocale("en");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBeNull();
  });

  it("drops the override on logout, so the org default rules the login screen", async () => {
    setLocale("ru", 1);
    await expectLocale("ru");

    resolveLocale(null, undefined, "en");
    await expectLocale("en");
  });

  it("treats a bare legacy string as ownerless and hands control back to the chain", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "ru");

    resolveLocale(1, "en", "en");
    await expectLocale("en");
  });

  it("honours an anonymous override on the login screen", async () => {
    setLocale("ru", null);
    await expectLocale("ru");

    resolveLocale(null, undefined, "en");
    await expectLocale("ru");
  });
});
