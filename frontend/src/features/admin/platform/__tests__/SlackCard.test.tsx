import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { SlackCard } from "../SlackCard";
import type { SlackSettings } from "../types";

const SETTINGS: SlackSettings = {
  enabled: false,
  auto_link_by_email: true,
  team: "T123",
  team_name: "Acme",
  bot_user_id: "U99",
  bot_token_mask: "••••abcd",
  signing_secret_set: true,
  last_test_ok: true,
  last_test_at: "2026-07-04T10:00:00Z",
};

function stubGet(settings: SlackSettings = SETTINGS) {
  server.use(http.get(apiUrl("/admin/slack"), () => HttpResponse.json(settings)));
}

describe("SlackCard", () => {
  it("shows the token mask and the workspace status", async () => {
    stubGet();
    renderAs("owner", <SlackCard readOnly={false} />);

    expect(await screen.findByPlaceholderText("••••abcd")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Connected" })).toBeInTheDocument();
  });

  it("PATCHes only the touched secret", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/slack"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, bot_token_mask: "••••wxyz" });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <SlackCard readOnly={false} />);

    await user.type(await screen.findByLabelText("Bot token"), "xoxb-new-token-wxyz");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ bot_token: "xoxb-new-token-wxyz" });
    });
  });

  it("toggles the master switch", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/slack"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, enabled: true });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <SlackCard readOnly={false} />);

    // Master switch first, auto-link second.
    await user.click((await screen.findAllByRole("switch"))[0]);
    await waitFor(() => {
      expect(patchBody).toEqual({ enabled: true });
    });
  });

  it("toggles auto-link by email", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/slack"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, auto_link_by_email: false });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <SlackCard readOnly={false} />);

    await user.click((await screen.findAllByRole("switch"))[1]);
    await waitFor(() => {
      expect(patchBody).toEqual({ auto_link_by_email: false });
    });
  });

  it("runs the live probe and refreshes the card", async () => {
    stubGet();
    let posted = false;
    server.use(
      http.post(apiUrl("/admin/slack/test"), () => {
        posted = true;
        return HttpResponse.json({
          ok: true,
          team: "T123",
          team_name: "Acme",
          bot_user_id: "U99",
          error: null,
        });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <SlackCard readOnly={false} />);

    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });

  it("keeps Test disabled without a saved token and while a secret edit is unsaved", async () => {
    stubGet({ ...SETTINGS, bot_token_mask: null });
    const user = userEvent.setup();
    renderAs("owner", <SlackCard readOnly={false} />);

    const testButton = await screen.findByRole("button", { name: "Test connection" });
    expect(testButton).toBeDisabled(); // nothing saved to probe yet

    await user.type(await screen.findByLabelText("Bot token"), "xoxb-typed-not-saved");
    expect(testButton).toBeDisabled(); // an unsaved token would not be the one probed
  });

  it("is read-only for the Admin role", async () => {
    stubGet();
    renderAs("admin", <SlackCard readOnly />);

    expect(await screen.findByLabelText("Bot token")).toBeDisabled();
    for (const toggle of screen.getAllByRole("switch")) {
      expect(toggle).toHaveAttribute("data-disabled");
    }
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });
});
