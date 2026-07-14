import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { AgentEditorPage } from "../AgentEditorPage";
import type { Agent, AgentOptions } from "../types";

const OPTIONS: AgentOptions = {
  models: [{ id: 1, display_name: "Sonnet", is_default: true }],
  tools: [{ id: 3, name: "fetch_url" }],
  core_tools: ["search", "graph", "sql"],
};

function agent(overrides: Partial<Agent>): Agent {
  return {
    id: 5,
    name: "Weekly digest",
    description: null,
    prompt: "Summarize the week.",
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

const EMPTY_RUNS = { items: [], next_cursor: null };

function routed(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/agents/new" element={<AgentEditorPage />} />
      <Route path="/agents/:agentId" element={<AgentEditorPage />} />
    </Routes>,
    { route },
  );
}

describe("AgentEditorPage", () => {
  it("gates save until name and prompt are filled, then POSTs the create body", async () => {
    let createBody: unknown = null;
    server.use(
      http.get(apiUrl("/agents/options"), () => HttpResponse.json(OPTIONS)),
      http.post(apiUrl("/agents"), async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json(agent({ id: 99, name: "Digest", prompt: "Do things" }), {
          status: 201,
        });
      }),
      // Reached after the create navigates to /agents/99.
      http.get(apiUrl("/agents/99"), () =>
        HttpResponse.json(agent({ id: 99, name: "Digest", prompt: "Do things" })),
      ),
      http.get(apiUrl("/agents/99/runs"), () => HttpResponse.json(EMPTY_RUNS)),
    );
    const user = userEvent.setup();
    routed("/agents/new");

    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText("Name"), "Digest");
    await user.type(screen.getByLabelText("Prompt"), "Do things");
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() => {
      expect(createBody).toEqual({
        name: "Digest",
        description: null,
        prompt: "Do things",
        schedule: null,
        model_id: null, // backend presets the list default
        tool_ids: [],
      });
    });
  });

  it("shows an admin-revoked tool as a locked pill and keeps it in the save payload", async () => {
    let patchBody: unknown = null;
    server.use(
      http.get(apiUrl("/agents/options"), () => HttpResponse.json(OPTIONS)),
      http.get(apiUrl("/agents/5"), () =>
        HttpResponse.json(
          agent({ tool_ids: [9], disabled_tools: [{ id: 9, name: "web_search" }] }),
        ),
      ),
      http.get(apiUrl("/agents/5/runs"), () => HttpResponse.json(EMPTY_RUNS)),
      http.patch(apiUrl("/agents/5"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json(
          agent({ tool_ids: [9], disabled_tools: [{ id: 9, name: "web_search" }] }),
        );
      }),
    );
    const user = userEvent.setup();
    routed("/agents/5");

    // The revoked tool is not offered as a toggle (options omit it) but is shown,
    // locked, so the owner sees it is still attached.
    await screen.findByText("web_search");
    expect(screen.queryByRole("button", { name: "web_search" })).not.toBeInTheDocument();
    // The allowed tool is a real toggle.
    expect(screen.getByRole("button", { name: "fetch_url" })).toBeInTheDocument();

    // Re-saving must echo the grandfathered id — dropping it would silently
    // detach a tool the owner never chose to remove.
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(patchBody).toMatchObject({ tool_ids: [9] });
    });
  });

  it("surfaces a run failure as a banner without navigating away", async () => {
    server.use(
      http.get(apiUrl("/agents/options"), () => HttpResponse.json(OPTIONS)),
      http.get(apiUrl("/agents/5"), () => HttpResponse.json(agent({ status: "active" }))),
      http.get(apiUrl("/agents/5/runs"), () => HttpResponse.json(EMPTY_RUNS)),
      http.post(apiUrl("/agents/5/run"), () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Weekly token budget exceeded",
            status: 409,
            code: "AGENT_BUDGET_EXCEEDED",
            detail: "The owner's weekly agent budget is exhausted.",
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    routed("/agents/5");

    // Two "Run" controls exist (header action + none here); the header button is
    // the enabled one for an active agent.
    const runButton = await screen.findByRole("button", { name: "Run" });
    await user.click(runButton);

    expect(await screen.findByText(/weekly token limit/i)).toBeInTheDocument();
  });
});
