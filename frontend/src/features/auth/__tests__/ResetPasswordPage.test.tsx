import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HTTPError } from "ky";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAnon } from "@/test/session";

import * as authApi from "../api";
import { ResetPasswordPage } from "../ResetPasswordPage";

function httpError(status: number, body: object): HTTPError {
  const response = HttpResponse.json(body, { status });
  return new HTTPError(response, new Request("http://x/api/v1/auth/password/reset"), {} as never);
}

const resetRoute = (
  <Routes>
    <Route path="/reset-password/:token" element={<ResetPasswordPage />} />
    <Route path="/login" element={<div>sign-in form</div>} />
  </Routes>
);

async function fill(user: ReturnType<typeof userEvent.setup>, pw: string, confirm = pw) {
  await user.type(screen.getByLabelText("New password"), pw);
  await user.type(screen.getByLabelText("Repeat new password"), confirm);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ResetPasswordPage", () => {
  it("posts the token with the new password and returns to sign in", async () => {
    let received: unknown = null;
    server.use(
      http.post(apiUrl("/auth/password/reset"), async ({ request }) => {
        received = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAnon(resetRoute, { route: "/reset-password/reset-tok" });

    await fill(user, "a-fresh-passphrase-2026");
    await user.click(screen.getByRole("button", { name: "Save password" }));

    expect(await screen.findByText("sign-in form")).toBeInTheDocument();
    expect(received).toEqual({ token: "reset-tok", new_password: "a-fresh-passphrase-2026" });
  });

  it("shows the expired state for a dead link (used or timed out)", async () => {
    vi.spyOn(authApi, "resetPassword").mockRejectedValueOnce(
      httpError(410, { code: "RESET_EXPIRED", status: 410 }),
    );
    const user = userEvent.setup();
    renderAnon(resetRoute, { route: "/reset-password/reset-tok" });

    await fill(user, "a-fresh-passphrase-2026");
    await user.click(screen.getByRole("button", { name: "Save password" }));

    expect(await screen.findByText("Link expired")).toBeInTheDocument();
  });

  it("flags a mismatch before any request", async () => {
    let called = false;
    server.use(
      http.post(apiUrl("/auth/password/reset"), () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAnon(resetRoute, { route: "/reset-password/reset-tok" });

    await fill(user, "a-fresh-passphrase-2026", "typo-2026");
    await user.click(screen.getByRole("button", { name: "Save password" }));

    expect(await screen.findByText("Passwords do not match.")).toBeInTheDocument();
    expect(called).toBe(false);
  });
});
