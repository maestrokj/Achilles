import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { BulkInvitePage } from "../BulkInvitePage";
import type { BulkReport } from "../types";

/** One valid row, one conflict, one broken email — the wizard must keep the
 * valid row sendable and surface the rest without blocking. */
const REPORT: BulkReport = {
  results: [
    {
      row: 1,
      email: "new@acme.example",
      status: "created",
      role: "member",
      role_from_default: true,
    },
    {
      row: 2,
      email: "old@acme.example",
      status: "conflict",
      message: "already registered",
      role: "admin",
      role_from_default: false,
    },
    {
      row: 3,
      email: "broken",
      status: "invalid",
      message: "email",
      role: "member",
      role_from_default: true,
    },
  ],
};

function stubBackend({ smtp = true }: { smtp?: boolean } = {}) {
  const bulkCalls: { dryRun: string | null; defaultRole: string | null }[] = [];
  server.use(
    http.get(apiUrl("/admin/settings"), () =>
      HttpResponse.json({ smtp_configured: smtp } as Record<string, unknown>),
    ),
    http.post(apiUrl("/invites/bulk"), ({ request }) => {
      const url = new URL(request.url);
      bulkCalls.push({
        dryRun: url.searchParams.get("dry_run"),
        defaultRole: url.searchParams.get("default_role"),
      });
      return HttpResponse.json(REPORT, { status: 207 });
    }),
  );
  return bulkCalls;
}

async function pasteList(user: ReturnType<typeof userEvent.setup>) {
  const textarea = await screen.findByLabelText("Or paste addresses as a list");
  await user.click(textarea);
  await user.paste("new@acme.example\nold@acme.example\nbroken");
}

describe("BulkInvitePage", () => {
  it("previews via dry-run, then sends for real", async () => {
    const bulkCalls = stubBackend();
    const user = userEvent.setup();
    renderWithProviders(<BulkInvitePage />);

    await pasteList(user);
    await user.click(screen.getByRole("button", { name: "Next" }));

    // Step 2: the dry-run report with per-status counters.
    expect(await screen.findByText("new@acme.example")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /will be invited/ })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: /already exists/ })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: /error/ })).toHaveTextContent("1");
    expect(bulkCalls).toEqual([{ dryRun: "true", defaultRole: "member" }]);

    await user.click(screen.getByRole("button", { name: "Send 1 invitations" }));

    expect(await screen.findByText("Invitations are on their way")).toBeInTheDocument();
    expect(screen.getByText("Queued 1 · skipped 1 · errors 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download report" })).toBeInTheDocument();
    expect(bulkCalls).toEqual([
      { dryRun: "true", defaultRole: "member" },
      { dryRun: "false", defaultRole: "member" },
    ]);
  });

  it("filters the preview by a status counter", async () => {
    stubBackend();
    const user = userEvent.setup();
    renderWithProviders(<BulkInvitePage />);

    await pasteList(user);
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("new@acme.example");

    await user.click(screen.getByRole("button", { name: /error/ }));

    expect(screen.getByText("broken")).toBeInTheDocument();
    expect(screen.queryByText("new@acme.example")).not.toBeInTheDocument();
  });

  it("stays blocked while SMTP is not configured", async () => {
    stubBackend({ smtp: false });
    const user = userEvent.setup();
    renderWithProviders(<BulkInvitePage />);

    await pasteList(user);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    });
  });
});
