import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { SessionContext } from "@/features/auth/session-context";
import type { SessionUser } from "@/features/auth/types";
import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { AuditLogPage } from "../AuditLogPage";

const ENTRIES = {
  items: [
    {
      id: 1,
      actor_id: 1,
      actor_email: "someone@acme.example",
      action: "auth.login",
      target_type: null,
      target_id: null,
      result: "success",
      ip: "10.0.0.4",
      user_agent: "Mozilla/5.0 (Test Runner)",
      meta: { reason: "expired" },
      created_at: "2026-07-04T10:00:00Z",
    },
    {
      id: 2,
      actor_id: null,
      actor_email: null,
      action: "auth.logout",
      target_type: null,
      target_id: null,
      result: "failure",
      ip: "83.4.1.1",
      user_agent: null,
      meta: null,
      created_at: "2026-07-04T09:47:00Z",
    },
  ],
  total: 2,
  page: 1,
  per_page: 50,
  groups: ["auth", "admin", "api-keys", "ai"],
};

/** Record every audit request so the tests can assert the derived query params. */
function captureAudit(): URL[] {
  const seen: URL[] = [];
  server.use(
    http.get(apiUrl("/admin/audit-log"), ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json(ENTRIES);
    }),
  );
  return seen;
}

describe("AuditLogPage", () => {
  it("renders entries for the Owner", async () => {
    server.use(http.get(apiUrl("/admin/audit-log"), () => HttpResponse.json(ENTRIES)));
    renderAs("owner", <AuditLogPage />);

    expect(await screen.findByText("10.0.0.4")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "someone@acme.example" })).toHaveAttribute(
      "href",
      "/admin/users/1",
    );
    expect(screen.getByText("failure")).toBeInTheDocument();
    expect(screen.getByText("1–2 of 2")).toBeInTheDocument();
  });

  it("opens on the default 7-day window — a `from` bound rides the query", async () => {
    const seen = captureAudit();
    renderAs("owner", <AuditLogPage />);

    await screen.findByText("10.0.0.4");
    const from = seen.at(-1)?.searchParams.get("from");
    expect(from).toBeTruthy();
    // 7d default: the lower bound is roughly a week back, never in the future.
    expect(new Date(from as string).getTime()).toBeLessThan(Date.now());
  });

  it("drops the `from` bound when the period is switched to All time", async () => {
    const seen = captureAudit();
    const user = userEvent.setup();
    renderAs("owner", <AuditLogPage />);

    await screen.findByText("10.0.0.4");
    expect(seen.at(-1)?.searchParams.has("from")).toBe(true);

    await user.click(screen.getByRole("button", { name: /Period/ }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "All time" }));

    await waitFor(() => {
      expect(seen.at(-1)?.searchParams.has("from")).toBe(false);
    });
  });

  it("filters by actor — the picked user lands in ?actor_id", async () => {
    const seen = captureAudit();
    server.use(
      http.get(apiUrl("/admin/users"), () =>
        HttpResponse.json({
          items: [
            {
              id: 42,
              email: "anna@acme.example",
              full_name: "Anna Orlova",
              role: "member",
              status: "active",
              must_change_password: false,
              timezone: null,
              locale: null,
              date_format: null,
              last_login_at: null,
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
          total: 1,
          page: 1,
          per_page: 6,
        }),
      ),
    );
    const user = userEvent.setup();
    renderAs("owner", <AuditLogPage />);

    await screen.findByText("10.0.0.4");
    await user.click(screen.getByRole("button", { name: "Actor" }));
    await user.type(screen.getByPlaceholderText("Search by name or email"), "an");
    await user.click(await screen.findByText("Anna Orlova"));

    await waitFor(() => {
      expect(seen.at(-1)?.searchParams.get("actor_id")).toBe("42");
    });
  });

  it("shows the action as a human label, not the raw code", async () => {
    server.use(http.get(apiUrl("/admin/audit-log"), () => HttpResponse.json(ENTRIES)));
    renderAs("owner", <AuditLogPage />);

    expect(await screen.findByText("Sign-in")).toBeInTheDocument();
    expect(screen.getByText("Sign-out")).toBeInTheDocument();
    expect(screen.queryByText("auth.login")).not.toBeInTheDocument();
  });

  it("expands a row into its action code, meta and user agent", async () => {
    server.use(http.get(apiUrl("/admin/audit-log"), () => HttpResponse.json(ENTRIES)));
    const user = userEvent.setup();
    renderAs("owner", <AuditLogPage />);

    await screen.findByText("10.0.0.4");
    expect(screen.queryByText(/Mozilla/)).not.toBeInTheDocument();

    await user.click(screen.getByText("Sign-in"));

    // The machine code stays visible for forensics — in the expanded record.
    expect(await screen.findByText("auth.login")).toBeInTheDocument();
    expect(screen.getByText(/Mozilla\/5\.0/)).toBeInTheDocument();
    expect(screen.getByText(/"reason": "expired"/)).toBeInTheDocument();
  });

  it("refetches on every visit — a live journal ignores the 30s stale window", async () => {
    // Reproduce the production QueryClient: a non-zero staleTime is exactly what
    // let a just-written event stay hidden until it expired. The audit query pins
    // staleTime:0, so a remount must refetch even though the cached data is fresh.
    const seen = captureAudit();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    });
    const owner: SessionUser = {
      id: 1,
      email: "owner@acme.example",
      full_name: "Owner",
      role: "owner",
      status: "active",
      must_change_password: false,
      timezone: null,
      locale: null,
      date_format: null,
      last_login_at: null,
      created_at: "2026-01-01T00:00:00Z",
    };
    const view = (
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SessionContext.Provider value={{ status: "authenticated", user: owner, expired: false }}>
            <AuditLogPage />
          </SessionContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>
    );

    const first = render(view);
    await screen.findByText("10.0.0.4");
    expect(seen).toHaveLength(1);
    first.unmount();

    // Re-enter the screen well inside the 30s window: the fix forces a refetch.
    render(view);
    await waitFor(() => {
      expect(seen).toHaveLength(2);
    });
  });

  it("is forbidden for the Admin role", () => {
    renderAs("admin", <AuditLogPage />);

    expect(screen.getByText("403 — access denied")).toBeInTheDocument();
  });

  it("is forbidden for a Member", () => {
    renderAs("member", <AuditLogPage />);

    expect(screen.getByText("403 — access denied")).toBeInTheDocument();
  });
});
