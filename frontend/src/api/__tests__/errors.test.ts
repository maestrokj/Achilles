import i18n from "i18next";
import { HTTPError } from "ky";
import { HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import en from "@/i18n/locales/en";
import ru from "@/i18n/locales/ru";

import { apiErrorReason, codeReason, problemReason } from "../errors";
import { PROBLEM_CODES, type ProblemDetails } from "../problems";

function problem(overrides: Partial<ProblemDetails>): ProblemDetails {
  return {
    type: "/errors/x",
    title: "X",
    status: 400,
    detail: "English backend prose",
    code: "X",
    request_id: "req-1",
    ...overrides,
  };
}

function httpError(status: number, body: object): HTTPError {
  const response = HttpResponse.json({ ...body }, { status });
  return new HTTPError(response, new Request("http://x/api/v1/thing"), {} as never);
}

describe("errors.codes registry", () => {
  it("covers every problem code in both locales, with no orphans", () => {
    for (const code of Object.keys(PROBLEM_CODES)) {
      expect(en.errors.codes[code as keyof typeof en.errors.codes], `en ${code}`).toBeTruthy();
      expect(ru.errors.codes[code as keyof typeof ru.errors.codes], `ru ${code}`).toBeTruthy();
    }
    for (const key of Object.keys(en.errors.codes)) {
      expect(PROBLEM_CODES, `orphan locale key ${key}`).toHaveProperty(key);
    }
    expect(Object.keys(ru.errors.codes).sort()).toEqual(Object.keys(en.errors.codes).sort());
  });
});

describe("codeReason", () => {
  it("resolves a known code and returns null for unknown ones", () => {
    expect(codeReason(PROBLEM_CODES.NO_CHAT_MODEL)).toBe(en.errors.codes.NO_CHAT_MODEL);
    expect(codeReason("SOMETHING_NEW")).toBeNull();
    expect(codeReason(undefined)).toBeNull();
  });

  it("speaks the active language", async () => {
    await i18n.changeLanguage("ru");
    try {
      expect(codeReason(PROBLEM_CODES.NO_CHAT_MODEL)).toBe(ru.errors.codes.NO_CHAT_MODEL);
    } finally {
      await i18n.changeLanguage("en");
    }
  });
});

describe("problemReason", () => {
  it("prefers the mapped code over everything else", () => {
    const reason = problemReason(problem({ code: PROBLEM_CODES.MODEL_IN_USE, status: 409 }));
    expect(reason).toBe(en.errors.codes.MODEL_IN_USE);
  });

  it("interpolates retry_after for RATE_LIMITED", () => {
    const reason = problemReason(
      problem({ code: PROBLEM_CODES.RATE_LIMITED, status: 429, retry_after: 42 }),
    );
    expect(reason).toContain("42");
  });

  it("falls back to the status tier for unmapped codes", () => {
    expect(problemReason(problem({ code: "BRAND_NEW_CODE", status: 404 }))).toBe(
      en.errors.status.notFound,
    );
    expect(problemReason(problem({ code: "BRAND_NEW_CODE", status: 503 }))).toBe(
      en.errors.status.server,
    );
  });

  it("uses the explicit status when there is no problem document", () => {
    expect(problemReason(null, 403)).toBe(en.errors.status.forbidden);
    expect(problemReason(null)).toBe(en.errors.status.unknown);
  });
});

describe("apiErrorReason", () => {
  afterEach(() => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
  });

  it("reads the problem out of an HTTPError", async () => {
    const error = httpError(409, problem({ code: PROBLEM_CODES.EMAIL_TAKEN, status: 409 }));
    expect(await apiErrorReason(error)).toBe(en.errors.codes.EMAIL_TAKEN);
  });

  it("uses the status tier when the body is not a problem document", async () => {
    expect(await apiErrorReason(httpError(500, { oops: true }))).toBe(en.errors.status.server);
  });

  it("distinguishes offline from a network failure", async () => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: false });
    expect(await apiErrorReason(new TypeError("fetch failed"))).toBe(en.errors.status.offline);

    Object.defineProperty(window.navigator, "onLine", { configurable: true, value: true });
    expect(await apiErrorReason(new TypeError("fetch failed"))).toBe(en.errors.status.network);
  });

  it("keeps programming errors generic", async () => {
    expect(await apiErrorReason(new Error("bug"))).toBe(en.errors.status.unknown);
  });
});
