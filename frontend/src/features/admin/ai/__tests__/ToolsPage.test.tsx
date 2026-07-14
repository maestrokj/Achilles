import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { ToolsPage } from "../ToolsPage";
import type { Tool } from "../types";

const WEB_SEARCH: Tool = {
  id: 5,
  name: "web_search",
  source: "preset",
  access: "read_only",
  config: { provider: "tavily" },
  credential_is_set: true,
  needs_credential: true,
  chat_enabled: true,
  agents_allowed: false,
  status: "active",
  last_check_at: new Date(Date.now() - 2 * 60_000).toISOString(),
  parameters: {},
};

/** Registered type without an instance row — the id:null case. */
const FETCH_URL: Tool = {
  id: null,
  name: "fetch_url",
  source: "preset",
  access: "read_only",
  config: null,
  credential_is_set: false,
  needs_credential: false,
  chat_enabled: false,
  agents_allowed: false,
  status: "unchecked",
  last_check_at: null,
  parameters: {},
};

function stubTools(tools: Tool[] = [WEB_SEARCH, FETCH_URL]) {
  server.use(http.get(apiUrl("/admin/ai/tools"), () => HttpResponse.json(tools)));
}

describe("ToolsPage", () => {
  it("materializes an instance row via POST before toggling an id:null tool", async () => {
    stubTools();
    let createBody: unknown = null;
    let patchedId: string | null = null;
    let patchBody: unknown = null;
    server.use(
      http.post(apiUrl("/admin/ai/tools"), async ({ request }) => {
        createBody = await request.json();
        return HttpResponse.json({ ...FETCH_URL, id: 9 }, { status: 201 });
      }),
      http.patch(apiUrl("/admin/ai/tools/:id"), async ({ request, params }) => {
        patchedId = params["id"] as string;
        patchBody = await request.json();
        return HttpResponse.json({ ...FETCH_URL, id: 9, chat_enabled: true });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);

    const chatSwitches = await screen.findAllByRole("switch", { name: /Chat/ });
    const fetchChat = chatSwitches[1]; // cards follow the tools order: web_search, fetch_url
    await user.click(fetchChat);

    await waitFor(() => {
      expect(createBody).toEqual({ name: "fetch_url" });
      expect(patchedId).toBe("9");
      expect(patchBody).toEqual({ chat_enabled: true });
    });
  });

  it("saves the web_search engine choice inside config", async () => {
    stubTools();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/ai/tools/5"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...WEB_SEARCH, config: { provider: "brave" } });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);

    await user.click(await screen.findByRole("button", { name: "Configure" }));
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Brave" }));
    await user.type(screen.getByLabelText("API key"), "sk-new");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ config: { provider: "brave" }, credential: "sk-new" });
    });
  });

  it("reveals the Google CSE cx field and saves it inside config", async () => {
    stubTools();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/ai/tools/5"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({
          ...WEB_SEARCH,
          config: { provider: "google_cse", cx: "cx-42" },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);

    await user.click(await screen.findByRole("button", { name: "Configure" }));
    // The cx field is hidden until Google CSE is the chosen engine.
    expect(screen.queryByLabelText(/Search engine ID/)).not.toBeInTheDocument();
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Google CSE" }));
    await user.type(await screen.findByLabelText(/Search engine ID/), "cx-42");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ config: { provider: "google_cse", cx: "cx-42" } });
    });
  });

  it("repaints the right-rail status after a check flips it unchecked → error", async () => {
    // The status pill is hidden while unchecked; a failed probe must reveal it
    // as a red dot with the "error" label, driven by the list refetch — not the
    // mutation's own return value.
    const unchecked: Tool = {
      ...WEB_SEARCH,
      status: "unchecked",
      last_check_at: null,
      credential_is_set: false,
    };
    let current: Tool = unchecked;
    server.use(
      http.get(apiUrl("/admin/ai/tools"), () => HttpResponse.json([current])),
      http.post(apiUrl("/admin/ai/tools/5/check"), () => {
        const at = new Date().toISOString();
        current = { ...current, status: "error", last_check_at: at };
        return HttpResponse.json({ status: "error", last_check_at: at });
      }),
    );
    const user = userEvent.setup();
    const { container } = renderWithProviders(<ToolsPage />);

    // Unchecked → no health signal at all.
    await screen.findByText("Web search");
    expect(screen.queryByText("working")).not.toBeInTheDocument();
    expect(screen.queryByText("error")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Check" }));

    // After the refetch the pill appears: red dot + "error · 0 min ago".
    expect(await screen.findByText(/error · 0 min ago/)).toBeInTheDocument();
    expect(container.querySelector(".bg-destructive")).not.toBeNull();
    expect(container.querySelector(".bg-success")).toBeNull();
  });

  it("shows the last probe age and runs a health check from the row", async () => {
    stubTools();
    let checked = false;
    server.use(
      http.post(apiUrl("/admin/ai/tools/5/check"), () => {
        checked = true;
        return HttpResponse.json({
          status: "active",
          last_check_at: new Date().toISOString(),
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);

    // last_check_at is two minutes old — the status pill carries the age.
    expect(await screen.findByText("working · 2 min ago")).toBeInTheDocument();
    expect(screen.getByText(/Tavily · key set/)).toBeInTheDocument();

    const [checkWebSearch] = screen.getAllByRole("button", { name: "Check" });
    await user.click(checkWebSearch);

    await waitFor(() => {
      expect(checked).toBe(true);
    });
  });
});
