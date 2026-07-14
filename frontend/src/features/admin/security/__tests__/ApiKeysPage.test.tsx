import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { toast } from "@/lib/toast";
import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { ApiKeysPage } from "../ApiKeysPage";
import type { AdminApiKey } from "../types";

vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const KEY: AdminApiKey = {
  id: 5,
  user_id: 2,
  prefix: "ach_x1",
  name: null,
  scope: { access: "read-only", sources: null },
  expires_at: null,
  last_used_at: null,
  is_revoked: false,
  revoked_at: null,
  created_at: "2026-06-01T00:00:00Z",
  owner: { id: 2, full_name: "Anna Orlova", email: "anna@acme.example" },
  status: "active",
};

function stubBackend() {
  server.use(
    http.get(apiUrl("/admin/api-keys"), () =>
      HttpResponse.json({ items: [KEY], total: 1, page: 1, per_page: 50 }),
    ),
  );
}

describe("ApiKeysPage", () => {
  it("lists company keys with owner and scope", async () => {
    stubBackend();
    renderAs("owner", <ApiKeysPage />);

    expect(await screen.findByText("Anna Orlova")).toBeInTheDocument();
    expect(screen.getByText("ach_x1…")).toBeInTheDocument();
    expect(screen.getByText("all sources")).toBeInTheDocument();
  });

  it("shows the key name above the prefix when set", async () => {
    server.use(
      http.get(apiUrl("/admin/api-keys"), () =>
        HttpResponse.json({
          items: [{ ...KEY, name: "CI server" }],
          total: 1,
          page: 1,
          per_page: 50,
        }),
      ),
    );
    renderAs("owner", <ApiKeysPage />);

    expect(await screen.findByText("CI server")).toBeInTheDocument();
    expect(screen.getByText("ach_x1…")).toBeInTheDocument();
  });

  it("revokes a key only after the confirm — cancel sends nothing", async () => {
    stubBackend();
    let revoked = false;
    server.use(
      http.delete(apiUrl("/api-keys/5"), () => {
        revoked = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <ApiKeysPage />);

    await user.click(await screen.findByRole("button", { name: "Revoke" }));
    expect(await screen.findByText("Revoke the key?")).toBeInTheDocument();

    // Cancel closes the dialog and no call goes out.
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByText("Revoke the key?")).not.toBeInTheDocument();
    });
    expect(revoked).toBe(false);

    // Reopen and confirm — now the DELETE fires.
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    await screen.findByText("Revoke the key?");
    await user.click(screen.getAllByRole("button", { name: "Revoke" }).at(-1) as HTMLElement);
    await waitFor(() => {
      expect(revoked).toBe(true);
    });
  });

  it("surfaces a toast when the revoke is refused", async () => {
    stubBackend();
    server.use(
      http.delete(apiUrl("/api-keys/5"), () =>
        HttpResponse.json(
          { type: "about:blank", title: "Forbidden", status: 403, code: "FORBIDDEN" },
          { status: 403 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderAs("owner", <ApiKeysPage />);

    await user.click(await screen.findByRole("button", { name: "Revoke" }));
    await screen.findByText("Revoke the key?");
    await user.click(screen.getAllByRole("button", { name: "Revoke" }).at(-1) as HTMLElement);

    // The failure is surfaced, not swallowed — and the dialog stays put for a retry.
    await waitFor(() => {
      expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
        "The action could not be completed.",
        expect.anything(),
      );
    });
    expect(screen.getByText("Revoke the key?")).toBeInTheDocument();
  });
});
