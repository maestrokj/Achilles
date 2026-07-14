import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "@/lib/toast";
import { describe, expect, it, vi } from "vitest";

import { PROBLEM_CODES, toProblem } from "@/api/problems";
import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { MattermostCard } from "../MattermostCard";
import type { MattermostSettings } from "../types";

// ky+undici consumes the HTTPError body in node, so toProblem returns null off
// the transport — test the code→message branch by mocking the helper directly
// (see the frontend error-test quirk).
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/api/problems", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/problems")>()),
  toProblem: vi.fn(),
}));

const SETTINGS: MattermostSettings = {
  enabled: false,
  base_url: "https://mattermost.company.test",
  bot_username: "achilles",
  bot_token_mask: "••••abcd",
  listener_connected: null,
  last_test_ok: true,
  last_test_at: "2026-07-10T10:00:00Z",
};

function stubGet(settings: MattermostSettings = SETTINGS) {
  server.use(http.get(apiUrl("/admin/mattermost"), () => HttpResponse.json(settings)));
}

describe("MattermostCard", () => {
  it("shows the server URL, the token mask and the bot handle", async () => {
    stubGet();
    renderAs("owner", <MattermostCard readOnly={false} />);

    expect(await screen.findByPlaceholderText("••••abcd")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Server URL/ })).toHaveValue(
      "https://mattermost.company.test",
    );
    expect(screen.getByText("@achilles")).toBeInTheDocument();
  });

  it("PATCHes only the touched fields", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/mattermost"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, bot_token_mask: "••••wxyz" });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <MattermostCard readOnly={false} />);

    await user.type(await screen.findByLabelText("Bot token"), "mm-token-wxyz");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ bot_token: "mm-token-wxyz" });
    });
  });

  it("toggles the master switch", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/mattermost"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, enabled: true });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <MattermostCard readOnly={false} />);

    await user.click(await screen.findByRole("switch"));
    await waitFor(() => {
      expect(patchBody).toEqual({ enabled: true });
    });
  });

  it("runs the live probe and refreshes the card", async () => {
    stubGet();
    let posted = false;
    server.use(
      http.post(apiUrl("/admin/mattermost/test"), () => {
        posted = true;
        return HttpResponse.json({ ok: true, bot_username: "achilles", error: null });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <MattermostCard readOnly={false} />);

    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });

  it("keeps enabling blocked until an address and a token are saved", async () => {
    stubGet({ ...SETTINGS, enabled: false, base_url: null });
    renderAs("owner", <MattermostCard readOnly={false} />);

    expect(await screen.findByRole("switch")).toHaveAttribute("data-disabled");
  });

  it("explains a refused enable and rolls the switch back", async () => {
    stubGet();
    server.use(
      http.patch(apiUrl("/admin/mattermost"), () => new HttpResponse(null, { status: 409 })),
    );
    vi.mocked(toProblem).mockResolvedValue({
      type: "/errors/mattermost-enable-failed",
      title: "Mattermost bot could not be enabled",
      status: 409,
      detail: "Invalid or expired session token",
      code: PROBLEM_CODES.MATTERMOST_ENABLE_FAILED,
      request_id: "req-1",
    });
    const user = userEvent.setup();
    renderAs("owner", <MattermostCard readOnly={false} />);

    await user.click(await screen.findByRole("switch"));
    await waitFor(() => {
      expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
        expect.stringContaining("Invalid or expired session token"),
      );
    });
    // The server rolled enabled back to false; the switch must not read as on.
    expect(screen.getByRole("switch")).not.toBeChecked();
  });

  it("shows the listener's word when the bot is enabled", async () => {
    stubGet({ ...SETTINGS, enabled: true, listener_connected: true });
    renderAs("owner", <MattermostCard readOnly={false} />);

    expect(await screen.findByText("Listening for direct messages.")).toBeInTheDocument();
  });

  it("is read-only for the Admin role", async () => {
    stubGet();
    renderAs("admin", <MattermostCard readOnly />);

    expect(await screen.findByLabelText("Bot token")).toBeDisabled();
    expect(screen.getByRole("textbox", { name: /Server URL/ })).toBeDisabled();
    expect(screen.getByRole("switch")).toHaveAttribute("data-disabled");
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });
});
