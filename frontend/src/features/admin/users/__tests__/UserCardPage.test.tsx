import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { SessionContext } from "@/features/auth/session-context";
import type { ApiKey } from "@/features/auth/api-keys";
import type { SessionUser } from "@/features/auth/types";
import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import type { AdminUserDetail, MappingPage } from "../types";
import { UserCardPage } from "../UserCardPage";

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

const DETAIL: AdminUserDetail = {
  id: 2,
  email: "anna@acme.example",
  full_name: "Anna Orlova",
  role: "member",
  status: "active",
  must_change_password: false,
  timezone: null,
  locale: null,
  date_format: null,
  last_login_at: "2026-07-04T09:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  active_sessions: 2,
};

const MAPPING: MappingPage = {
  items: [
    {
      user_id: 2,
      full_name: "Anna Orlova",
      email: "anna@acme.example",
      links: [
        {
          principal_id: 11,
          source_id: 1,
          source_user_id: "jira-1",
          email: "anna@acme.example",
          display_name: "anna",
          pinned: false,
        },
      ],
    },
  ],
  total: 1,
  page: 1,
  per_page: 50,
  sources: [
    { id: 1, name: "Jira", connector_type: "jira" },
    { id: 2, name: "Slack", connector_type: "slack" },
  ],
};

const KEY: ApiKey = {
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
};

function stubBackend() {
  server.use(
    http.get(apiUrl("/admin/users/2"), () => HttpResponse.json(DETAIL)),
    http.get(apiUrl("/admin/identity-mapping"), () => HttpResponse.json(MAPPING)),
    http.get(apiUrl("/api-keys"), () => HttpResponse.json({ items: [KEY] })),
  );
}

function renderCard(sessionUser: SessionUser = OWNER) {
  return renderWithProviders(
    <SessionContext.Provider value={{ status: "authenticated", user: sessionUser, expired: false }}>
      <Routes>
        <Route path="/admin/users/:userId" element={<UserCardPage />} />
        <Route path="/admin/users" element={<p>users list</p>} />
      </Routes>
    </SessionContext.Provider>,
    { route: "/admin/users/2" },
  );
}

describe("UserCardPage", () => {
  it("shows the identity block: linked and unmatched per source", async () => {
    stubBackend();
    renderCard();

    expect(await screen.findByText("Jira")).toBeInTheDocument();
    expect(screen.getByText("anna")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument();
    // Slack has no link — unmatched with a Link action.
    expect(screen.getByText("Slack")).toBeInTheDocument();
    expect(screen.getByText("unmatched")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Link" })).toBeInTheDocument();
  });

  it("shows key scope chips and revokes behind a confirm", async () => {
    stubBackend();
    let revoked = false;
    server.use(
      http.delete(apiUrl("/api-keys/5"), () => {
        revoked = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderCard();

    expect(await screen.findByText("read-only")).toBeInTheDocument();
    expect(screen.getByText("all sources")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Revoke" }));
    expect(await screen.findByText("Revoke this API key?")).toBeInTheDocument();
    expect(revoked).toBe(false);

    await user.click(screen.getAllByRole("button", { name: "Revoke" }).at(-1) as HTMLElement);
    await waitFor(() => {
      expect(revoked).toBe(true);
    });
  });

  it("asks before sending the reset link", async () => {
    stubBackend();
    let resetCalled = false;
    server.use(
      http.post(apiUrl("/admin/users/2/reset-password"), () => {
        resetCalled = true;
        return HttpResponse.json({ mode: "link", temp_password: null });
      }),
    );
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Reset password" }));
    expect(
      await screen.findByText("A link to set a new password will be sent to anna@acme.example."),
    ).toBeInTheDocument();
    expect(resetCalled).toBe(false);

    await user.click(screen.getByRole("button", { name: "Send link" }));
    await waitFor(() => {
      expect(resetCalled).toBe(true);
    });
  });

  it("deletes only after the email is retyped", async () => {
    stubBackend();
    let deleted = false;
    server.use(
      http.delete(apiUrl("/admin/users/2"), () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Delete user" }));
    await screen.findByText("Delete this account?");

    const confirm = screen.getAllByRole("button", { name: "Delete user" }).at(-1) as HTMLElement;
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText("Type the user's email to confirm"), "anna@acme.example");
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    await waitFor(() => {
      expect(deleted).toBe(true);
    });
  });

  it("refreshes the API keys after a deactivation", async () => {
    let keysCalls = 0;
    server.use(
      http.get(apiUrl("/admin/users/2"), () => HttpResponse.json(DETAIL)),
      http.get(apiUrl("/admin/identity-mapping"), () => HttpResponse.json(MAPPING)),
      // The deactivation cascade revokes the user's keys on the backend, so the
      // second read (triggered by the mutation's invalidation) is empty.
      http.get(apiUrl("/api-keys"), () => {
        keysCalls += 1;
        return HttpResponse.json({ items: keysCalls === 1 ? [KEY] : [] });
      }),
      http.patch(apiUrl("/admin/users/2"), () =>
        HttpResponse.json({ ...DETAIL, status: "deactivated" }),
      ),
    );
    const user = userEvent.setup();
    renderCard();

    expect(await screen.findByText("read-only")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Deactivate" }));
    await user.click(screen.getAllByRole("button", { name: "Deactivate" }).at(-1) as HTMLElement);

    await waitFor(() => {
      expect(screen.queryByText("read-only")).not.toBeInTheDocument();
    });
    expect(keysCalls).toBeGreaterThanOrEqual(2);
  });

  it("hides admin actions on one's own card", async () => {
    stubBackend();
    // The viewer is the very account on screen — reset routes to the profile and
    // deactivate/delete are self-barred, so the whole actions section is dropped.
    const self: SessionUser = { ...OWNER, id: 2, email: "anna@acme.example", role: "member" };
    renderCard(self);

    await screen.findByText("Anna Orlova");
    expect(screen.queryByRole("button", { name: "Reset password" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete user" })).not.toBeInTheDocument();
  });

  it("hides admin actions outside an admin's manage scope", async () => {
    // An admin cannot reset or deactivate another admin (owner-only reach) — the
    // actions section must not offer buttons the server would 403.
    server.use(
      http.get(apiUrl("/admin/users/2"), () => HttpResponse.json({ ...DETAIL, role: "admin" })),
      http.get(apiUrl("/admin/identity-mapping"), () => HttpResponse.json(MAPPING)),
      http.get(apiUrl("/api-keys"), () => HttpResponse.json({ items: [] })),
    );
    renderCard({ ...OWNER, id: 9, role: "admin" });

    await screen.findByText("Anna Orlova");
    expect(screen.queryByRole("button", { name: "Reset password" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
  });

  it("lets an admin manage a member but never delete", async () => {
    stubBackend(); // DETAIL is a member.
    renderCard({ ...OWNER, id: 9, role: "admin" });

    expect(await screen.findByRole("button", { name: "Reset password" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deactivate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete user" })).not.toBeInTheDocument();
  });
});
