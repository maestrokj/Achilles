import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import type { AdminAgent } from "../../types";
import { AdminAgentsPage } from "../AdminAgentsPage";

function agent(overrides: Partial<AdminAgent>): AdminAgent {
  return {
    id: 7,
    name: "Nightly digest",
    description: null,
    schedule: { type: "calendar", cadence: "daily", time: "06:00" },
    enabled: true,
    admin_paused: false,
    status: "active",
    owner: { id: 42, email: "anna@acme.example", display_name: "Anna Orlova" },
    last_run: {
      state: "succeeded",
      reason: null,
      finished_at: "2026-07-04T06:00:20Z",
      duration_seconds: 45,
      tokens_used: 1500,
    },
    created_at: "2026-06-01T00:00:00Z",
    ...overrides,
  };
}

const LIMITS = { iteration_cap: 12, max_concurrency: 3 };

function stubList(agents: AdminAgent[]) {
  server.use(
    http.get(apiUrl("/admin/agent-limits"), () => HttpResponse.json(LIMITS)),
    http.get(apiUrl("/admin/agents"), () =>
      HttpResponse.json({ items: agents, total: agents.length, page: 1, per_page: 50 }),
    ),
  );
}

describe("AdminAgentsPage", () => {
  it("links the owner to their user card and shows the last-run meta", async () => {
    stubList([agent({})]);
    renderWithProviders(<AdminAgentsPage />);

    await screen.findByText("Nightly digest");
    expect(screen.getByRole("link", { name: "Anna Orlova" })).toHaveAttribute(
      "href",
      "/admin/users/42",
    );
    // Last-run meta: run state · duration · tokens, all localized (45s, 1.5K).
    expect(screen.getByText("Completed · 45s · 1.5K")).toBeInTheDocument();
  });

  it("routes the status and schedule facets into the list query", async () => {
    const urls: string[] = [];
    server.use(
      http.get(apiUrl("/admin/agent-limits"), () => HttpResponse.json(LIMITS)),
      http.get(apiUrl("/admin/agents"), ({ request }) => {
        urls.push(request.url);
        return HttpResponse.json({ items: [agent({})], total: 1, page: 1, per_page: 50 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AdminAgentsPage />);

    await screen.findByText("Nightly digest");

    await user.click(screen.getByRole("button", { name: "Status" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Disabled" }));
    await waitFor(() => {
      expect(urls.some((url) => url.includes("status=disabled"))).toBe(true);
    });

    await user.click(screen.getByRole("button", { name: "Schedule" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Scheduled" }));
    await waitFor(() => {
      expect(urls.some((url) => url.includes("scheduled=true"))).toBe(true);
    });
  });

  it("pauses an agent through the row menu and confirm dialog", async () => {
    let pauseBody: unknown = null;
    server.use(
      http.get(apiUrl("/admin/agent-limits"), () => HttpResponse.json(LIMITS)),
      http.get(apiUrl("/admin/agents"), () =>
        HttpResponse.json({
          items: [agent({ admin_paused: false })],
          total: 1,
          page: 1,
          per_page: 50,
        }),
      ),
      http.patch(apiUrl("/admin/agents/7/pause"), async ({ request }) => {
        pauseBody = await request.json();
        return HttpResponse.json(agent({ admin_paused: true, status: "admin_paused" }));
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AdminAgentsPage />);
    await screen.findByText("Nightly digest");

    await user.click(screen.getByRole("button", { name: "Agent actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Pause" }));
    // Dialog copy tracks the current (un-paused) state, not "Resume".
    expect(await screen.findByText("Pause this agent?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(pauseBody).toEqual({ paused: true });
    });
  });

  it("saves edited platform run limits, sending both fields", async () => {
    let patchBody: unknown = null;
    server.use(
      http.get(apiUrl("/admin/agent-limits"), () => HttpResponse.json(LIMITS)),
      http.get(apiUrl("/admin/agents"), () =>
        HttpResponse.json({ items: [agent({})], total: 1, page: 1, per_page: 50 }),
      ),
      http.patch(apiUrl("/admin/agent-limits"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ iteration_cap: 20, max_concurrency: 3 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AdminAgentsPage />);

    // Seeded from the current limits; editing one field still submits both.
    // Two number inputs in the card — steps-per-run first, concurrency second.
    const [cap] = await screen.findAllByRole("spinbutton");
    expect(cap).toHaveValue(12);
    await user.clear(cap);
    await user.type(cap, "20");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ iteration_cap: 20, max_concurrency: 3 });
    });
  });

  it("shows the empty state when no agents exist", async () => {
    stubList([]);
    renderWithProviders(<AdminAgentsPage />);

    expect(await screen.findByText("No agents yet")).toBeInTheDocument();
  });
});
