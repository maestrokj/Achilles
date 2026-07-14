import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HTTPError } from "ky";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAnon } from "@/test/session";

import * as authApi from "../api";
import { SetupWizardPage } from "../SetupWizardPage";

async function fillForm(
  user: ReturnType<typeof userEvent.setup>,
  password: string,
  repeat = password,
) {
  await user.type(screen.getByLabelText("Full name"), "First Owner");
  await user.type(screen.getByLabelText("Email"), "owner@acme.example");
  await user.type(screen.getByLabelText("Password"), password);
  await user.type(screen.getByLabelText("Repeat password"), repeat);
}

describe("SetupWizardPage", () => {
  it("shows the owner badge and a password strength hint", async () => {
    const user = userEvent.setup();
    renderAnon(<SetupWizardPage />, { route: "/setup" });

    expect(screen.getByText("Owner")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Password"), "short");
    expect(screen.getByText(/Strength/)).toBeInTheDocument();
  });

  it("posts the owner payload on submit", async () => {
    let received: unknown = null;
    server.use(
      http.post(apiUrl("/auth/setup"), async ({ request }) => {
        received = await request.json();
        return HttpResponse.json({
          access_token: "t",
          token_type: "bearer",
          must_change_password: false,
          user: { id: 1, email: "owner@acme.example", full_name: "First Owner", role: "owner" },
        });
      }),
    );
    const user = userEvent.setup();
    renderAnon(<SetupWizardPage />, { route: "/setup" });

    await fillForm(user, "a-strong-passphrase-2026");
    await user.click(screen.getByRole("button", { name: "Create owner account" }));

    await waitFor(() => {
      expect(received).toEqual({
        email: "owner@acme.example",
        full_name: "First Owner",
        password: "a-strong-passphrase-2026",
      });
    });
  });

  it("blocks submit and shows an error when passwords differ", async () => {
    let called = false;
    server.use(
      http.post(apiUrl("/auth/setup"), () => {
        called = true;
        return new HttpResponse(null, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderAnon(<SetupWizardPage />, { route: "/setup" });

    await fillForm(user, "a-strong-passphrase-2026", "different-2026");
    await user.click(screen.getByRole("button", { name: "Create owner account" }));

    expect(await screen.findByText("Passwords do not match.")).toBeInTheDocument();
    expect(called).toBe(false);
  });

  it("shows the already-set-up state on 404", async () => {
    // A fresh (unconsumed) HTTPError so responseProblem can read its body — ky's
    // real transport consumes the error body under node/undici (a harness quirk).
    const response = HttpResponse.json({ code: "SETUP_UNAVAILABLE", status: 404 }, { status: 404 });
    const httpError = new HTTPError(
      response,
      new Request("http://x/api/v1/auth/setup"),
      {} as never,
    );
    vi.spyOn(authApi, "setup").mockRejectedValueOnce(httpError);

    const user = userEvent.setup();
    renderAnon(<SetupWizardPage />, { route: "/setup" });

    await fillForm(user, "a-strong-passphrase-2026");
    await user.click(screen.getByRole("button", { name: "Create owner account" }));

    expect(await screen.findByText("Platform already set up")).toBeInTheDocument();
  });
});
