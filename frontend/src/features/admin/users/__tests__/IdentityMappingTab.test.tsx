import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderWithProviders } from "@/test/render";

import { IdentityMappingTab } from "../IdentityMappingTab";
import type { MappingCandidate, MappingPage } from "../types";

const MAPPING: MappingPage = {
  items: [
    {
      user_id: 2,
      full_name: "Anna Orlova",
      email: "anna@acme.example",
      links: [
        {
          principal_id: 11,
          source_id: 1,
          source_user_id: "slack-1",
          email: null,
          display_name: "@anna",
          pinned: false,
        },
      ],
    },
    { user_id: 3, full_name: "Maria Kim", email: "maria@acme.example", links: [] },
  ],
  total: 2,
  page: 1,
  per_page: 50,
  sources: [{ id: 1, name: "Slack", connector_type: "slack" }],
};

const CANDIDATES: MappingCandidate[] = [
  {
    id: 12,
    source_user_id: "slack-2",
    email: null,
    display_name: "@maria",
    linked_user_id: null,
    pinned: false,
  },
];

function stubBackend() {
  server.use(
    http.get(apiUrl("/admin/identity-mapping"), () => HttpResponse.json(MAPPING)),
    http.get(apiUrl("/admin/identity-mapping/candidates"), () =>
      HttpResponse.json({ items: CANDIDATES }),
    ),
  );
}

describe("IdentityMappingTab", () => {
  it("opens the link popover from a matched cell and unlinks inside it", async () => {
    stubBackend();
    let unlinked: unknown;
    server.use(
      http.post(apiUrl("/admin/identity-mapping/unlink"), async ({ request }) => {
        unlinked = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<IdentityMappingTab />);

    // The linked chip itself is the trigger — override without unlink→link.
    await user.click(await screen.findByRole("button", { name: /@anna/ }));
    expect(await screen.findByPlaceholderText("Find a source account…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "@maria" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Unlink" }));
    await screen.findByText("Unlink this account?");

    await user.click(screen.getAllByRole("button", { name: "Unlink" }).at(-1) as HTMLElement);
    await waitFor(() => {
      expect(unlinked).toEqual({ principal_id: 11 });
    });
  });

  it("links an unmatched cell to a picked candidate", async () => {
    stubBackend();
    let linked: unknown;
    server.use(
      http.post(apiUrl("/admin/identity-mapping/link"), async ({ request }) => {
        linked = await request.json();
        return HttpResponse.json({
          principal_id: 12,
          source_id: 1,
          source_user_id: "slack-2",
          email: null,
          display_name: "@maria",
          pinned: true,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<IdentityMappingTab />);

    await user.click(await screen.findByRole("button", { name: /link a slack account/i }));
    await user.click(await screen.findByRole("button", { name: "@maria" }));

    await waitFor(() => {
      expect(linked).toEqual({ principal_id: 12, user_id: 3 });
    });
  });
});
