import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { SourceCardPage } from "../SourceCardPage";
import type { ConnectorType, Source } from "../types";

function source(overrides: Partial<Source>): Source {
  return {
    id: 1,
    name: "Team Jira",
    connector_type: "jira",
    state: "active",
    health: "idle",
    base_url: "https://jira.example.test",
    auth_account: "service",
    credential_is_set: true,
    scope_mode: "all",
    scope_list: [],
    content_filters: {},
    sync_interval: null,
    reconcile_interval: null,
    reconcile_window: null,
    authority_tier: "normal",
    incremental_cursor: null,
    last_probe_at: null,
    last_probe_status: null,
    last_sync_at: "2026-07-01T10:00:00Z",
    last_run: null,
    dlq_count: 0,
    entity_count: 0,
    webhook_supported: true,
    webhook_enabled: false,
    webhook_secret_set: false,
    webhook_endpoint_url: "https://acme.test/api/v1/harvester/webhooks/sources/1",
    created_at: "2026-06-01T10:00:00Z",
    ...overrides,
  };
}

const JIRA: ConnectorType = {
  type: "jira",
  title: "Jira",
  needs_base_url: true,
  credential_label: "API token",
  scope_kinds: ["project"],
  collection_toggles: [],
  webhooks: true,
};

function stubCard(src: Source) {
  server.use(
    http.get(apiUrl(`/sources/${String(src.id)}`), () => HttpResponse.json(src)),
    http.get(apiUrl("/sources/connectors"), () => HttpResponse.json([JIRA])),
  );
}

/** The lone enabled "Save" — the config card keeps a disabled credential Save on screen. */
function enabledSave(): HTMLElement {
  const save = screen
    .getAllByRole("button", { name: "Save" })
    .find((button) => !(button as HTMLButtonElement).disabled);
  if (!save) throw new Error("no enabled Save button");
  return save;
}

function renderCard(src: Source) {
  return renderWithProviders(
    <Routes>
      <Route path="/admin/harvester/sources/:sourceId" element={<SourceCardPage />} />
      <Route path="/admin/harvester" element={<p>sources list</p>} />
    </Routes>,
    { route: `/admin/harvester/sources/${String(src.id)}` },
  );
}

describe("SourceCardPage", () => {
  it("saves an edited base URL through a PATCH", async () => {
    const src = source({});
    stubCard(src);
    let body: unknown = null;
    server.use(
      http.patch(apiUrl("/sources/1"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(src);
      }),
    );
    const user = userEvent.setup();
    renderCard(src);

    const input = await screen.findByLabelText("Base URL");
    await user.clear(input);
    await user.type(input, "https://new.example.test");
    await user.click(enabledSave());

    await waitFor(() => {
      expect(body).toEqual({ base_url: "https://new.example.test" });
    });
  });

  it("keeps schedule overrides collapsed while inheriting the global default", async () => {
    stubCard(source({}));
    renderCard(source({}));

    // Incremental + reconciliation both fall back to the hub when unset: the
    // override prompt shows in both blocks and no interval fields are exposed.
    expect(
      await screen.findAllByText("Override the platform default for this source."),
    ).toHaveLength(2);
    expect(screen.queryByLabelText("Reconcile every")).toBeNull();
  });

  it("saves a per-source reconciliation interval override", async () => {
    const src = source({ reconcile_interval: 14, reconcile_window: 8820 });
    stubCard(src);
    let body: unknown = null;
    server.use(
      http.patch(apiUrl("/sources/1"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(src);
      }),
    );
    const user = userEvent.setup();
    renderCard(src);

    const interval = await screen.findByLabelText("Reconcile every");
    await user.clear(interval);
    await user.type(interval, "10");
    await user.click(enabledSave());

    // Save commits all three fields as one draft: the edited interval plus the
    // unchanged window (day 6 · 03:00 → 8820 minutes-of-week).
    await waitFor(() => {
      expect(body).toEqual({ reconcile_interval: 10, reconcile_window: 8820 });
    });
  });

  it("loads the catalog and saves the picked containers in 'selected only' mode", async () => {
    const src = source({ scope_mode: "selected", scope_list: [], base_url: null });
    stubCard(src);
    server.use(
      http.get(apiUrl("/sources/1/catalog"), () =>
        HttpResponse.json({
          items: [
            { native_id: "PROJ-1", name: "Platform", kind: "project" },
            { native_id: "PROJ-2", name: "Design", kind: "project" },
          ],
        }),
      ),
    );
    let body: unknown = null;
    server.use(
      http.patch(apiUrl("/sources/1"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(src);
      }),
    );
    const user = userEvent.setup();
    renderCard(src);

    // Clicking the wrapping label toggles the container checkbox.
    await user.click(await screen.findByText("Platform"));
    await user.click(enabledSave());

    await waitFor(() => {
      expect(body).toEqual({ scope_list: ["PROJ-1"] });
    });
  });

  it("rotates the webhook secret, shows it once, and keeps the toggle off until a secret exists", async () => {
    let secretSet = false;
    server.use(
      http.get(apiUrl("/sources/1"), () =>
        HttpResponse.json(source({ webhook_secret_set: secretSet })),
      ),
      http.get(apiUrl("/sources/connectors"), () => HttpResponse.json([JIRA])),
      http.post(apiUrl("/sources/1/webhook/rotate"), () => {
        secretSet = true;
        return HttpResponse.json({ secret: "whsec_generated_once" });
      }),
    );
    const user = userEvent.setup();
    renderCard(source({}));

    // Without a secret the card asks for one first and offers Generate, not Rotate.
    expect(
      await screen.findByText("Generate a signing secret first, then switch it on."),
    ).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: /Real-time/i })).toBeInTheDocument();

    // The endpoint is shown for copying.
    expect(screen.getByDisplayValue(/webhooks\/sources\/1$/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Generate secret" }));
    await user.click(await screen.findByRole("button", { name: "Rotate" }));

    // The freshly minted secret is revealed once.
    expect(await screen.findByDisplayValue("whsec_generated_once")).toBeInTheDocument();
  });

  it("reveals the history with duration and the run mode/trigger labels", async () => {
    const src = source({});
    stubCard(src);
    server.use(
      http.get(apiUrl("/sources/1/runs"), () =>
        HttpResponse.json([
          {
            id: 1,
            mode: "full",
            trigger: "schedule",
            state: "succeeded",
            entities_done: 120,
            entities_total: 120,
            error_count: 0,
            error_detail: null,
            started_at: "2026-07-03T04:00:00Z",
            finished_at: "2026-07-03T04:00:42Z",
            created_at: "2026-07-03T04:00:00Z",
          },
        ]),
      ),
    );
    const user = userEvent.setup();
    renderCard(src);

    await user.click(await screen.findByRole("button", { name: "Show" }));
    expect(await screen.findByText("full")).toBeInTheDocument();
    expect(screen.getByText("scheduled")).toBeInTheDocument();
    expect(screen.getByText("42s")).toBeInTheDocument();
  });
});
