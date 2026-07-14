import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { NotificationPrefs } from "../NotificationPrefs";
import type { Pref } from "../types";

vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const PREFS: Pref[] = [
  { event_type: "agent", in_app_enabled: true, email_enabled: false },
  { event_type: "account", in_app_enabled: true, email_enabled: true },
];

function stubGet(items: Pref[] = PREFS) {
  server.use(http.get(apiUrl("/notifications/prefs"), () => HttpResponse.json({ items })));
}

describe("NotificationPrefs", () => {
  it("saves an email opt-in through PUT and reflects the server echo", async () => {
    stubGet();
    let sent: { items: Pref[] } | null = null;
    server.use(
      http.put(apiUrl("/notifications/prefs"), async ({ request }) => {
        sent = (await request.json()) as { items: Pref[] };
        // The backend echoes the full effective set with the cell flipped on.
        const items = PREFS.map((p) =>
          p.event_type === "agent" ? { ...p, email_enabled: true } : p,
        );
        return HttpResponse.json({ items });
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <NotificationPrefs />);

    // Agent row: [in-app on, email off]. Flip its email switch on.
    const switches = await screen.findAllByRole("switch");
    // Order: agent in-app, agent email, account in-app, account email.
    expect(switches[1]).toHaveAttribute("aria-checked", "false");
    await user.click(switches[1]);

    await waitFor(() => {
      expect(sent).toEqual({
        items: [{ event_type: "agent", in_app_enabled: true, email_enabled: true }],
      });
    });
    // The server echo lands in the cache → the switch shows the saved state.
    await waitFor(() => {
      expect(screen.getAllByRole("switch")[1]).toHaveAttribute("aria-checked", "true");
    });
  });

  it("surfaces a save failure and leaves the switch at the server value", async () => {
    const { toast } = await import("@/lib/toast");
    stubGet();
    server.use(
      http.put(apiUrl("/notifications/prefs"), () =>
        HttpResponse.json(
          { code: "INTERNAL", title: "Server error", detail: "boom" },
          { status: 500 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderAs("member", <NotificationPrefs />);

    const switches = await screen.findAllByRole("switch");
    await user.click(switches[1]);

    // The failure is shown to the user (toastApiError → toast.error), and the
    // controlled switch snaps back to the unchanged server value.
    await waitFor(() => {
      expect(vi.mocked(toast.error)).toHaveBeenCalled();
    });
    expect(screen.getAllByRole("switch")[1]).toHaveAttribute("aria-checked", "false");
  });

  it("shows the error state with a retry when prefs fail to load", async () => {
    server.use(
      http.get(apiUrl("/notifications/prefs"), () =>
        HttpResponse.json({ code: "INTERNAL", title: "Server error" }, { status: 500 }),
      ),
    );
    renderAs("member", <NotificationPrefs />);
    expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
