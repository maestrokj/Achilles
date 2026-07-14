import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { RunJournal } from "../RunJournal";
import type { AgentRun, Page } from "../types";

function run(overrides: Partial<AgentRun>): AgentRun {
  return {
    id: 1,
    trigger: "manual",
    state: "succeeded",
    reason: null,
    output: null,
    tokens_used: 1200,
    error: null,
    started_at: "2026-07-04T06:00:00Z",
    finished_at: "2026-07-04T06:00:20Z",
    duration_seconds: 20,
    created_at: "2026-07-04T06:00:00Z",
    ...overrides,
  };
}

function page(items: AgentRun[], nextCursor: string | null = null): Page<AgentRun> {
  return { items, next_cursor: nextCursor };
}

describe("RunJournal", () => {
  it("shows the empty state when there are no runs", async () => {
    const fetchPage = vi.fn().mockResolvedValue(page([]));
    renderWithProviders(<RunJournal queryKey={["runs", "empty"]} fetchPage={fetchPage} />);
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
  });

  it("reveals a run's output on expand and its error for a failed run", async () => {
    const fetchPage = vi.fn().mockResolvedValue(
      page([
        run({ id: 2, state: "succeeded", output: "Weekly summary ready." }),
        run({
          id: 3,
          state: "failed",
          reason: "error",
          error: "provider timed out",
          output: null,
        }),
      ]),
    );
    const user = userEvent.setup();
    renderWithProviders(<RunJournal queryKey={["runs", "two"]} fetchPage={fetchPage} />);

    await screen.findByText("Completed");
    expect(screen.getByText("Failed")).toBeInTheDocument();
    // Collapsed: neither the output nor the error is shown yet.
    expect(screen.queryByText("Weekly summary ready.")).not.toBeInTheDocument();

    await user.click(screen.getByText("Completed"));
    expect(await screen.findByText("Weekly summary ready.")).toBeInTheDocument();

    await user.click(screen.getByText("Failed"));
    expect(await screen.findByText("provider timed out")).toBeInTheDocument();
  });

  it("pages the journal through the cursor on Show more", async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce(page([run({ id: 2, state: "succeeded" })], "cursor-2"))
      .mockResolvedValueOnce(page([run({ id: 1, state: "failed", error: "boom" })], null));
    const user = userEvent.setup();
    renderWithProviders(<RunJournal queryKey={["runs", "paged"]} fetchPage={fetchPage} />);

    await screen.findByText("Completed");
    await user.click(screen.getByRole("button", { name: "Show more" }));

    await waitFor(() => {
      expect(fetchPage).toHaveBeenLastCalledWith("cursor-2");
    });
    expect(await screen.findByText("Failed")).toBeInTheDocument();
  });
});
