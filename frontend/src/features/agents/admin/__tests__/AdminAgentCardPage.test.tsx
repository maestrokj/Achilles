import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import type { AdminAgentDetail } from "../../types";
import { AdminAgentCardPage } from "../AdminAgentCardPage";

const DETAIL: AdminAgentDetail = {
  id: 1,
  name: "Nightly digest",
  description: "Summarize the day",
  schedule: null,
  enabled: true,
  admin_paused: false,
  status: "active",
  owner: { id: 42, email: "anna@acme.example", display_name: "Anna Orlova" },
  last_run: null,
  created_at: "2026-06-01T00:00:00Z",
  prompt: "Summarize the day's activity.",
  model_name: "gpt-4o",
  tools: [],
  next_run_at: null,
  owner_budget: { used: 1000, limit: 50000, week_resets_at: "2026-07-07T00:00:00Z" },
};

/** Renders the profile route with the given detail + core-tool options. */
function renderCard(detail: AdminAgentDetail = DETAIL, coreTools: string[] = ["search_knowledge"]) {
  server.use(
    http.get(apiUrl("/admin/agents/1"), () => HttpResponse.json(detail)),
    http.get(apiUrl("/agents/options"), () =>
      HttpResponse.json({ models: [], tools: [], core_tools: coreTools }),
    ),
    http.get(apiUrl("/admin/agents/1/runs"), () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
  );
  return renderWithProviders(
    <Routes>
      <Route path="/admin/agents/:agentId" element={<AdminAgentCardPage />} />
      <Route path="/admin/users/:userId" element={<p>user card</p>} />
    </Routes>,
    { route: "/admin/agents/1" },
  );
}

describe("AdminAgentCardPage", () => {
  it("links the owner to their user card, not the roster", async () => {
    renderCard();

    expect(await screen.findByRole("link", { name: "Anna Orlova" })).toHaveAttribute(
      "href",
      "/admin/users/42",
    );
  });

  it("flags a missing model and renders the locked core tools", async () => {
    renderCard({ ...DETAIL, model_name: null, status: "model_missing", tools: [] }, [
      "search",
      "graph",
      "sql",
    ]);

    expect(await screen.findByText("No model selected")).toBeInTheDocument();
    // Locked KS core tools come from options; the agent has no external ones.
    expect(screen.getByText("search")).toBeInTheDocument();
    expect(screen.getByText("no external tools")).toBeInTheDocument();
  });

  it("pauses the agent from the profile header", async () => {
    let pauseBody: unknown = null;
    renderCard();
    server.use(
      http.patch(apiUrl("/admin/agents/1/pause"), async ({ request }) => {
        pauseBody = await request.json();
        return HttpResponse.json({ ...DETAIL, admin_paused: true, status: "admin_paused" });
      }),
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(await screen.findByText("Pause this agent?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(pauseBody).toEqual({ paused: true });
    });
  });
});
