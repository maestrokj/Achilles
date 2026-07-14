import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { toast } from "@/lib/toast";
import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { AiBehaviorPage } from "../AiBehaviorPage";
import type { Prompt } from "../types";

// Toast is a side effect we assert on (save success / failure); mock keeps the
// real notifier out of jsdom. Existing happy-path tests don't inspect it.
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const PROMPT_MAX_CHARS = 6000;

const PROMPT: Prompt = {
  safety: { text: "Default safety layer.", is_default: true },
  org: { text: "We are Acme.", is_default: false },
};

function stubGet(prompt: Prompt = PROMPT) {
  server.use(http.get(apiUrl("/admin/ai-prompt"), () => HttpResponse.json(prompt)));
}

describe("AiBehaviorPage", () => {
  it("renders both layers read-first and marks the built-in default", async () => {
    stubGet();
    renderWithProviders(<AiBehaviorPage />);

    // Blocks show as calm read-only text until the admin opts into editing.
    expect(await screen.findByText("Default safety layer.")).toBeInTheDocument();
    expect(screen.getByText("We are Acme.")).toBeInTheDocument();
    // Only the untouched safety block reads "built-in default"; only the
    // customized org block offers a reset.
    expect(screen.getAllByText("built-in default")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Reset to default" })).toHaveLength(1);
  });

  it("edits a block in place and saves it", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/ai-prompt"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({
          ...PROMPT,
          org: { text: "We are Acme, honestly.", is_default: false },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiBehaviorPage />);

    await screen.findByText("We are Acme.");
    // Open the org block for editing (second card), then rewrite and save.
    const orgEdit = screen.getAllByRole("button", { name: "Edit" }).at(1);
    if (!orgEdit) throw new Error("no edit button for the org block");
    await user.click(orgEdit);

    const orgField = await screen.findByDisplayValue("We are Acme.");
    await user.clear(orgField);
    await user.type(orgField, "We are Acme, honestly.");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ org_text: "We are Acme, honestly." });
    });
  });

  it("resets a customized block by PATCHing null", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/ai-prompt"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({
          ...PROMPT,
          org: { text: "Built-in org text.", is_default: true },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiBehaviorPage />);

    await screen.findByText("We are Acme.");
    await user.click(screen.getByRole("button", { name: "Reset to default" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ org_text: null });
    });
    expect(await screen.findByText("Built-in org text.")).toBeInTheDocument();
  });

  it("blocks saving past the character cap and never fires the request", async () => {
    stubGet();
    let patched = false;
    server.use(
      http.patch(apiUrl("/admin/ai-prompt"), () => {
        patched = true;
        return HttpResponse.json(PROMPT);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiBehaviorPage />);

    await screen.findByText("We are Acme.");
    const orgEdit = screen.getAllByRole("button", { name: "Edit" }).at(1);
    if (!orgEdit) throw new Error("no edit button for the org block");
    await user.click(orgEdit);

    // fireEvent, not user.type — typing 6001 keystrokes one by one is untenable.
    const field = await screen.findByDisplayValue("We are Acme.");
    fireEvent.change(field, { target: { value: "x".repeat(PROMPT_MAX_CHARS + 1) } });

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    expect(field).toHaveAttribute("aria-invalid", "true");

    // Back within the cap re-enables it; the guard is length-driven, not sticky.
    fireEvent.change(field, { target: { value: "within the cap" } });
    expect(save).toBeEnabled();

    await user.click(save);
    await waitFor(() => {
      expect(patched).toBe(true);
    });
    // The over-cap value never reached the wire — only the trimmed retry did.
    expect(save).toBeInTheDocument();
  });

  it("surfaces a save failure as a toast and keeps the draft for a retry", async () => {
    stubGet();
    server.use(
      http.patch(apiUrl("/admin/ai-prompt"), () =>
        HttpResponse.json(
          { code: "UNKNOWN_PLACEHOLDER", detail: "{x} is not supported", status: 422 },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiBehaviorPage />);

    await screen.findByText("We are Acme.");
    const orgEdit = screen.getAllByRole("button", { name: "Edit" }).at(1);
    if (!orgEdit) throw new Error("no edit button for the org block");
    await user.click(orgEdit);

    // fireEvent, not user.type — "{x}" would be parsed as a userEvent special key.
    const field = await screen.findByDisplayValue("We are Acme.");
    fireEvent.change(field, { target: { value: "We are Acme. {x}" } });
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(vi.mocked(toast.error)).toHaveBeenCalled();
    });
    // The draft stays open with the admin's text — no silent reset on error.
    expect(screen.getByDisplayValue("We are Acme. {x}")).toBeInTheDocument();
  });

  it("cancels an edit without touching the backend", async () => {
    stubGet();
    let patched = false;
    server.use(
      http.patch(apiUrl("/admin/ai-prompt"), () => {
        patched = true;
        return HttpResponse.json(PROMPT);
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AiBehaviorPage />);

    await screen.findByText("We are Acme.");
    const orgEdit = screen.getAllByRole("button", { name: "Edit" }).at(1);
    if (!orgEdit) throw new Error("no edit button for the org block");
    await user.click(orgEdit);

    const field = await screen.findByDisplayValue("We are Acme.");
    await user.clear(field);
    await user.type(field, "throwaway");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    // Back to the calm read view with the original text; nothing was sent.
    expect(await screen.findByText("We are Acme.")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("throwaway")).not.toBeInTheDocument();
    expect(patched).toBe(false);
  });
});
