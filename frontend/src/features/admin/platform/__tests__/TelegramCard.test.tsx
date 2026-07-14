import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { toast } from "@/lib/toast";
import { describe, expect, it, vi } from "vitest";

import { PROBLEM_CODES, toProblem } from "@/api/problems";
import en from "@/i18n/locales/en";
import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { TelegramCard } from "../TelegramCard";
import type { TelegramSettings } from "../types";

// ky+undici consumes the HTTPError body in node, so toProblem returns null off
// the transport — test the code→message branch by mocking the helper directly
// (see the frontend error-test quirk).
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/api/problems", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/problems")>()),
  toProblem: vi.fn(),
}));

const SETTINGS: TelegramSettings = {
  enabled: false,
  bot_username: "achilles_bot",
  bot_token_mask: "••••abcd",
  webhook_secret_set: true,
  last_test_ok: true,
  last_test_at: "2026-07-04T10:00:00Z",
};

function stubGet(settings: TelegramSettings = SETTINGS) {
  server.use(http.get(apiUrl("/admin/telegram"), () => HttpResponse.json(settings)));
}

describe("TelegramCard", () => {
  it("shows the token mask and the bot handle", async () => {
    stubGet();
    renderAs("owner", <TelegramCard readOnly={false} />);

    expect(await screen.findByPlaceholderText("••••abcd")).toBeInTheDocument();
    expect(screen.getByText("@achilles_bot")).toBeInTheDocument();
  });

  it("PATCHes only the touched token", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/telegram"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, bot_token_mask: "••••wxyz" });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <TelegramCard readOnly={false} />);

    await user.type(await screen.findByLabelText("Bot token"), "12345:new-token-wxyz");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ bot_token: "12345:new-token-wxyz" });
    });
  });

  it("toggles the master switch", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/telegram"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ ...SETTINGS, enabled: true });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <TelegramCard readOnly={false} />);

    await user.click(await screen.findByRole("switch"));
    await waitFor(() => {
      expect(patchBody).toEqual({ enabled: true });
    });
  });

  it("runs the live probe and refreshes the card", async () => {
    stubGet();
    let posted = false;
    server.use(
      http.post(apiUrl("/admin/telegram/test"), () => {
        posted = true;
        return HttpResponse.json({ ok: true, bot_username: "achilles_bot", error: null });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <TelegramCard readOnly={false} />);

    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });

  it("keeps enabling blocked until a token is saved", async () => {
    stubGet({ ...SETTINGS, enabled: false, bot_token_mask: null });
    renderAs("owner", <TelegramCard readOnly={false} />);

    expect(await screen.findByRole("switch")).toHaveAttribute("data-disabled");
  });

  it("explains a webhook-not-public enable failure and rolls the switch back", async () => {
    stubGet();
    server.use(
      http.patch(apiUrl("/admin/telegram"), () => new HttpResponse(null, { status: 409 })),
    );
    vi.mocked(toProblem).mockResolvedValue({
      type: "/errors/telegram-webhook-not-public",
      title: "Telegram bot could not be enabled",
      status: 409,
      detail: "",
      code: PROBLEM_CODES.TELEGRAM_WEBHOOK_NOT_PUBLIC,
      request_id: "req-1",
    });
    const user = userEvent.setup();
    renderAs("owner", <TelegramCard readOnly={false} />);

    await user.click(await screen.findByRole("switch"));
    await waitFor(() => {
      // The central helper titles the toast with the failed action and derives
      // the "why" from the problem code via the errors.codes.* registry.
      expect(vi.mocked(toast.error)).toHaveBeenCalled();
    });
    const [title, options] = vi.mocked(toast.error).mock.calls.at(-1) ?? [];
    expect(title).toBe("Could not save the settings.");
    expect(options).toMatchObject({
      description: en.errors.codes.TELEGRAM_WEBHOOK_NOT_PUBLIC,
    });
    // The server rolled enabled back to false; the switch must not read as on.
    expect(screen.getByRole("switch")).not.toBeChecked();
  });

  it("is read-only for the Admin role", async () => {
    stubGet();
    renderAs("admin", <TelegramCard readOnly />);

    expect(await screen.findByLabelText("Bot token")).toBeDisabled();
    expect(screen.getByRole("switch")).toHaveAttribute("data-disabled");
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });
});
