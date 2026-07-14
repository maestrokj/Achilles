import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { PlatformSettings } from "@/features/admin/platform/types";
import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { UsagePage } from "../UsagePage";
import type { ModelSpend, Usage } from "../types";

const SETTINGS: PlatformSettings = {
  org_name: "Acme Corp",
  org_logo_url: null,
  org_description: null,
  accent_color: "#6366f1",
  timezone: "Europe/Moscow",
  locale: "ru",
  date_format: "DD.MM.YYYY",
  locale_choices: ["ru", "en"],
  date_format_choices: ["DD.MM.YYYY", "MM/DD/YYYY", "YYYY-MM-DD"],
  access_token_ttl: 900,
  refresh_token_ttl: 2_592_000,
  session_absolute_ttl: 7_776_000,
  maintenance_mode: false,
  mcp_enabled: true,
  ai_monthly_budget: null,
  ai_budget_alert_enabled: false,
  chat_weekly_token_budget: null,
  agent_weekly_token_budget: 500_000,
  sync_interval_minutes: 15,
  reconcile_minute_of_week: 8820,
  watchdog_silence_hours: 12,
  curation_frequency: "daily",
  curation_weekday: null,
  curation_time: "04:00",
  updated_at: "2026-07-04T10:00:00Z",
  smtp_configured: false,
};

function modelRow(fn: string, index: number): ModelSpend {
  return {
    display_name: `model-${String(index)}`,
    provider_name: "OpenAI",
    function: fn,
    request_count: 10 + index,
    input_tokens: 1000 * (index + 1),
    output_tokens: 500,
    cost: "1.00",
  };
}

const USAGE: Usage = {
  totals: {
    week: { tokens: 1_540_000, cost: "12.40" },
    month: { tokens: 4_200_000, cost: null },
    year: { tokens: 9_100_000, cost: "88.00" },
  },
  limits: {
    agent_weekly_token_budget: 500_000,
    chat_weekly_token_budget: null,
    ai_monthly_budget: null,
    ai_budget_alert_enabled: false,
  },
  by_user: {
    items: [
      {
        user_id: 7,
        full_name: "Ada Lovelace",
        email: "ada@acme.example",
        role: "member",
        agent_tokens: 620_000,
        chat_tokens: 34_000,
        total_tokens: 654_000,
        agent_over_limit: true,
        chat_over_limit: false,
      },
      {
        user_id: 8,
        full_name: "Bob Ross",
        email: "bob@acme.example",
        role: "member",
        agent_tokens: 0,
        chat_tokens: 12_000,
        total_tokens: 12_000,
        agent_over_limit: false,
        chat_over_limit: false,
      },
    ],
    total: 2,
    page: 1,
    per_page: 25,
  },
  by_model: [
    "chat",
    "agent_engine",
    "query_rag",
    "harvester_embedding",
    "chat",
    "agent_engine",
  ].map(modelRow),
};

function stubUsage(usage: Usage = USAGE) {
  server.use(
    http.get(apiUrl("/admin/usage"), () => HttpResponse.json(usage)),
    http.get(apiUrl("/admin/settings"), () => HttpResponse.json(SETTINGS)),
  );
}

describe("UsagePage", () => {
  it("renders totals, the per-person table and the over-limit pill", async () => {
    stubUsage();
    renderWithProviders(<UsagePage />);

    // Week tile: 1_540_000 → 1.5M (Intl compact) with its dollar cost.
    expect(await screen.findByText("1.5M")).toBeInTheDocument();
    expect(screen.getByText(/\$12\.40/)).toBeInTheDocument();
    // Month cost is NULL-poisoned (a model without prices) → "no pricing set".
    expect(screen.getAllByText(/no pricing set/).length).toBeGreaterThan(0);

    // The name links to the profile like everywhere else; a separate action
    // opens this person's spend breakdown.
    expect(screen.getByRole("link", { name: "Ada Lovelace" })).toHaveAttribute(
      "href",
      "/admin/users/7",
    );
    const [detail] = screen.getAllByRole("link", { name: "Details" });
    expect(detail).toHaveAttribute("href", "/admin/ai-usage/7");
    expect(screen.getByText("weekly limit reached")).toBeInTheDocument();
    expect(screen.queryByText("over advisory")).not.toBeInTheDocument();
  });

  it("saves spend limits through PATCH /admin/settings", async () => {
    stubUsage();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/settings"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, chat_weekly_token_budget: 100_000 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<UsagePage />);

    // The field speaks millions of tokens: "0.1" → 100_000 on the wire.
    const chatInput = await screen.findByLabelText("Chat");
    await user.type(chatInput, "0.1");
    const [saveLimits] = screen.getAllByRole("button", { name: "Save" });
    await user.click(saveLimits);

    await waitFor(() => {
      expect(patchBody).toEqual({
        agent_weekly_token_budget: 500_000,
        chat_weekly_token_budget: 100_000,
        ai_monthly_budget: null,
        ai_budget_alert_enabled: false,
      });
    });
  });

  it("paginates the by-model table at ten rows a page", async () => {
    // Twelve rows spill onto a second page (SPEND_PER_PAGE = 10).
    const many = ["chat", "agents", "rag", "embedding", "chat", "agents"]
      .concat(["chat", "agents", "rag", "embedding", "chat", "agents"])
      .map(modelRow);
    stubUsage({ ...USAGE, by_model: many });
    const user = userEvent.setup();
    renderWithProviders(<UsagePage />);

    // First page shows the first ten; the eleventh waits on page two.
    expect(await screen.findByText("model-0")).toBeInTheDocument();
    expect(screen.getByText("model-9")).toBeInTheDocument();
    expect(screen.queryByText("model-10")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "2" }));
    expect(await screen.findByText("model-10")).toBeInTheDocument();
    expect(screen.queryByText("model-0")).not.toBeInTheDocument();
  });

  it("keeps the by-model table on one page when it fits", async () => {
    stubUsage();
    renderWithProviders(<UsagePage />);

    // Six rows fit under the ten-row page — every model shows, no pager.
    expect(await screen.findByText("model-0")).toBeInTheDocument();
    expect(screen.getByText("model-5")).toBeInTheDocument();
  });

  it("tells the server its ten-row page size for the by-user table", async () => {
    // The server default is 50; the by-user table pages at 10. If the size never
    // crosses the wire the server returns 50 rows while the UI paginates by 10.
    let seen: URLSearchParams | null = null;
    server.use(
      http.get(apiUrl("/admin/usage"), ({ request }) => {
        seen = new URL(request.url).searchParams;
        return HttpResponse.json(USAGE);
      }),
      http.get(apiUrl("/admin/settings"), () => HttpResponse.json(SETTINGS)),
    );
    renderWithProviders(<UsagePage />);

    await screen.findByText("1.5M");
    await waitFor(() => {
      expect(seen?.get("per_page")).toBe("10");
    });
  });
});
