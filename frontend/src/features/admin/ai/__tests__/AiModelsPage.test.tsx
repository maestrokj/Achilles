import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { PROBLEM_CODES, toProblem } from "@/api/problems";
import type { CurationStatus } from "@/features/admin/knowledge/types";
import en from "@/i18n/locales/en";
import { toast } from "@/lib/toast";
import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { AiModelsPage } from "../AiModelsPage";
import type { AiModel, Assignments, EmbedderStatus, Provider } from "../types";

// ky+undici consumes the HTTPError body in node, so toProblem returns null off
// the transport — test the code→message branch by mocking the helper directly
// (see the frontend error-test quirk).
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/api/problems", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/problems")>()),
  toProblem: vi.fn(),
}));

const PROVIDERS: Provider[] = [
  {
    id: 1,
    name: "OpenAI",
    kind: "cloud",
    adapter: "openai",
    base_url: null,
    api_key_mask: "sk-…42",
    is_system: false,
    status: "active",
    last_check_at: null,
  },
];

function model(overrides: Partial<AiModel> & Pick<AiModel, "id" | "model_id">): AiModel {
  return {
    provider_id: 1,
    display_name: overrides.model_id,
    model_type: "chat",
    origin: "discovered",
    is_enabled: true,
    price_input: null,
    price_output: null,
    meta: null,
    ...overrides,
  };
}

const MODELS: AiModel[] = [
  model({ id: 11, model_id: "gpt-x", display_name: "GPT X", model_type: "chat" }),
  model({
    id: 12,
    model_id: "embed-1",
    display_name: "Embed One",
    model_type: "embedding",
    meta: { embedding_dim: 1024 },
  }),
  model({
    id: 13,
    model_id: "embed-2",
    display_name: "Embed Two",
    model_type: "embedding",
    meta: { embedding_dim: 1024 },
  }),
];

const ASSIGNMENTS: Assignments = {
  harvester_embedding: 12,
  chat_models: { items: [{ id: 11, is_enabled: true }], default: 11 },
  agent_models: { items: [], default: null },
  embedding_dim: 1024,
};

const CURATION_IDLE: CurationStatus = {
  active: null,
  reembed: null,
  last: null,
  next_scheduled: null,
};

const EMBEDDER_READY: EmbedderStatus = {
  assigned: { model_pk: 12, model_id: "embed-1", display_name: "Embed One" },
  runtime: { state: "ready", error: null },
};

/** Pin a Base UI select by the text it currently shows — the page carries
 * several comboboxes (catalogue provider, per-row type, board pickers), so an
 * index would be brittle. */
function comboboxWith(text: string): HTMLElement {
  const match = screen.getAllByRole("combobox").find((c) => c.textContent.includes(text));
  if (match === undefined) throw new Error(`no combobox showing "${text}"`);
  return match;
}

function stubPage({
  models = MODELS,
  assignments = ASSIGNMENTS,
  curation = CURATION_IDLE,
  embedder = EMBEDDER_READY,
}: {
  models?: AiModel[];
  assignments?: Assignments;
  curation?: CurationStatus;
  embedder?: EmbedderStatus;
} = {}) {
  server.use(
    http.get(apiUrl("/admin/ai/providers"), () => HttpResponse.json(PROVIDERS)),
    http.get(apiUrl("/admin/ai/models"), () => HttpResponse.json(models)),
    // The catalogue auto-discovers the selected provider on open; a specific
    // test overrides this with the models it wants surfaced.
    http.get(apiUrl("/admin/ai/providers/1/discovery"), () => HttpResponse.json({ models: [] })),
    http.get(apiUrl("/admin/ai/assignments"), () => HttpResponse.json(assignments)),
    http.get(apiUrl("/admin/ai/embedder"), () => HttpResponse.json(embedder)),
    http.get(apiUrl("/admin/knowledge/curation"), () => HttpResponse.json(curation)),
  );
}

describe("AiModelsPage", () => {
  it("adds a model ID by hand with origin manual", async () => {
    stubPage();
    let postBody: unknown = null;
    server.use(
      http.post(apiUrl("/admin/ai/models"), async ({ request }) => {
        postBody = await request.json();
        return HttpResponse.json(model({ id: 14, model_id: "ft-1" }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiModelsPage />);

    await user.click(await screen.findByRole("button", { name: "Add model ID" }));
    await user.type(await screen.findByLabelText("Model ID"), "ft-1");
    await user.type(screen.getByLabelText("Name"), "Fine Tune");
    // A per-1M input price rides the create call — output left blank → null.
    await user.type(screen.getByLabelText("Input"), "2.5");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => {
      expect(postBody).toEqual({
        provider_id: 1,
        model_id: "ft-1",
        display_name: "Fine Tune",
        model_type: "chat",
        origin: "manual",
        price_input: "2.5",
        price_output: null,
      });
    });
  });

  it("activates a discovered model with its real name, type and origin", async () => {
    stubPage();
    let postBody: unknown = null;
    server.use(
      http.get(apiUrl("/admin/ai/providers/1/discovery"), () =>
        HttpResponse.json({
          models: [{ model_id: "new-embed", display_name: "New Embed", model_type: "embedding" }],
        }),
      ),
      http.post(apiUrl("/admin/ai/models"), async ({ request }) => {
        postBody = await request.json();
        return HttpResponse.json(model({ id: 15, model_id: "new-embed" }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiModelsPage />);

    await user.click(await screen.findByRole("button", { name: "Refresh list" }));
    await user.click(await screen.findByRole("button", { name: "Activate" }));

    await waitFor(() => {
      expect(postBody).toEqual({
        provider_id: 1,
        model_id: "new-embed",
        display_name: "New Embed",
        model_type: "embedding",
        origin: "discovered",
      });
    });
  });

  it("confirms the embedding switch before PATCHing the assignment", async () => {
    stubPage();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/ai/assignments"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...ASSIGNMENTS, harvester_embedding: 13 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiModelsPage />);

    // Wait for the assignments card, then take the embedding select. The page
    // now carries several comboboxes (catalogue provider + a per-row type
    // select), so pin the embedding one by its current value rather than index.
    await screen.findByText("Function assignments");
    const embeddingSelect = comboboxWith("Embed One");
    await user.click(embeddingSelect);
    await user.click(await screen.findByRole("option", { name: /Embed Two/ }));

    // Nothing is written until the ritual is confirmed.
    expect(await screen.findByText("Switch the embedding model?")).toBeInTheDocument();
    expect(patchBody).toBeNull();

    await user.click(screen.getByRole("button", { name: "Switch & re-index" }));
    await waitFor(() => {
      expect(patchBody).toEqual({ harvester_embedding: 13 });
    });
  });

  it("points chat & agent boards to the catalog when no chat model is enabled", async () => {
    // Only an embedding model exists → both boards show their empty state and a
    // pointer to the catalog, never falling back to embedding models.
    stubPage({
      models: [
        model({
          id: 12,
          model_id: "embed-1",
          display_name: "Embed One",
          model_type: "embedding",
          meta: { embedding_dim: 1024 },
        }),
      ],
      assignments: {
        harvester_embedding: 12,
        chat_models: { items: [], default: null },
        agent_models: { items: [], default: null },
        embedding_dim: 1024,
      },
    });
    renderWithProviders(<AiModelsPage />);

    // The empty state appears for both chat-typed boards (chat + agents).
    expect(
      (await screen.findAllByText(/No enabled models of this type/)).length,
    ).toBeGreaterThanOrEqual(2);
    // The embedding function still has its model assigned through a live select.
    expect(screen.getAllByRole("combobox").length).toBeGreaterThanOrEqual(2);
  });

  it("adds a chat model to the allow-list via the picker", async () => {
    stubPage({
      models: [model({ id: 11, model_id: "gpt-x", display_name: "GPT X", model_type: "chat" })],
      assignments: {
        harvester_embedding: null,
        chat_models: { items: [], default: null },
        agent_models: { items: [], default: null },
        embedding_dim: 1024,
      },
    });
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/ai/assignments"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({
          ...ASSIGNMENTS,
          harvester_embedding: null,
          chat_models: { items: [{ id: 11, is_enabled: true }], default: 11 },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiModelsPage />);

    await screen.findByText("Function assignments");
    const addPicker = comboboxWith("Add model");
    await user.click(addPicker);
    // The option's accessible name carries the provider badge — match by prefix.
    await user.click(await screen.findByRole("option", { name: /GPT X/ }));

    await waitFor(() => {
      expect(patchBody).toEqual({
        chat_models: { items: [{ id: 11, is_enabled: true }], default: 11 },
      });
    });
  });

  it("locks the embedding select and shows progress while a re-embed runs", async () => {
    stubPage({
      curation: {
        active: {
          id: 1,
          trigger: "model_change",
          state: "running",
          started_at: "2026-07-05T10:00:00Z",
          finished_at: null,
          steps: null,
          error: null,
          created_at: "2026-07-05T10:00:00Z",
          destructive_open: false,
        },
        reembed: { done: 62, total: 100, from_model: "Embed One", to_model: "Embed Two" },
        last: null,
        next_scheduled: null,
      },
    });
    renderWithProviders(<AiModelsPage />);

    // The progress badge + the from→to endpoints carry the locked state now;
    // the select itself is disabled while the run is live.
    expect(await screen.findByText("re-indexing · 62%")).toBeInTheDocument();
    expect(screen.getByText("Embed One → Embed Two")).toBeInTheDocument();

    const embeddingSelect = comboboxWith("Embed One");
    expect(embeddingSelect).toBeDisabled();
  });

  it("shows the weights-loading phase and keeps the select usable", async () => {
    stubPage({
      embedder: {
        assigned: EMBEDDER_READY.assigned,
        runtime: { state: "loading", error: null },
      },
    });
    renderWithProviders(<AiModelsPage />);

    expect(await screen.findByText("loading weights…")).toBeInTheDocument();
    // Picking again mid-load just supersedes the load — no lock here.
    expect(comboboxWith("Embed One")).not.toBeDisabled();
  });

  it("surfaces a runtime load failure with its message", async () => {
    stubPage({
      embedder: {
        assigned: EMBEDDER_READY.assigned,
        runtime: { state: "error", error: "weights corrupted" },
      },
    });
    renderWithProviders(<AiModelsPage />);

    expect(await screen.findByText("model failed to load")).toBeInTheDocument();
    expect(screen.getByText("weights corrupted")).toBeInTheDocument();
  });

  it("shows the built-in embedder's memory footprint in the picker, cloud stays bare", async () => {
    // Only the built-in embedders carry meta.approx_size_bytes; a cloud embedder
    // has no local size, so its option renders without a size adornment.
    stubPage({
      models: [
        model({
          id: 12,
          model_id: "bge-m3",
          display_name: "BGE-M3",
          model_type: "embedding",
          origin: "builtin",
          meta: { approx_size_bytes: 2 * 1024 ** 3, embedding_dim: 1024 },
        }),
        model({
          id: 13,
          model_id: "embed-2",
          display_name: "Embed Two",
          model_type: "embedding",
          meta: { embedding_dim: 1024 },
        }),
      ],
      assignments: { ...ASSIGNMENTS, harvester_embedding: 12 },
      embedder: {
        assigned: { model_pk: 12, model_id: "bge-m3", display_name: "BGE-M3" },
        runtime: { state: "ready", error: null },
      },
    });
    const user = userEvent.setup();
    renderWithProviders(<AiModelsPage />);

    await screen.findByText("Function assignments");
    await user.click(comboboxWith("BGE-M3"));
    // The seeded footprint rides the built-in row; the cloud row shows no size.
    expect(await screen.findByText("~2.00 GB")).toBeInTheDocument();
    const cloudOption = await screen.findByRole("option", { name: /Embed Two/ });
    expect(cloudOption.textContent).not.toMatch(/GB|MB|kB/);
  });

  it("disables an embedder whose dimension the knowledge base can't hold", async () => {
    // The column is provisioned to 1024; a 1536-dim model stays listed but is
    // disabled with the reason inline — the 409 never has to fire.
    stubPage({
      models: [
        model({
          id: 12,
          model_id: "embed-1",
          display_name: "Embed One",
          model_type: "embedding",
          meta: { embedding_dim: 1024 },
        }),
        model({
          id: 14,
          model_id: "big-embed",
          display_name: "Big Embed",
          model_type: "embedding",
          meta: { embedding_dim: 1536 },
        }),
      ],
    });
    const user = userEvent.setup();
    renderWithProviders(<AiModelsPage />);

    await screen.findByText("Function assignments");
    await user.click(comboboxWith("Embed One"));
    const incompatible = await screen.findByRole("option", { name: /Big Embed/ });
    expect(incompatible).toHaveAttribute("data-disabled");
    expect(incompatible.textContent).toContain("needs 1024");
  });

  it("explains a too-large model via the MODEL_TOO_LARGE toast", async () => {
    const user = userEvent.setup();
    stubPage();
    server.use(
      http.patch(apiUrl("/admin/ai/assignments"), () =>
        HttpResponse.json({ code: PROBLEM_CODES.MODEL_TOO_LARGE }, { status: 409 }),
      ),
    );
    vi.mocked(toProblem).mockResolvedValue({
      type: "/errors/model-too-large",
      title: "Model too large",
      status: 409,
      detail: "",
      code: PROBLEM_CODES.MODEL_TOO_LARGE,
      request_id: "req-1",
    });
    renderWithProviders(<AiModelsPage />);

    await screen.findByText("Function assignments");
    await user.click(comboboxWith("Embed One"));
    await user.click(await screen.findByRole("option", { name: /Embed Two/ }));
    await user.click(await screen.findByRole("button", { name: "Switch & re-index" }));

    await waitFor(() => {
      expect(vi.mocked(toast.error)).toHaveBeenCalled();
    });
    const [, options] = vi.mocked(toast.error).mock.calls.at(-1) ?? [];
    expect(options).toMatchObject({ description: en.errors.codes.MODEL_TOO_LARGE });
  });
});
