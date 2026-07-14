import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HTTPError } from "ky";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAnon } from "@/test/session";

import * as authApi from "../api";
import { LoginPage } from "../LoginPage";
import { clearSession } from "../session-store";

/** A fresh, unconsumed problem response — the node/undici transport eats a real
 * HTTPError body, so error branches feed toProblem a body it can still read. */
function httpError(status: number, body: object): HTTPError {
  const response = HttpResponse.json(body, { status });
  return new HTTPError(response, new Request("http://x/api/v1/auth/login"), {} as never);
}

const loginRoute = (
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/chat" element={<div>chat surface</div>} />
  </Routes>
);

afterEach(() => {
  clearSession("logout");
  vi.restoreAllMocks();
});

describe("LoginPage", () => {
  it("posts the credentials and lands on the role home", async () => {
    let received: unknown = null;
    server.use(
      http.post(apiUrl("/auth/login"), async ({ request }) => {
        received = await request.json();
        return HttpResponse.json({
          access_token: "t",
          token_type: "bearer",
          must_change_password: false,
          user: {
            id: 5,
            email: "m@acme.example",
            full_name: "Member",
            role: "member",
            status: "active",
            must_change_password: false,
            timezone: null,
            locale: null,
            date_format: null,
            last_login_at: null,
            created_at: "2026-01-01T00:00:00Z",
          },
        });
      }),
    );
    const user = userEvent.setup();
    renderAnon(loginRoute, { route: "/login" });

    await user.type(screen.getByLabelText("Email"), "m@acme.example");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery-staple");
    await user.click(screen.getByRole("checkbox", { name: "Remember me" }));
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("chat surface")).toBeInTheDocument();
    expect(received).toEqual({
      email: "m@acme.example",
      password: "correct-horse-battery-staple",
      remember_me: true,
    });
  });

  it("shows a credentials error and stays on the form", async () => {
    vi.spyOn(authApi, "login").mockRejectedValueOnce(
      httpError(401, { code: "INVALID_CREDENTIALS", status: 401 }),
    );
    const user = userEvent.setup();
    renderAnon(loginRoute, { route: "/login" });

    await user.type(screen.getByLabelText("Email"), "m@acme.example");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Incorrect email or password.")).toBeInTheDocument();
    expect(screen.queryByText("chat surface")).not.toBeInTheDocument();
  });

  it("counts down and blocks resubmit when rate limited", async () => {
    vi.spyOn(authApi, "login").mockRejectedValueOnce(
      httpError(429, { code: "RATE_LIMITED", status: 429, retry_after: 30 }),
    );
    const user = userEvent.setup();
    renderAnon(loginRoute, { route: "/login" });

    await user.type(screen.getByLabelText("Email"), "m@acme.example");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery-staple");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByText("Too many sign-in attempts. Try again in 30 s."),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    });
  });

  it("explains an involuntary session drop from the query param", () => {
    renderAnon(loginRoute, { route: "/login?reason=session-expired" });

    expect(
      screen.getByText("Your session has expired — sign in again to continue."),
    ).toBeInTheDocument();
  });
});
