import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { SessionsPage } from "../SessionsPage";
import type { SessionInfo } from "../api";

const CURRENT: SessionInfo = {
  id: "11111111-1111-1111-1111-111111111111",
  user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Chrome/120 Safari/537",
  ip: "10.0.0.1",
  created_at: "2026-07-01T10:00:00Z",
  is_current: true,
};
const OTHER: SessionInfo = {
  id: "22222222-2222-2222-2222-222222222222",
  user_agent: "Mozilla/5.0 (Windows NT 10.0) Firefox/121",
  ip: "10.0.0.2",
  created_at: "2026-06-20T08:00:00Z",
  is_current: false,
};

function stubList(items: SessionInfo[] = [CURRENT, OTHER]) {
  server.use(http.get(apiUrl("/auth/sessions"), () => HttpResponse.json({ items })));
}

describe("SessionsPage", () => {
  it("lists devices, flags the current one, and hides its revoke", async () => {
    stubList();
    renderAs("member", <SessionsPage />);

    expect(await screen.findByText("Chrome on macOS")).toBeInTheDocument();
    expect(screen.getByText("Firefox on Windows")).toBeInTheDocument();
    expect(screen.getByText("This session")).toBeInTheDocument();
    // Only the non-current session offers a Revoke button.
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
  });

  it("revokes another session only after confirming", async () => {
    stubList();
    let deleted: string | null = null;
    server.use(
      http.delete(apiUrl("/auth/sessions/:id"), ({ params }) => {
        deleted = params.id as string;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <SessionsPage />);

    await user.click(await screen.findByRole("button", { name: "Revoke" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() => {
      expect(deleted).toBe(OTHER.id);
    });
  });

  it("ends all other sessions after confirming", async () => {
    stubList();
    let called = false;
    server.use(
      http.post(apiUrl("/auth/sessions/revoke-others"), () => {
        called = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <SessionsPage />);

    await user.click(await screen.findByRole("button", { name: "End all sessions" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "End all sessions" }));

    await waitFor(() => {
      expect(called).toBe(true);
    });
  });
});
