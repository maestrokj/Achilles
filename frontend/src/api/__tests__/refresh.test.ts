import { HTTPError } from "ky";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { clearSession, getAccessToken, setSession } from "@/features/auth/session-store";
import type { SessionUser } from "@/features/auth/types";

import { api } from "../client";

const USER: SessionUser = {
  id: 1,
  email: "u@acme.example",
  full_name: "U",
  role: "member",
  status: "active",
  must_change_password: false,
  timezone: null,
  locale: null,
  date_format: null,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

function sessionBody(token: string) {
  return { access_token: token, token_type: "bearer", must_change_password: false, user: USER };
}

/** A protected endpoint that only accepts the fresh token; the stale one earns a
 * 401 TOKEN_EXPIRED — exactly the shape the interceptor is meant to recover from. */
function protectedByFreshToken() {
  return http.get(apiUrl("/thing"), ({ request }) => {
    const auth = request.headers.get("Authorization");
    if (auth === "Bearer fresh-token") return HttpResponse.json({ ok: true });
    return HttpResponse.json({ code: "TOKEN_EXPIRED", status: 401 }, { status: 401 });
  });
}

afterEach(() => {
  clearSession("logout");
});

describe("api refresh interceptor", () => {
  it("refreshes a stale token once and retries the original request", async () => {
    setSession("stale-token", USER);
    let refreshCount = 0;
    server.use(
      protectedByFreshToken(),
      http.post(apiUrl("/auth/refresh"), () => {
        refreshCount += 1;
        return HttpResponse.json(sessionBody("fresh-token"));
      }),
    );

    const result = await api.get("thing").json<{ ok: boolean }>();

    expect(result).toEqual({ ok: true });
    expect(refreshCount).toBe(1);
    expect(getAccessToken()).toBe("fresh-token");
  });

  it("shares a single refresh across concurrent stale requests", async () => {
    setSession("stale-token", USER);
    let refreshCount = 0;
    server.use(
      protectedByFreshToken(),
      http.post(apiUrl("/auth/refresh"), () => {
        refreshCount += 1;
        return HttpResponse.json(sessionBody("fresh-token"));
      }),
    );

    const results = await Promise.all([
      api.get("thing").json<{ ok: boolean }>(),
      api.get("thing").json<{ ok: boolean }>(),
      api.get("thing").json<{ ok: boolean }>(),
    ]);

    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(refreshCount).toBe(1);
  });

  it("passes a non-refreshable 401 straight to the caller", async () => {
    setSession("stale-token", USER);
    let refreshCount = 0;
    server.use(
      http.post(apiUrl("/auth/login"), () =>
        HttpResponse.json({ code: "INVALID_CREDENTIALS", status: 401 }, { status: 401 }),
      ),
      http.post(apiUrl("/auth/refresh"), () => {
        refreshCount += 1;
        return HttpResponse.json(sessionBody("fresh-token"));
      }),
    );

    await expect(api.post("auth/login").json()).rejects.toBeInstanceOf(HTTPError);
    expect(refreshCount).toBe(0);
  });
});
