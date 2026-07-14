import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { NotificationFeed } from "../NotificationFeed";
import { EVENT_TYPE_KEYS, type NotificationItem } from "../types";

const ITEM: NotificationItem = {
  id: 7,
  event: "agent.admin_paused",
  event_type: "agent",
  severity: "info",
  title: "Agent «Watcher» paused",
  body: null,
  source: "agent_engine",
  source_ref: "agent/7",
  dedup_count: 1,
  created_at: "2026-07-04T10:00:00Z",
  last_seen_at: null,
  read_at: null,
};

/** Record every feed request so the tests can assert the outgoing facets. */
function captureFeed(): URL[] {
  const seen: URL[] = [];
  server.use(
    http.get(apiUrl("/notifications"), ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({ items: [ITEM], total: 1, page: 1, per_page: 50 });
    }),
  );
  return seen;
}

describe("NotificationFeed", () => {
  it("opens on the Unread-only default", async () => {
    const seen = captureFeed();
    renderAs("member", <NotificationFeed types={EVENT_TYPE_KEYS} surface="app" />);

    await screen.findByText(/Watcher/);
    expect(seen.at(-1)?.searchParams.get("unread")).toBe("true");
  });

  it("sends the chosen period to the backend", async () => {
    const seen = captureFeed();
    const user = userEvent.setup();
    renderAs("member", <NotificationFeed types={EVENT_TYPE_KEYS} surface="app" />);

    await screen.findByText(/Watcher/);
    // Period is a single-window facet — picking one tick carries the window.
    await user.click(screen.getByRole("button", { name: /Period/ }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Last 7 days" }));

    await waitFor(() => {
      expect(seen.at(-1)?.searchParams.get("period")).toBe("7d");
    });
  });

  it("combines multiple type picks as OR", async () => {
    const seen = captureFeed();
    const user = userEvent.setup();
    renderAs("member", <NotificationFeed types={EVENT_TYPE_KEYS} surface="app" />);

    await screen.findByText(/Watcher/);
    await user.click(screen.getByRole("button", { name: /Type/ }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Sync" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Budget" }));

    await waitFor(() => {
      expect(seen.at(-1)?.searchParams.getAll("type")).toEqual(["sync", "budget"]);
    });
  });

  it("debounces the search box into the q param, past the 2-char threshold", async () => {
    const seen = captureFeed();
    const user = userEvent.setup();
    renderAs("member", <NotificationFeed types={EVENT_TYPE_KEYS} surface="app" />);

    await screen.findByText(/Watcher/);
    await user.type(screen.getByPlaceholderText("Search by source or keyword"), "Wat");

    await waitFor(() => {
      expect(seen.at(-1)?.searchParams.get("q")).toBe("Wat");
    });
  });

  it("deep-links an inbox row to the personal agent screen", async () => {
    captureFeed();
    renderAs("member", <NotificationFeed types={EVENT_TYPE_KEYS} surface="app" />);

    await screen.findByText(/Watcher/);
    expect(screen.getByRole("link", { name: /agent\/7/ })).toHaveAttribute("href", "/agents/7");
  });

  it("deep-links the same row to the admin agent screen on the admin surface", async () => {
    captureFeed();
    renderAs("owner", <NotificationFeed types={EVENT_TYPE_KEYS} surface="admin" />);

    await screen.findByText(/Watcher/);
    expect(screen.getByRole("link", { name: /agent\/7/ })).toHaveAttribute(
      "href",
      "/admin/agents/7",
    );
  });

  it("marks a single row read through POST /notifications/{id}/read", async () => {
    captureFeed();
    let readId: string | null = null;
    server.use(
      http.post(apiUrl("/notifications/:id/read"), ({ params }) => {
        readId = params.id as string;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <NotificationFeed types={EVENT_TYPE_KEYS} surface="app" />);

    await screen.findByText(/Watcher/);
    await user.click(screen.getByRole("button", { name: "Mark as read" }));
    await waitFor(() => {
      expect(readId).toBe("7");
    });
  });

  it("offers Show history from the empty unread state and drops the unread facet", async () => {
    const seen: URL[] = [];
    server.use(
      http.get(apiUrl("/notifications"), ({ request }) => {
        const url = new URL(request.url);
        seen.push(url);
        // Unread-only view is empty; the history view has the row.
        const items = url.searchParams.get("unread") === "true" ? [] : [ITEM];
        return HttpResponse.json({ items, total: items.length, page: 1, per_page: 50 });
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <NotificationFeed types={EVENT_TYPE_KEYS} surface="app" />);

    // Default unread view resolves to the empty state, not the row.
    await user.click(await screen.findByRole("button", { name: "Show history" }));

    await screen.findByText(/Watcher/);
    expect(seen.at(-1)?.searchParams.get("unread")).toBeNull();
  });
});
