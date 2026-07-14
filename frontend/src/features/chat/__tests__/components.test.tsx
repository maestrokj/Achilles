/** The chat leaf components at the seams the adapter doesn't reach: the demand
 * signal on a source click, the optimistic feedback vote, the honesty plaque,
 * and the model picker's default/selection. api.ts is mocked so each test asserts
 * the exact outbound call (or its absence), never the network. */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderAs } from "@/test/session";

import { Feedback } from "../Feedback";
import { GroundingState } from "../GroundingState";
import { MessageSources } from "../MessageSources";
import { ModelPicker } from "../ModelPicker";
import type { Citation } from "../types";

const postAccess = vi.fn<(c: number, e: number) => Promise<void>>();
const setFeedback = vi.fn<(m: number, v: unknown) => Promise<void>>();
const getChatModels = vi.fn<() => Promise<unknown>>();

vi.mock("../api", () => ({
  chatQueryKeys: { models: ["chat", "models"] },
  postAccess: (c: number, e: number) => postAccess(c, e),
  setFeedback: (m: number, v: unknown) => setFeedback(m, v),
  getChatModels: () => getChatModels(),
}));

const CITATION: Citation = {
  marker: 1,
  entity_id: 7,
  chunk_id: null,
  title: "Deploy checklist",
  url: "https://wiki.example/deploy",
  source_type: "page",
  snippet: "Deploy runs through the checklist.",
};

beforeEach(() => {
  postAccess.mockReset().mockResolvedValue(undefined);
  setFeedback.mockReset().mockResolvedValue(undefined);
  getChatModels.mockReset();
});

describe("MessageSources — the demand signal", () => {
  it("fires postAccess with the conversation and entity on a source click", async () => {
    const user = userEvent.setup();
    renderAs("member", <MessageSources citations={[CITATION]} conversationId={42} />);

    await user.click(screen.getByRole("link", { name: /Deploy checklist/ }));

    expect(postAccess).toHaveBeenCalledExactlyOnceWith(42, 7);
  });

  it("stays silent when the conversation id is not yet known", async () => {
    const user = userEvent.setup();
    renderAs("member", <MessageSources citations={[CITATION]} conversationId={null} />);

    await user.click(screen.getByRole("link", { name: /Deploy checklist/ }));

    expect(postAccess).not.toHaveBeenCalled();
  });

  it("renders a link-less citation as inert text, not an anchor", () => {
    renderAs(
      "member",
      <MessageSources citations={[{ ...CITATION, url: null }]} conversationId={42} />,
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("Deploy checklist")).toBeInTheDocument();
  });
});

describe("Feedback — optimistic vote", () => {
  it("flips the vote optimistically and persists it", async () => {
    const user = userEvent.setup();
    renderAs("member", <Feedback messageId={99} initial={null} />);

    const up = screen.getByRole("button", { name: /Helpful/ });
    await user.click(up);

    expect(up).toHaveAttribute("aria-pressed", "true");
    expect(setFeedback).toHaveBeenCalledExactlyOnceWith(99, 1);
  });

  it("rolls the flip back when the PATCH fails", async () => {
    setFeedback.mockRejectedValueOnce(new Error("boom"));
    const user = userEvent.setup();
    renderAs("member", <Feedback messageId={99} initial={null} />);

    const up = screen.getByRole("button", { name: /Helpful/ });
    await user.click(up);

    await waitFor(() => {
      expect(up).toHaveAttribute("aria-pressed", "false");
    });
  });

  it("a second click on the active vote clears it", async () => {
    const user = userEvent.setup();
    renderAs("member", <Feedback messageId={99} initial={1} />);

    const up = screen.getByRole("button", { name: /Helpful/ });
    expect(up).toHaveAttribute("aria-pressed", "true");
    await user.click(up);

    expect(up).toHaveAttribute("aria-pressed", "false");
    expect(setFeedback).toHaveBeenCalledExactlyOnceWith(99, null);
  });
});

describe("GroundingState — the honesty plaque", () => {
  it("warns when a grounded answer cites nothing", () => {
    renderAs(
      "member",
      <GroundingState
        grounding={{
          mode: "grounded",
          outcome: "empty",
          hidden_source_type: null,
          hidden_author_email: null,
        }}
      />,
    );
    expect(screen.getByText(/unverified/i)).toBeInTheDocument();
  });

  it("names the hidden source coordinates on an acl_hidden answer", () => {
    renderAs(
      "member",
      <GroundingState
        grounding={{
          mode: "grounded",
          outcome: "acl_hidden",
          hidden_source_type: "page",
          hidden_author_email: "boss@acme.example",
        }}
      />,
    );
    expect(screen.getByText(/boss@acme\.example/)).toBeInTheDocument();
  });

  it("stays silent for a purely conversational turn", () => {
    const { container } = renderAs(
      "member",
      <GroundingState
        grounding={{
          mode: "conversational",
          outcome: null,
          hidden_source_type: null,
          hidden_author_email: null,
        }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ModelPicker", () => {
  const MODELS = {
    items: [
      { model_id: "a", display_name: "Model A", is_default: true },
      { model_id: "b", display_name: "Model B", is_default: false },
    ],
    selected: null,
  };

  it("falls back to the default model for display when nothing is picked", async () => {
    getChatModels.mockResolvedValue(MODELS);
    renderAs("member", <ModelPicker selected={null} onSelect={vi.fn()} />);

    await screen.findByText("Model A");
  });

  it("shows the user's personal default ahead of the admin default", async () => {
    getChatModels.mockResolvedValue({ ...MODELS, selected: "b" });
    renderAs("member", <ModelPicker selected={null} onSelect={vi.fn()} />);

    await screen.findByText("Model B");
  });

  it("an explicit pick still wins over the personal default", async () => {
    getChatModels.mockResolvedValue({ ...MODELS, selected: "b" });
    renderAs("member", <ModelPicker selected="a" onSelect={vi.fn()} />);

    await screen.findByText("Model A");
  });

  it("reports the user's pick through onSelect", async () => {
    getChatModels.mockResolvedValue(MODELS);
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderAs("member", <ModelPicker selected={null} onSelect={onSelect} />);

    await user.click(await screen.findByRole("button", { name: /Model/ }));
    await user.click(await screen.findByRole("menuitemradio", { name: "Model B" }));

    expect(onSelect).toHaveBeenCalledWith("b");
  });
});
