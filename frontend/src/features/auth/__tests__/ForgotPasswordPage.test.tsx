import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HTTPError } from "ky";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAnon } from "@/test/session";

import * as authApi from "../api";
import { ForgotPasswordPage } from "../ForgotPasswordPage";

function httpError(status: number, body: object): HTTPError {
  const response = HttpResponse.json(body, { status });
  return new HTTPError(response, new Request("http://x/api/v1/auth/password/forgot"), {} as never);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ForgotPasswordPage", () => {
  it("sends the email and confirms uniformly (anti-enumeration)", async () => {
    let received: unknown = null;
    server.use(
      http.post(apiUrl("/auth/password/forgot"), async ({ request }) => {
        received = await request.json();
        return HttpResponse.json({ status: "ok" });
      }),
    );
    const user = userEvent.setup();
    renderAnon(<ForgotPasswordPage />);

    await user.type(screen.getByLabelText("Email"), "someone@acme.example");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(
      await screen.findByText(
        "If an account with this email exists, a reset link has been sent. Check your inbox.",
      ),
    ).toBeInTheDocument();
    expect(received).toEqual({ email: "someone@acme.example" });
  });

  it("reports a rate-limit with its retry window", async () => {
    vi.spyOn(authApi, "forgotPassword").mockRejectedValueOnce(
      httpError(429, { code: "RATE_LIMITED", status: 429, retry_after: 45 }),
    );
    const user = userEvent.setup();
    renderAnon(<ForgotPasswordPage />);

    await user.type(screen.getByLabelText("Email"), "someone@acme.example");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText("Too many requests. Try again in 45 s.")).toBeInTheDocument();
  });
});
