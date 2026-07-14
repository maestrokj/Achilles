import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { EmbedderStatus } from "@/features/admin/ai/types";
import type { CurationStatus } from "@/features/admin/knowledge/types";
import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { HarvesterPage } from "../HarvesterPage";
import type { Source } from "../types";

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
    webhook_endpoint_url: null,
    created_at: "2026-06-01T10:00:00Z",
    ...overrides,
  };
}

const PLATFORM_STUB = {
  sync_interval_minutes: 360,
  reconcile_minute_of_week: 8820,
};

const CURATION_IDLE: CurationStatus = {
  active: null,
  reembed: null,
  last: null,
  next_scheduled: null,
};

const EMBEDDER_NONE: EmbedderStatus = { assigned: null, runtime: null };

function stubLists(sources: Source[], embedder: EmbedderStatus = EMBEDDER_NONE) {
  server.use(
    http.get(apiUrl("/sources"), () => HttpResponse.json(sources)),
    http.get(apiUrl("/admin/settings"), () => HttpResponse.json(PLATFORM_STUB)),
    http.get(apiUrl("/admin/ai/embedder"), () => HttpResponse.json(embedder)),
    http.get(apiUrl("/admin/knowledge/curation"), () => HttpResponse.json(CURATION_IDLE)),
  );
}

describe("HarvesterPage", () => {
  it("renders the sources with state, health and the DLQ pill", async () => {
    stubLists([
      source({ id: 1, name: "Team Jira", health: "idle", entity_count: 48100 }),
      source({
        id: 2,
        name: "Docs Wiki",
        connector_type: "confluence",
        state: "paused",
        health: "error",
        dlq_count: 3,
      }),
    ]);
    renderWithProviders(<HarvesterPage />);

    expect(await screen.findByText("Team Jira")).toBeInTheDocument();
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByText("error")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "3 failed" })).toBeInTheDocument();
    // The graph-contribution column formats compactly (48.1k).
    expect(screen.getByText("48.1K")).toBeInTheDocument();
  });

  it("shows the progressive-value empty state when nothing is connected", async () => {
    stubLists([]);
    renderWithProviders(<HarvesterPage />);

    expect(await screen.findByText("No sources connected yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync all" })).toBeDisabled();
  });

  it("confirms before a fan-out sync", async () => {
    stubLists([source({ id: 1 })]);
    let posted = false;
    server.use(
      http.post(apiUrl("/sources/sync"), () => {
        posted = true;
        return HttpResponse.json({ run_ids: [11] }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<HarvesterPage />);

    await user.click(await screen.findByRole("button", { name: "Sync all" }));
    // The trigger opens a guard — nothing fires until it is confirmed.
    expect(posted).toBe(false);
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Sync all" }));
    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });

  it("sorts the table client-side when a column header is clicked", async () => {
    stubLists([
      source({ id: 1, name: "Alpha", last_sync_at: "2026-07-01T10:00:00Z" }),
      source({ id: 2, name: "Bravo", last_sync_at: "2026-07-03T10:00:00Z" }),
    ]);
    const user = userEvent.setup();
    renderWithProviders(<HarvesterPage />);

    await screen.findByText("Alpha");
    // Default order: freshest sync first — Bravo (Jul 3) over Alpha (Jul 1).
    const dataRows = () => screen.getAllByRole("row").slice(1);
    expect(dataRows()[0]).toHaveTextContent("Bravo");

    // Sort by the Source column: name ascending.
    await user.click(screen.getByRole("button", { name: "Source" }));
    expect(dataRows()[0]).toHaveTextContent("Alpha");

    // A second click on the same header flips to descending.
    await user.click(screen.getByRole("button", { name: "Source" }));
    expect(dataRows()[0]).toHaveTextContent("Bravo");
  });

  it("shows in-flight progress and the queued health badge in the run cell", async () => {
    stubLists([
      source({
        id: 1,
        name: "Running Src",
        health: "syncing",
        last_run: {
          state: "running",
          mode: "incremental",
          duration_seconds: null,
          error: null,
          progress_done: 120,
          progress_total: 480,
        },
      }),
      source({ id: 2, name: "Queued Src", health: "queued", last_run: null }),
    ]);
    renderWithProviders(<HarvesterPage />);

    expect(await screen.findByText("120 of 480")).toBeInTheDocument();
    expect(screen.getByText("syncing")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("saves an edited reconciliation time as a minute-of-week PATCH on Save", async () => {
    stubLists([source({ id: 1 })]);
    let patched: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/settings"), async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json({ ...PLATFORM_STUB, reconcile_minute_of_week: 8910 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<HarvesterPage />);

    // Seed is Sun 03:00 (8820 = day 6 · 1440 + 180). Editing to 04:30 → 8910.
    const time = await screen.findByLabelText("Full sync time");
    await user.clear(time);
    await user.type(time, "04:30");
    // Draft only — nothing is written until Save is pressed.
    expect(patched).toBeNull();

    // Two always-on Save buttons (interval + reconcile); the reconcile one is second.
    const [, reconcileSave] = screen.getAllByRole("button", { name: "Save" });
    await user.click(reconcileSave);

    await waitFor(() => {
      expect(patched).toEqual({ reconcile_minute_of_week: 8910 });
    });
  });

  it("warns via the embedder pill when no model is assigned", async () => {
    stubLists([]);
    renderWithProviders(<HarvesterPage />);

    const pill = await screen.findByRole("link", { name: /not assigned/ });
    expect(pill).toHaveAttribute("href", "/admin/ai-models#assignments");
  });

  it("shows the weights-loading phase on the embedder pill", async () => {
    stubLists([], {
      assigned: { model_pk: 12, model_id: "embed-1", display_name: "Embed One" },
      runtime: { state: "loading", error: null },
    });
    renderWithProviders(<HarvesterPage />);

    expect(await screen.findByText("loading weights…")).toBeInTheDocument();
  });

  it("runs a single source through the row menu after confirmation", async () => {
    stubLists([source({ id: 7, name: "Team Jira", state: "active", health: "idle" })]);
    let body: unknown = null;
    server.use(
      http.post(apiUrl("/sources/7/sync"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ run_id: 42 }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<HarvesterPage />);

    await user.click(await screen.findByRole("button", { name: "Source actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sync" }));
    // The menu action opens a guard — the run starts only once confirmed, as
    // incremental (the row's manual mode).
    const dialog = await screen.findByRole("alertdialog");
    expect(body).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Sync" }));
    await waitFor(() => {
      expect(body).toEqual({ mode: "incremental" });
    });
  });

  it("pauses an active source straight from the row menu", async () => {
    stubLists([source({ id: 8, name: "Docs Wiki", state: "active", health: "idle" })]);
    let body: unknown = null;
    server.use(
      http.patch(apiUrl("/sources/8"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(source({ id: 8, state: "paused" }));
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<HarvesterPage />);

    await user.click(await screen.findByRole("button", { name: "Source actions" }));
    // Pause fires immediately (no guard) — one PATCH flipping the state.
    await user.click(await screen.findByRole("menuitem", { name: "Pause" }));
    await waitFor(() => {
      expect(body).toEqual({ state: "paused" });
    });
  });

  it("cancels the active run from the row menu while syncing", async () => {
    stubLists([
      source({
        id: 9,
        name: "Running Src",
        state: "active",
        health: "syncing",
        last_run: {
          state: "running",
          mode: "incremental",
          duration_seconds: null,
          error: null,
          progress_done: 1,
          progress_total: 10,
        },
      }),
    ]);
    let cancelled = false;
    server.use(
      http.post(apiUrl("/sources/9/cancel"), () => {
        cancelled = true;
        return HttpResponse.json({ run_id: 9 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<HarvesterPage />);

    await user.click(await screen.findByRole("button", { name: "Source actions" }));
    // A syncing row offers Cancel — not Sync/Pause, which belong to a still row.
    expect(await screen.findByRole("menuitem", { name: "Cancel run" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Pause" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Cancel run" }));
    await waitFor(() => {
      expect(cancelled).toBe(true);
    });
  });

  it("retries the dead-letter queue from the DLQ popover", async () => {
    stubLists([source({ id: 10, name: "Docs Wiki", dlq_count: 2 })]);
    let retried = false;
    server.use(
      http.get(apiUrl("/sources/10/dead-letters"), () =>
        HttpResponse.json([
          {
            id: 1,
            source_type: "page",
            source_entity_id: "p1",
            reason: "permission",
            error_detail: null,
            attempts: 3,
            updated_at: "2026-07-01T10:00:00Z",
          },
          {
            id: 2,
            source_type: "page",
            source_entity_id: "p2",
            reason: "permission",
            error_detail: null,
            attempts: 2,
            updated_at: "2026-07-01T10:00:00Z",
          },
        ]),
      ),
      http.post(apiUrl("/sources/10/dead-letters/retry"), () => {
        retried = true;
        return HttpResponse.json({ run_id: 5 }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<HarvesterPage />);

    await user.click(await screen.findByRole("button", { name: "2 failed" }));
    // The popover lazy-loads the letters and rolls them up by reason.
    expect(await screen.findByText("permission")).toBeInTheDocument();
    expect(screen.getByText(/× 2/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry failed items" }));
    await waitFor(() => {
      expect(retried).toBe(true);
    });
  });
});
