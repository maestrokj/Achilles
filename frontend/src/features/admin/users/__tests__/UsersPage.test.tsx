import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { downloadBlob } from "@/lib/download";
import { SessionContext } from "@/features/auth/session-context";

vi.mock("@/lib/download", () => ({ downloadBlob: vi.fn() }));
import type { SessionUser } from "@/features/auth/types";
import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import type { AdminUser, Invite } from "../types";
import { UsersPage } from "../UsersPage";

const OWNER: SessionUser = {
  id: 1,
  email: "boss@acme.example",
  full_name: "Boss",
  role: "owner",
  status: "active",
  must_change_password: false,
  timezone: null,
  locale: null,
  date_format: null,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

const USERS: AdminUser[] = [
  {
    id: 2,
    email: "anna@acme.example",
    full_name: "Anna Orlova",
    role: "owner",
    status: "active",
    must_change_password: false,
    timezone: null,
    locale: null,
    date_format: null,
    last_login_at: "2026-07-04T09:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 3,
    email: "maria@acme.example",
    full_name: "Maria Kim",
    role: "member",
    status: "deactivated",
    must_change_password: false,
    timezone: null,
    locale: null,
    date_format: null,
    last_login_at: null,
    created_at: "2026-02-01T00:00:00Z",
  },
];

const INVITES: Invite[] = [
  {
    id: 10,
    email: "colleague@acme.example",
    role: "member",
    status: "pending",
    expires_at: "2026-07-06T00:00:00Z",
    created_at: "2026-07-04T00:00:00Z",
  },
  {
    id: 11,
    email: "old@acme.example",
    role: "member",
    status: "expired",
    expires_at: "2026-06-01T00:00:00Z",
    created_at: "2026-05-29T00:00:00Z",
  },
];

function page<T>(items: T[]) {
  return { items, total: items.length, page: 1, per_page: 50 };
}

function stubBackend({ smtp = false }: { smtp?: boolean } = {}) {
  server.use(
    http.get(apiUrl("/admin/settings"), () =>
      HttpResponse.json({ smtp_configured: smtp } as Record<string, unknown>),
    ),
    http.get(apiUrl("/admin/users"), () => HttpResponse.json(page(USERS))),
    http.get(apiUrl("/invites"), () => HttpResponse.json(page(INVITES))),
  );
}

function renderPage(ui: ReactElement) {
  return renderWithProviders(
    <SessionContext.Provider value={{ status: "authenticated", user: OWNER, expired: false }}>
      {ui}
    </SessionContext.Provider>,
  );
}

describe("UsersPage", () => {
  it("renders the list tab with role and status", async () => {
    stubBackend();
    renderPage(<UsersPage />);

    expect(await screen.findByText("Anna Orlova")).toBeInTheDocument();
    expect(screen.getByText("deactivated")).toBeInTheDocument();
    expect(screen.getByText("never")).toBeInTheDocument();
  });

  it("exports the filtered list as CSV", async () => {
    stubBackend();
    let exportUrl = "";
    server.use(
      http.get(apiUrl("/admin/users/export"), ({ request }) => {
        exportUrl = request.url;
        return new HttpResponse("id,email\n2,anna@acme.example\n", {
          headers: { "content-type": "text/csv" },
        });
      }),
    );
    const user = userEvent.setup();
    renderPage(<UsersPage />);
    await screen.findByText("Anna Orlova");

    await user.click(screen.getByRole("button", { name: "Export" }));
    await user.click(await screen.findByText("CSV — for spreadsheets"));

    await waitFor(() => {
      expect(vi.mocked(downloadBlob)).toHaveBeenCalledWith("users.csv", expect.any(Blob));
    });
    expect(new URL(exportUrl).searchParams.get("format")).toBe("csv");
  });

  it("keeps Invite disabled while SMTP is not configured", async () => {
    stubBackend({ smtp: false });
    renderPage(<UsersPage />);

    await screen.findByText("Anna Orlova");
    expect(screen.getByRole("button", { name: "Invite" })).toBeDisabled();
  });

  it("enables Invite once SMTP is configured", async () => {
    stubBackend({ smtp: true });
    renderPage(<UsersPage />);

    await screen.findByText("Anna Orlova");
    expect(screen.getByRole("button", { name: "Invite" })).toBeEnabled();
  });

  it("shows invites with per-status actions", async () => {
    stubBackend({ smtp: true });
    const user = userEvent.setup();
    renderPage(<UsersPage />);
    await screen.findByText("Anna Orlova");

    await user.click(screen.getByRole("tab", { name: "Invitations" }));

    expect(await screen.findByText("colleague@acme.example")).toBeInTheDocument();
    // pending → resend + revoke; expired → resend only.
    expect(screen.getAllByRole("button", { name: "Resend" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
  });

  it("revokes an invite behind a confirm", async () => {
    stubBackend({ smtp: true });
    let revoked = false;
    server.use(
      http.delete(apiUrl("/invites/10"), () => {
        revoked = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderPage(<UsersPage />);
    await screen.findByText("Anna Orlova");

    await user.click(screen.getByRole("tab", { name: "Invitations" }));
    await user.click(await screen.findByRole("button", { name: "Revoke" }));

    expect(await screen.findByText("Revoke this invitation?")).toBeInTheDocument();
    expect(revoked).toBe(false);

    await user.click(screen.getAllByRole("button", { name: "Revoke" }).at(-1) as HTMLElement);
    await waitFor(() => {
      expect(revoked).toBe(true);
    });
  });

  it("filters by the last sign-in window", async () => {
    stubBackend();
    const seen: (string | null)[] = [];
    server.use(
      http.get(apiUrl("/admin/users"), ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("last_login"));
        return HttpResponse.json(page(USERS));
      }),
    );
    const user = userEvent.setup();
    renderPage(<UsersPage />);
    await screen.findByText("Anna Orlova");

    await user.click(screen.getByRole("button", { name: "Last sign-in" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "never" }));

    await waitFor(() => {
      expect(seen.at(-1)).toBe("never");
    });
  });

  it("deactivates from the row menu behind a confirm", async () => {
    stubBackend();
    let patched: unknown;
    server.use(
      http.patch(apiUrl("/admin/users/2"), async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json(USERS[0]);
      }),
    );
    const user = userEvent.setup();
    renderPage(<UsersPage />);
    await screen.findByText("Anna Orlova");

    // Anna's row: the owner sees role change, deactivate, reset and delete.
    await user.click(screen.getAllByRole("button", { name: "User actions" })[0]);
    expect(await screen.findByRole("menuitem", { name: "Change role" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Delete user" })).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: "Deactivate" }));
    expect(await screen.findByText("Deactivate this account?")).toBeInTheDocument();
    expect(patched).toBeUndefined();

    await user.click(screen.getByRole("button", { name: "Deactivate" }));
    await waitFor(() => {
      expect(patched).toEqual({ status: "deactivated" });
    });
  });

  it("offers no self-barred actions in the acting user's own row", async () => {
    // Reset/deactivate/delete are all barred against oneself server-side; only
    // role change (owner-only) survives on the owner's own row.
    server.use(
      http.get(apiUrl("/admin/settings"), () =>
        HttpResponse.json({ smtp_configured: false } as Record<string, unknown>),
      ),
      http.get(apiUrl("/admin/users"), () =>
        HttpResponse.json(page([{ ...USERS[0], id: OWNER.id, full_name: "Boss" }])),
      ),
      http.get(apiUrl("/invites"), () => HttpResponse.json(page(INVITES))),
    );
    const user = userEvent.setup();
    renderPage(<UsersPage />);
    await screen.findByText("Boss");

    await user.click(screen.getByRole("button", { name: "User actions" }));
    expect(await screen.findByRole("menuitem", { name: "Change role" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Reset password" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Deactivate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Delete user" })).not.toBeInTheDocument();
  });
});
