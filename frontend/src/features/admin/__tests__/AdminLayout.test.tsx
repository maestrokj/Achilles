import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ThemeProvider } from "@/providers/theme";
import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { AdminLayout } from "../AdminLayout";

const BRANDING = {
  org_name: "Acme",
  org_logo_url: null,
  accent_color: "#000000",
  timezone: "UTC",
  locale: "en",
  date_format: "iso",
};

function stubApi(count: number) {
  server.use(
    http.get(apiUrl("/notifications/unread"), () => HttpResponse.json({ count })),
    http.get(apiUrl("/events/stream"), () => new HttpResponse(null, { status: 401 })),
    http.get(apiUrl("/platform/branding"), () => HttpResponse.json(BRANDING)),
  );
}

/** The Notifications nav item — a link whose accessible name holds the label. */
function notificationsNav(): HTMLElement {
  return screen
    .getAllByRole("link", { name: /Notifications/ })
    .find((el) => el.getAttribute("href") === "/admin/notifications") as HTMLElement;
}

describe("AdminLayout", () => {
  // The unread count lives on the header bell alone; the sidebar row carries no
  // badge, so an unread count must never leak a number onto the nav item.
  it("keeps the Notifications nav item free of an unread badge", async () => {
    stubApi(5);
    renderAs(
      "owner",
      <ThemeProvider>
        <AdminLayout />
      </ThemeProvider>,
    );

    // Let the counter settle, then confirm the nav item carries no number.
    expect(await screen.findByText("Notifications")).toBeInTheDocument();
    expect(within(notificationsNav()).queryByText("5")).not.toBeInTheDocument();
  });
});
