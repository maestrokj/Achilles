import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { PlatformSettingsPage } from "../PlatformSettingsPage";
import type { PlatformSettings, SlackSettings } from "../types";

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
  agent_weekly_token_budget: null,
  sync_interval_minutes: 15,
  reconcile_minute_of_week: 8820,
  watchdog_silence_hours: 12,
  curation_frequency: "daily",
  curation_weekday: null,
  curation_time: "04:00",
  updated_at: "2026-07-04T10:00:00Z",
  smtp_configured: false,
};

const SLACK_SETTINGS: SlackSettings = {
  enabled: false,
  auto_link_by_email: true,
  team: null,
  team_name: null,
  bot_user_id: null,
  bot_token_mask: null,
  signing_secret_set: false,
  last_test_ok: null,
  last_test_at: null,
};

function stubGet(settings: PlatformSettings = SETTINGS) {
  server.use(
    http.get(apiUrl("/admin/settings"), () => HttpResponse.json(settings)),
    http.get(apiUrl("/admin/slack"), () => HttpResponse.json(SLACK_SETTINGS)),
  );
}

describe("PlatformSettingsPage", () => {
  it("pre-fills the form from the settings row", async () => {
    stubGet();
    renderAs("owner", <PlatformSettingsPage />);

    expect(await screen.findByDisplayValue("Acme Corp")).toBeInTheDocument();
    // The combobox also renders an aria-hidden input for form submission, so
    // reach for the one in the accessibility tree rather than matching on the
    // displayed value alone (which both inputs carry).
    expect(screen.getByRole("combobox", { name: /timezone/i })).toHaveValue("Europe/Moscow");
    // 900s access TTL renders as 15 minutes.
    expect(screen.getByDisplayValue("15")).toBeInTheDocument();
  });

  it("PATCHes only the fields the owner actually changed", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/settings"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, org_name: "New Name" });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <PlatformSettingsPage />);

    const nameInput = await screen.findByDisplayValue("Acme Corp");
    await user.clear(nameInput);
    await user.type(nameInput, "New Name");
    const [firstSave] = screen.getAllByRole("button", { name: "Save" });
    await user.click(firstSave);

    await waitFor(() => {
      expect(patchBody).toEqual({ org_name: "New Name" });
    });
  });

  it("refuses a refresh window longer than the whole session", async () => {
    stubGet();
    const user = userEvent.setup();
    renderAs("owner", <PlatformSettingsPage />);

    const absolute = await screen.findByLabelText(/maximum session length/i);
    await user.clear(absolute); // 90 days → 10, below the 30-day refresh window
    await user.type(absolute, "10");

    expect(screen.getByText(/a session nests/i)).toBeInTheDocument();
    const [, sessionSave] = screen.getAllByRole("button", { name: "Save" });
    expect(sessionSave).toBeDisabled();
  });

  it("is read-only for the Admin role", async () => {
    stubGet();
    renderAs("admin", <PlatformSettingsPage />);

    const nameInput = await screen.findByDisplayValue("Acme Corp");
    expect(nameInput).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.getByText("read-only")).toBeInTheDocument();
  });

  it("asks for confirmation before disabling the MCP server", async () => {
    stubGet(); // mcp_enabled: true — the switch starts on
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/settings"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, mcp_enabled: false });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <PlatformSettingsPage />);

    await screen.findByDisplayValue("Acme Corp");
    const mcpSwitch = screen.getAllByRole("switch").at(0); // first toggle
    if (!mcpSwitch) throw new Error("no MCP switch rendered");
    await user.click(mcpSwitch); // turning it off is the disruptive direction
    expect(patchBody, "no PATCH before the confirmation").toBeNull();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => {
      expect(patchBody).toEqual({ mcp_enabled: false });
    });
  });

  it("enables the MCP server immediately, without confirmation", async () => {
    stubGet({ ...SETTINGS, mcp_enabled: false }); // starts off
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/settings"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, mcp_enabled: true });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <PlatformSettingsPage />);

    await screen.findByDisplayValue("Acme Corp");
    const mcpSwitch = screen.getAllByRole("switch").at(0);
    if (!mcpSwitch) throw new Error("no MCP switch rendered");
    await user.click(mcpSwitch); // turning it on is the safe direction — no dialog
    await waitFor(() => {
      expect(patchBody).toEqual({ mcp_enabled: true });
    });
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });

  it("asks for confirmation before enabling maintenance mode", async () => {
    stubGet();
    let patched = false;
    server.use(
      http.patch(apiUrl("/admin/settings"), () => {
        patched = true;
        return HttpResponse.json({ ...SETTINGS, maintenance_mode: true });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <PlatformSettingsPage />);

    await screen.findByDisplayValue("Acme Corp");
    const maintenanceSwitch = screen.getAllByRole("switch").at(1); // second toggle
    if (!maintenanceSwitch) throw new Error("no maintenance switch rendered");
    await user.click(maintenanceSwitch);
    expect(patched, "no PATCH before the confirmation").toBe(false);

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => {
      expect(patched).toBe(true);
    });
  });

  it("disables maintenance mode immediately, without confirmation", async () => {
    stubGet({ ...SETTINGS, maintenance_mode: true }); // starts on
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/settings"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, maintenance_mode: false });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <PlatformSettingsPage />);

    await screen.findByDisplayValue("Acme Corp");
    const maintenanceSwitch = screen.getAllByRole("switch").at(1); // second toggle
    if (!maintenanceSwitch) throw new Error("no maintenance switch rendered");
    await user.click(maintenanceSwitch); // turning it off is the safe direction — no dialog
    await waitFor(() => {
      expect(patchBody).toEqual({ maintenance_mode: false });
    });
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });
});
