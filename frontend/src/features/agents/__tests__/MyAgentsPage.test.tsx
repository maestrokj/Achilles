import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { MyAgentsPage } from "../MyAgentsPage";
import type { Agent, AgentList } from "../types";

function agent(overrides: Partial<Agent>): Agent {
  return {
    id: 1,
    name: "Weekly digest",
    description: "Summarizes the week",
    prompt: "…",
    schedule: null,
    model_id: 1,
    enabled: true,
    admin_paused: false,
    status: "active",
    tool_ids: [],
    disabled_tools: [],
    next_run_at: null,
    last_run: null,
    created_at: "2026-06-01T00:00:00Z",
    ...overrides,
  };
}

function stubList(list: AgentList) {
  server.use(http.get(apiUrl("/agents"), () => HttpResponse.json(list)));
}

const BUDGET = { used: 1500, limit: 10000, week_resets_at: "2026-07-19T00:00:00Z" };

/** The card for a given agent name — controls (toggle, run) are queried within it. */
function cardOf(name: string): HTMLElement {
  return screen.getByText(name).closest("[data-slot=card]") as HTMLElement;
}

describe("MyAgentsPage", () => {
  it("shows the empty state when the owner has no agents", async () => {
    stubList({ items: [], budget: BUDGET });
    renderWithProviders(<MyAgentsPage />);
    expect(await screen.findByText("No agents yet")).toBeInTheDocument();
  });

  it("renders the weekly budget tile and the agent cards", async () => {
    stubList({ items: [agent({})], budget: BUDGET });
    renderWithProviders(<MyAgentsPage />);

    await screen.findByText("Weekly digest");
    expect(screen.getByText("Tokens this week")).toBeInTheDocument();
    // used / limit, both localized (1.5K / 10K).
    expect(screen.getByText("1.5K")).toBeInTheDocument();
  });

  it("only lets an active agent run; a budget-exceeded one is disabled", async () => {
    stubList({
      items: [
        agent({ id: 1, name: "Active one", status: "active" }),
        agent({ id: 2, name: "Spent one", status: "budget_exceeded" }),
      ],
      budget: BUDGET,
    });
    renderWithProviders(<MyAgentsPage />);

    await screen.findByText("Active one");
    expect(within(cardOf("Active one")).getByRole("button", { name: "Run" })).toBeEnabled();
    expect(within(cardOf("Spent one")).getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("toggles an agent off through PATCH { enabled: false }", async () => {
    let patchBody: unknown = null;
    stubList({ items: [agent({ id: 1, name: "Active one", enabled: true })], budget: BUDGET });
    server.use(
      http.patch(apiUrl("/agents/1"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json(agent({ id: 1, enabled: false, status: "disabled" }));
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<MyAgentsPage />);

    await screen.findByText("Active one");
    await user.click(within(cardOf("Active one")).getByRole("switch"));
    await waitFor(() => {
      expect(patchBody).toEqual({ enabled: false });
    });
  });

  it("fires a manual run through POST /agents/{id}/run", async () => {
    let ran = false;
    stubList({ items: [agent({ id: 1, name: "Active one", status: "active" })], budget: BUDGET });
    server.use(
      http.post(apiUrl("/agents/1/run"), () => {
        ran = true;
        return HttpResponse.json({ run_id: 77 }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<MyAgentsPage />);

    await screen.findByText("Active one");
    await user.click(within(cardOf("Active one")).getByRole("button", { name: "Run" }));
    await waitFor(() => {
      expect(ran).toBe(true);
    });
  });
});
