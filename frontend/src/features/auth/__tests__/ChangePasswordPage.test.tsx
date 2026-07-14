import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HTTPError } from "ky";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAnon } from "@/test/session";

import * as authApi from "../api";
import { ChangePasswordPage } from "../ChangePasswordPage";

function httpError(status: number, body: object): HTTPError {
  const response = HttpResponse.json(body, { status });
  return new HTTPError(response, new Request("http://x/api/v1/auth/password/change"), {} as never);
}

async function fill(
  user: ReturnType<typeof userEvent.setup>,
  current: string,
  next: string,
  confirm = next,
) {
  await user.type(screen.getByLabelText("Current password"), current);
  await user.type(screen.getByLabelText("New password"), next);
  await user.type(screen.getByLabelText("Repeat new password"), confirm);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChangePasswordPage", () => {
  it("posts the current/new pair and shows no error on success", async () => {
    let received: unknown = null;
    server.use(
      http.post(apiUrl("/auth/password/change"), async ({ request }) => {
        received = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAnon(<ChangePasswordPage />);

    await fill(user, "old-passphrase-2026", "a-fresh-passphrase-2026");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    await waitFor(() => {
      expect(received).toEqual({
        current_password: "old-passphrase-2026",
        new_password: "a-fresh-passphrase-2026",
      });
    });
  });

  it("blocks submit and flags a mismatch before any request", async () => {
    let called = false;
    server.use(
      http.post(apiUrl("/auth/password/change"), () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAnon(<ChangePasswordPage />);

    await fill(user, "old-passphrase-2026", "a-fresh-passphrase-2026", "typo-2026");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    expect(await screen.findByText("Passwords do not match.")).toBeInTheDocument();
    expect(called).toBe(false);
  });

  it("names a wrong current password", async () => {
    vi.spyOn(authApi, "changePassword").mockRejectedValueOnce(
      httpError(401, { code: "INVALID_CREDENTIALS", status: 401 }),
    );
    const user = userEvent.setup();
    renderAnon(<ChangePasswordPage />);

    await fill(user, "not-the-current", "a-fresh-passphrase-2026");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    expect(await screen.findByText("The current password is incorrect.")).toBeInTheDocument();
  });

  it("surfaces a weak-password rejection from the policy", async () => {
    vi.spyOn(authApi, "changePassword").mockRejectedValueOnce(
      httpError(422, {
        code: "VALIDATION_ERROR",
        status: 422,
        errors: [{ field: "password", message: "password is too weak" }],
      }),
    );
    const user = userEvent.setup();
    renderAnon(<ChangePasswordPage />);

    await fill(user, "old-passphrase-2026", "weak");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    expect(
      await screen.findByText("The password is too weak — make it longer or less predictable."),
    ).toBeInTheDocument();
  });
});
