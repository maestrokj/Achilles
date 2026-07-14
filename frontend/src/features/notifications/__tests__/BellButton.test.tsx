import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { BellButton } from "../BellButton";
import type { NotificationItem } from "../types";

const ITEM: NotificationItem = {
  id: 7,
  event: "agent.admin_paused",
  event_type: "agent",
  severity: "info",
  title: "Агент «Watcher» приостановлен администратором",
  body: null,
  source: "agent_engine",
  source_ref: "agent/7",
  dedup_count: 3,
  created_at: "2026-07-04T10:00:00Z",
  last_seen_at: null,
  read_at: null,
};

function stubApi(count: number, items: NotificationItem[]) {
  server.use(
    http.get(apiUrl("/notifications/unread"), () => HttpResponse.json({ count })),
    http.get(apiUrl("/events/stream"), () => new HttpResponse(null, { status: 401 })),
    http.get(apiUrl("/notifications"), () =>
      HttpResponse.json({ items, total: items.length, page: 1, per_page: 25 }),
    ),
  );
}

describe("BellButton", () => {
  it("shows the unread badge from the counter", async () => {
    stubApi(3, [ITEM]);
    renderAs("member", <BellButton inboxPath="/inbox" settingsPath="/account" />);
    expect(await screen.findByText("3")).toBeInTheDocument();
  });

  it("opens the panel with the feed tail and the series badge", async () => {
    stubApi(1, [ITEM]);
    const user = userEvent.setup();
    renderAs("member", <BellButton inboxPath="/inbox" settingsPath="/account" />);

    await user.click(await screen.findByRole("button", { name: "Notifications" }));
    expect(await screen.findByText(/Watcher/)).toBeInTheDocument();
    expect(screen.getByText("×3")).toBeInTheDocument();
  });

  it("deep-links a panel row to its source screen", async () => {
    stubApi(1, [ITEM]);
    const user = userEvent.setup();
    renderAs("member", <BellButton inboxPath="/inbox" settingsPath="/account" />);

    await user.click(await screen.findByRole("button", { name: "Notifications" }));
    // inboxPath is a personal surface → the agent ref lands on /agents/7.
    expect(await screen.findByRole("link", { name: /agent\/7/ })).toHaveAttribute(
      "href",
      "/agents/7",
    );
  });

  it("marks everything read from the panel header", async () => {
    stubApi(1, [ITEM]);
    let posted = false;
    server.use(
      http.post(apiUrl("/notifications/read-all"), () => {
        posted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <BellButton inboxPath="/inbox" settingsPath="/account" />);

    await user.click(await screen.findByRole("button", { name: "Notifications" }));
    await user.click(await screen.findByRole("button", { name: "Mark all as read" }));
    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });
});
