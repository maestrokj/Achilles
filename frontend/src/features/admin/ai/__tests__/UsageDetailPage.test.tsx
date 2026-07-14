import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { Routes, Route } from "react-router-dom";

import { UsageDetailPage } from "../UsageDetailPage";
import type { UserUsage } from "../types";

const USAGE: UserUsage = {
  user_id: 7,
  full_name: "Ada Lovelace",
  email: "ada@acme.example",
  agent_tokens: 620_000,
  chat_tokens: 34_000,
  limits: {
    agent_weekly_token_budget: 500_000,
    chat_weekly_token_budget: null,
    ai_monthly_budget: null,
    ai_budget_alert_enabled: false,
  },
  agents: [
    { agent_id: 3, name: "Release digest", model: "gpt-4o", runs: 4, tokens: 600_000 },
    { agent_id: 5, name: "Standup notes", model: null, runs: 1, tokens: 20_000 },
  ],
  chat: [
    { model: "gpt-4o", messages: 12, tokens: 30_000 },
    { model: "claude-sonnet", messages: 2, tokens: 4_000 },
  ],
};

/** Mount the page under a route so `useParams().userId` resolves. */
function renderDetail(userId = "7") {
  return renderWithProviders(
    <Routes>
      <Route path="/admin/ai-usage/:userId" element={<UsageDetailPage />} />
    </Routes>,
    { route: `/admin/ai-usage/${userId}` },
  );
}

function stub(usage: UserUsage = USAGE) {
  server.use(http.get(apiUrl("/admin/usage/:id"), () => HttpResponse.json(usage)));
}

describe("UsageDetailPage", () => {
  it("renders the person, meters and both breakdown tables", async () => {
    stub();
    renderDetail();

    expect(await screen.findByRole("heading", { name: "Ada Lovelace" })).toBeInTheDocument();
    // Agent meter is over its blocking cap (620k ≥ 500k) → the reached pill.
    expect(screen.getByText("weekly limit reached")).toBeInTheDocument();
    // Both breakdown rows deep-link to the agent editor.
    expect(screen.getByRole("link", { name: "Release digest" })).toHaveAttribute(
      "href",
      "/admin/agents/3",
    );
    expect(screen.getByText("Standup notes")).toBeInTheDocument();
    // Chat models show alongside their message counts.
    expect(screen.getByText("claude-sonnet")).toBeInTheDocument();
  });

  it("refetches for a different window", async () => {
    let seenWindow: string | null = null;
    server.use(
      http.get(apiUrl("/admin/usage/:id"), ({ request }) => {
        seenWindow = new URL(request.url).searchParams.get("window");
        return HttpResponse.json(USAGE);
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("heading", { name: "Ada Lovelace" });
    expect(seenWindow).toBe("week");

    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Previous week" }));
    await waitFor(() => {
      expect(seenWindow).toBe("prev_week");
    });
  });

  it("shows a quiet placeholder when a period has no activity", async () => {
    stub({ ...USAGE, agents: [], chat: [] });
    renderDetail();

    await screen.findByRole("heading", { name: "Ada Lovelace" });
    const empties = screen.getAllByText("No activity in this period");
    expect(empties.length).toBe(2); // one per table
  });

  it("surfaces a load error with a retry", async () => {
    server.use(http.get(apiUrl("/admin/usage/:id"), () => new HttpResponse(null, { status: 404 })));
    renderDetail();

    expect(await screen.findByRole("button", { name: /retry|try again/i })).toBeInTheDocument();
  });

  it("uses the agent's current model as the model column, dashing an unset one", async () => {
    stub();
    renderDetail();

    const row = (await screen.findByText("Standup notes")).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("—")).toBeInTheDocument();
  });
});
