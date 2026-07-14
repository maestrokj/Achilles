import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import type { EmbedderStatus } from "@/features/admin/ai/types";
import { SessionContext } from "@/features/auth/session-context";
import type { SessionUser } from "@/features/auth/types";
import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { KnowledgeStorePage } from "../KnowledgeStorePage";
import type { BackupSnapshot, CurationStatus } from "../types";

const METRICS = {
  entities: 48_100,
  chunks: 131_000,
  edges: 12_400,
  pending_refs: 240,
  vector_bytes: 2_684_354_560, // 2.5 GB
};

const IDLE: CurationStatus = {
  active: null,
  reembed: null,
  last: {
    id: 7,
    trigger: "schedule",
    state: "succeeded",
    started_at: "2026-07-03T04:00:00Z",
    finished_at: "2026-07-03T04:12:00Z",
    steps: { refs_materialized: 120 },
    error: null,
    created_at: "2026-07-03T04:00:00Z",
    destructive_open: false,
  },
  next_scheduled: "2026-07-05T04:00:00Z",
};

const SNAPSHOT: BackupSnapshot = {
  id: 3,
  state: "succeeded",
  started_at: "2026-07-04T02:00:00Z",
  finished_at: "2026-07-04T02:05:00Z",
  size_bytes: 1_073_741_824,
  error: null,
};

const PLATFORM_STUB = {
  curation_frequency: "daily",
  curation_weekday: null,
  curation_time: "04:00",
};

const EMBEDDER_READY: EmbedderStatus = {
  assigned: { model_pk: 12, model_id: "embed-1", display_name: "Embed One" },
  runtime: { state: "ready", error: null },
};

function stubAll(curation: CurationStatus = IDLE, embedder: EmbedderStatus = EMBEDDER_READY) {
  server.use(
    http.get(apiUrl("/sources"), () => HttpResponse.json([])),
    http.get(apiUrl("/admin/settings"), () => HttpResponse.json(PLATFORM_STUB)),
    http.get(apiUrl("/admin/ai/embedder"), () => HttpResponse.json(embedder)),
    http.get(apiUrl("/admin/knowledge/metrics"), () => HttpResponse.json(METRICS)),
    http.get(apiUrl("/admin/knowledge/curation"), () => HttpResponse.json(curation)),
    http.get(apiUrl("/admin/knowledge/backup-settings"), () =>
      HttpResponse.json({
        destination_url: "s3://acme/prod",
        credential_is_set: true,
        frequency: "daily",
        weekday: null,
        time: "02:00",
        retention_count: 14,
      }),
    ),
    http.get(apiUrl("/admin/knowledge/backups"), () => HttpResponse.json([SNAPSHOT])),
  );
}

function ownerSession(): SessionUser {
  return {
    id: 1,
    email: "boss@acme.example",
    full_name: "Boss",
    role: "owner",
    status: "active",
    must_change_password: false,
    timezone: null,
    locale: null,
    date_format: null,
    last_login_at: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function renderPage(ui: ReactElement = <KnowledgeStorePage />, user: SessionUser = ownerSession()) {
  return renderWithProviders(
    <SessionContext.Provider value={{ status: "authenticated", user, expired: false }}>
      {ui}
    </SessionContext.Provider>,
  );
}

describe("KnowledgeStorePage", () => {
  it("renders the storage tiles, schedule and snapshots", async () => {
    stubAll();
    renderPage();

    expect(await screen.findByText("48,100")).toBeInTheDocument();
    expect(screen.getByText("2.50 GB")).toBeInTheDocument(); // vector volume
    expect(screen.getByText("1.00 GB")).toBeInTheDocument(); // snapshot size
    // Idle maintenance shows a plain-language headline; the snapshot keeps its chip.
    expect(screen.getByText("Everything's in order")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore" })).toBeInTheDocument();
  });

  it("starts a grooming run through a confirmation dialog", async () => {
    stubAll();
    let posted = false;
    server.use(
      http.post(apiUrl("/admin/knowledge/reindex"), () => {
        posted = true;
        return HttpResponse.json({ run_id: 8 }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Run" }));
    expect(posted, "no POST before confirming").toBe(false);
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });

  it("locks the run button and shows progress while a re-embed is active", async () => {
    stubAll({
      active: {
        id: 9,
        trigger: "model_change",
        state: "running",
        started_at: "2026-07-04T10:00:00Z",
        finished_at: null,
        steps: null,
        error: null,
        created_at: "2026-07-04T10:00:00Z",
        destructive_open: false,
      },
      reembed: { done: 8200, total: 13_100 },
      last: null,
      next_scheduled: "2026-07-05T04:00:00Z",
    });
    renderPage();

    expect(await screen.findByText("8,200 of 13,100 chunks re-indexed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeInTheDocument();
  });

  it("links the embedder pill to the AI models screen", async () => {
    stubAll();
    renderPage();

    const pill = await screen.findByRole("link", { name: /Embed One/ });
    expect(pill).toHaveAttribute("href", "/admin/ai-models#assignments");
  });

  it("shows weights loading instead of a frozen progress bar", async () => {
    stubAll(
      {
        active: {
          id: 9,
          trigger: "model_change",
          state: "running",
          started_at: "2026-07-04T10:00:00Z",
          finished_at: null,
          steps: null,
          error: null,
          created_at: "2026-07-04T10:00:00Z",
          destructive_open: false,
        },
        reembed: { done: 0, total: 13_100 },
        last: null,
        next_scheduled: "2026-07-05T04:00:00Z",
      },
      {
        assigned: EMBEDDER_READY.assigned,
        runtime: { state: "loading", error: null },
      },
    );
    renderPage();

    // The pill and the run panel both speak the loading phase; the 0% bar is gone.
    expect(await screen.findAllByText("loading weights…")).not.toHaveLength(0);
    expect(screen.queryByText("0 of 13,100 chunks re-indexed")).not.toBeInTheDocument();
  });

  it("saves the curation schedule through PATCH /admin/settings", async () => {
    stubAll();
    let body: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/settings"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...PLATFORM_STUB, curation_time: "06:45" });
      }),
    );
    const user = userEvent.setup();
    const { container } = renderPage();

    await screen.findByText("Backups to keep"); // both cadence blocks mounted
    // Two "Time" fields share the screen; the unique id disambiguates the
    // curation one from the backup one (regression: they shared #cadence-time).
    const time = container.querySelector<HTMLInputElement>("#curation-cadence-time");
    if (!time) throw new Error("no curation time field");
    await user.clear(time);
    await user.type(time, "06:45");
    const save = screen
      .getAllByRole("button", { name: "Save" })
      .find((b) => !b.hasAttribute("disabled"));
    if (!save) throw new Error("no enabled Save button");
    await user.click(save);

    await waitFor(() => {
      expect(body).toEqual({
        curation_frequency: "daily",
        curation_weekday: null,
        curation_time: "06:45",
      });
    });
  });

  it("saves a weekly cadence with the picked weekday through PATCH /admin/settings", async () => {
    // The daily save leaves weekday null; this drives the weekly branch — the
    // day Select must appear and its value must reach the body (not null).
    stubAll();
    let body: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/settings"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({
          ...PLATFORM_STUB,
          curation_frequency: "weekly",
          curation_weekday: 3,
        });
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Backups to keep"); // both cadence blocks mounted
    // Curation renders before the backup card, so the first "Daily" combobox is
    // the curation frequency (the source picker reads "All sources").
    const frequency = screen.getAllByRole("combobox").find((c) => c.textContent.includes("Daily"));
    if (!frequency) throw new Error("no curation frequency select");
    await user.click(frequency);
    await user.click(await screen.findByRole("option", { name: "Weekly" }));

    // The weekday Select is now the only one showing a day name.
    const weekday = screen.getAllByRole("combobox").find((c) => c.textContent.includes("Monday"));
    if (!weekday) throw new Error("no weekday select");
    await user.click(weekday);
    await user.click(await screen.findByRole("option", { name: "Thursday" }));

    const save = screen
      .getAllByRole("button", { name: "Save" })
      .find((b) => !b.hasAttribute("disabled"));
    if (!save) throw new Error("no enabled Save button");
    await user.click(save);

    await waitFor(() => {
      expect(body).toEqual({
        curation_frequency: "weekly",
        curation_weekday: 3,
        curation_time: "04:00",
      });
    });
  });

  it("saves the backup window through PATCH /admin/knowledge/backup-settings", async () => {
    stubAll();
    let body: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/knowledge/backup-settings"), async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({
          destination_url: "s3://acme/prod",
          credential_is_set: true,
          frequency: "daily",
          weekday: null,
          time: "02:00",
          retention_count: 21,
        });
      }),
    );
    const user = userEvent.setup();
    renderPage();

    const retention = await screen.findByLabelText("Backups to keep");
    await user.clear(retention);
    await user.type(retention, "21");
    const save = screen
      .getAllByRole("button", { name: "Save" })
      .find((b) => !b.hasAttribute("disabled"));
    if (!save) throw new Error("no enabled Save button");
    await user.click(save);

    await waitFor(() => {
      expect(body).toEqual({
        frequency: "daily",
        weekday: null,
        time: "02:00",
        retention_count: 21,
      });
    });
  });

  it("surfaces the error of a failed grooming run", async () => {
    // A model-change re-embed that the runtime never became ready for finishes
    // `failed` with the runtime's own diagnosis (jobs.py run_reembed) — the idle
    // recap must show that message, not a hollow "in order".
    stubAll({
      active: null,
      reembed: null,
      last: {
        id: 8,
        trigger: "model_change",
        state: "failed",
        started_at: "2026-07-04T10:00:00Z",
        finished_at: "2026-07-04T10:03:00Z",
        steps: null,
        error: "embeddings runtime failed to load BAAI/bge-m3: out of memory",
        created_at: "2026-07-04T10:00:00Z",
        destructive_open: false,
      },
      next_scheduled: "2026-07-05T04:00:00Z",
    });
    renderPage();

    expect(await screen.findByText("The last run ended with an error")).toBeInTheDocument();
    expect(
      screen.getByText("embeddings runtime failed to load BAAI/bge-m3: out of memory"),
    ).toBeInTheDocument();
  });

  it("keeps backup settings read-only for a non-owner admin", async () => {
    stubAll();
    const admin: SessionUser = { ...ownerSession(), role: "admin" };
    renderPage(<KnowledgeStorePage />, admin);

    // The destination is where the company's data lands — Owner-write only; an
    // admin reads it but cannot re-point it, and no Save button is offered.
    const destination = await screen.findByLabelText("Location");
    expect(destination).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("guards restore behind type-to-confirm", async () => {
    stubAll();
    let posted = false;
    server.use(
      http.post(apiUrl("/admin/knowledge/restore"), () => {
        posted = true;
        return HttpResponse.json({ snapshot_id: 3 }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Restore" }));
    const confirmButton = screen.getAllByRole("button", { name: "Restore" }).at(-1);
    if (!confirmButton) throw new Error("no confirm button");
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByLabelText('Type "restore" to confirm'), "restore");
    expect(posted, "no POST before the phrase is typed").toBe(false);
    await user.click(confirmButton);
    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });

  it("summarizes the last grooming run's step counts and duration", async () => {
    stubAll({
      active: null,
      reembed: null,
      last: {
        id: 7,
        trigger: "schedule",
        state: "succeeded",
        started_at: "2026-07-03T04:00:00Z",
        finished_at: "2026-07-03T04:02:00Z",
        steps: { refs_materialized: 1200, duplicates_merged: 84, entities_rescored: 320 },
        error: null,
        created_at: "2026-07-03T04:00:00Z",
        destructive_open: false,
      },
      next_scheduled: "2026-07-05T04:00:00Z",
    });
    renderPage();

    expect(
      await screen.findByText("1,200 links added · 84 duplicates merged · 320 entities re-ranked"),
    ).toBeInTheDocument();
    // Finish time + duration ride together in the status meta line.
    expect(screen.getByText(/· 2m$/)).toBeInTheDocument();
  });

  it("names the from → to models during a re-embed", async () => {
    stubAll({
      active: {
        id: 9,
        trigger: "model_change",
        state: "running",
        started_at: "2026-07-04T10:00:00Z",
        finished_at: null,
        steps: null,
        error: null,
        created_at: "2026-07-04T10:00:00Z",
        destructive_open: false,
      },
      reembed: { done: 8200, total: 13_100, from_model: "BGE-M3", to_model: "Qwen3-0.6B" },
      last: null,
      next_scheduled: "2026-07-05T04:00:00Z",
    });
    renderPage();

    expect(await screen.findByText("BGE-M3 → Qwen3-0.6B")).toBeInTheDocument();
  });
});
