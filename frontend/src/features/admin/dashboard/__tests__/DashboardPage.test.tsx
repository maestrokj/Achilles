import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { DashboardPage } from "../DashboardPage";
import type { Dashboard } from "../types";

const BASE: Dashboard = {
  org_name: "Acme Corp",
  timezone: "Europe/Moscow",
  is_empty: false,
  users: { total: 118, pending_invites: 3, deactivated: 2 },
  sources: { total: 4, active: 3, paused: 1, disconnected: 0, failing: 1 },
  knowledge: { entities: 1_240_000, chunks: 3_100_000, edges: 560_000 },
  agents: { total: 86, active: 80, paused: 6, failing: 2 },
  spend: { month_cost: "168.00", budget: "200", alert_enabled: true },
  last_sync: {
    state: "succeeded",
    started_at: "2026-07-04T08:30:00Z",
    entities: 1240,
    running: 0,
  },
  curation: null,
  last_backup: {
    state: "succeeded",
    started_at: "2026-07-04T02:00:00Z",
    size_bytes: 13_314_398_618,
  },
  audit: [
    {
      action: "auth.login",
      actor_email: "boss@acme.example",
      success: true,
      created_at: "2026-07-04T09:00:00Z",
    },
  ],
  attention: [
    { severity: "critical", kind: "source_failing", subject: "Jira", count: null, source_id: 42 },
    { severity: "warning", kind: "budget", subject: null, count: null, source_id: null },
  ],
  tasks: { pending_invites: 3, unmatched_identities: 1 },
  setup: { email: true, surfaces: true, embedding: true, chat_models: true, agent_models: true },
};

function stub(data: Dashboard = BASE) {
  server.use(http.get(apiUrl("/admin/dashboard"), () => HttpResponse.json(data)));
}

describe("DashboardPage", () => {
  it("renders the tiles, attention rows and the audit top", async () => {
    stub();
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("118")).toBeInTheDocument();
    expect(screen.getByText("1.2M")).toBeInTheDocument();
    expect(screen.getByText("$168.00")).toBeInTheDocument();
    expect(screen.getByText("Source Jira: the latest sync failed")).toBeInTheDocument();
    // The per-source signal routes to the source card the frontend derives from kind + source_id.
    expect(screen.getByRole("link", { name: /the latest sync failed/ })).toHaveAttribute(
      "href",
      "/admin/harvester/sources/42",
    );
    expect(screen.getByText("1 critical")).toBeInTheDocument();
    // The audit tail shows the human label of the action code.
    expect(screen.getByText("Sign-in")).toBeInTheDocument();
    // The users tile links to its home section.
    expect(screen.getByRole("link", { name: /Users/ })).toHaveAttribute("href", "/admin/users");
  });

  it("hides the audit block when the backend returns none (Admin role)", async () => {
    stub({ ...BASE, audit: null });
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("118")).toBeInTheDocument();
    expect(screen.queryByText("Recent activity")).not.toBeInTheDocument();
  });

  it("shows the progressive-value empty state instead of alerts", async () => {
    stub({ ...BASE, is_empty: true, attention: [] });
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText(/knowledge base is empty/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect a source" })).toBeInTheDocument();
  });

  it("renders the backup status in the last-backup card", async () => {
    stub();
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Backups")).toBeInTheDocument();
    // Succeeded backup surfaces its run state; the schedule now lives on the Knowledge Store screen.
    expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
  });

  it("hides the setup checklist when every step is configured", async () => {
    stub();
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("118")).toBeInTheDocument();
    expect(screen.queryByText("Let's set up the platform")).not.toBeInTheDocument();
  });

  it("walks pending setup steps to the screens that complete them", async () => {
    stub({
      ...BASE,
      is_empty: true,
      attention: [],
      setup: {
        email: false,
        surfaces: true,
        embedding: false,
        chat_models: true,
        agent_models: true,
      },
    });
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Let's set up the platform")).toBeInTheDocument();
    // 4 of 6: sources come from the tile (total > 0), email + embedding pend.
    expect(screen.getByText("4/6")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Embedding model/ })).toHaveAttribute(
      "href",
      "/admin/ai-models#assignments",
    );
    expect(screen.getByRole("link", { name: /Email delivery/ })).toHaveAttribute(
      "href",
      "/admin/platform#smtp",
    );
    // The checklist supersedes the generic empty-state card.
    expect(screen.queryByText(/knowledge base is empty/)).not.toBeInTheDocument();
  });

  it("derives the sources step from the sources tile", async () => {
    stub({
      ...BASE,
      sources: { total: 0, active: 0, paused: 0, disconnected: 0, failing: 0 },
    });
    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Let's set up the platform")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Connect company knowledge/ })).toHaveAttribute(
      "href",
      "/admin/harvester",
    );
  });

  it("dismisses the setup checklist for good", async () => {
    stub({ ...BASE, setup: { ...BASE.setup, email: false } });
    const user = userEvent.setup();
    const first = renderWithProviders(<DashboardPage />);

    expect(await screen.findByText("Let's set up the platform")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Don't show again" }));
    expect(screen.queryByText("Let's set up the platform")).not.toBeInTheDocument();

    // A fresh mount honours the stored dismissal.
    first.unmount();
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByText("118")).toBeInTheDocument();
    expect(screen.queryByText("Let's set up the platform")).not.toBeInTheDocument();
  });

  it("counts disconnected sources in the sources tile", async () => {
    stub({
      ...BASE,
      sources: { total: 9, active: 5, paused: 1, disconnected: 2, failing: 1 },
    });
    renderWithProviders(<DashboardPage />);

    const tile = await screen.findByRole("link", { name: /Sources/ });
    expect(tile).toHaveAttribute("href", "/admin/harvester");
    // Total (9) counts the 2 disconnected sources the backend rolls in.
    expect(tile).toHaveTextContent("9");
    // The breakdown sub-label spells the disconnected count out too.
    expect(tile).toHaveTextContent("2 disconnected");
  });
});
