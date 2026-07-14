import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HTTPError } from "ky";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAnon } from "@/test/session";

import * as authApi from "../api";
import { InviteAcceptPage } from "../InviteAcceptPage";
import { clearSession } from "../session-store";

function httpError(status: number, body: object): HTTPError {
  const response = HttpResponse.json(body, { status });
  return new HTTPError(response, new Request("http://x/api/v1/invites/tok/accept"), {} as never);
}

const inviteRoute = (
  <Routes>
    <Route path="/invite/:token" element={<InviteAcceptPage />} />
    <Route path="/chat" element={<div>chat surface</div>} />
  </Routes>
);

async function fill(user: ReturnType<typeof userEvent.setup>, pw = "a-fresh-passphrase-2026") {
  await user.type(screen.getByLabelText("Full name"), "New Member");
  await user.type(screen.getByLabelText("Password"), pw);
  await user.type(screen.getByLabelText("Repeat password"), pw);
}

afterEach(() => {
  clearSession("logout");
  vi.restoreAllMocks();
});

describe("InviteAcceptPage", () => {
  it("registers the account and lands on the role home", async () => {
    let received: unknown = null;
    server.use(
      http.post(apiUrl("/invites/tok/accept"), async ({ request }) => {
        received = await request.json();
        return HttpResponse.json({
          access_token: "t",
          token_type: "bearer",
          must_change_password: false,
          user: {
            id: 7,
            email: "new@acme.example",
            full_name: "New Member",
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
    renderAnon(inviteRoute, { route: "/invite/tok" });

    await fill(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("chat surface")).toBeInTheDocument();
    expect(received).toEqual({ full_name: "New Member", password: "a-fresh-passphrase-2026" });
  });

  // The seam this repairs: the backend answers 409 EMAIL_TAKEN when the invited
  // address already has an account; without this branch the screen showed a
  // "try again" that could never succeed. Now it routes the visitor to sign in.
  it("routes an already-registered email to sign in", async () => {
    vi.spyOn(authApi, "acceptInvite").mockRejectedValueOnce(
      httpError(409, { code: "EMAIL_TAKEN", status: 409 }),
    );
    const user = userEvent.setup();
    renderAnon(inviteRoute, { route: "/invite/tok" });

    await fill(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("You already have an account")).toBeInTheDocument();
    expect(
      screen.getByText("An account with this email already exists. Sign in instead."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to sign in" })).toBeInTheDocument();
  });

  it("shows a terminal state for a spent invite", async () => {
    vi.spyOn(authApi, "acceptInvite").mockRejectedValueOnce(
      httpError(410, { code: "INVITE_USED", status: 410 }),
    );
    const user = userEvent.setup();
    renderAnon(inviteRoute, { route: "/invite/tok" });

    await fill(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Invitation already used")).toBeInTheDocument();
  });
});
