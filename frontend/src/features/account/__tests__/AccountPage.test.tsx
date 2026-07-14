import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { AccountPage } from "../AccountPage";

function profile(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    email: "someone@acme.example",
    full_name: "Someone",
    role: "member",
    status: "active",
    must_change_password: false,
    timezone: null,
    locale: null,
    date_format: null,
    last_login_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function stubBackend(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get(apiUrl("/api-keys"), () => HttpResponse.json({ items: [] })),
    http.get(apiUrl("/auth/me"), () =>
      HttpResponse.json({
        user: profile(overrides),
        locale_choices: ["ru", "en"],
        date_format_choices: ["DD.MM.YYYY", "MM/DD/YYYY", "YYYY-MM-DD"],
      }),
    ),
  );
}

describe("AccountPage", () => {
  it("renders the language and region section from the catalogues", async () => {
    stubBackend();
    renderAs("member", <AccountPage />);

    // The card's title ships with its loading skeleton; the fields arrive with /me.
    expect(await screen.findByText("Language and region")).toBeInTheDocument();
    expect(await screen.findByLabelText("Timezone")).toBeInTheDocument();
    // A user with no personal override reads the inherited state, not a sentinel.
    expect(screen.getByRole("combobox", { name: "Language" })).toHaveTextContent(
      "Organization default",
    );
  });

  it("saves an edited full name via PATCH /auth/me", async () => {
    stubBackend();
    let patched: unknown = null;
    server.use(
      http.patch(apiUrl("/auth/me"), async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json({
          id: 1,
          email: "someone@acme.example",
          full_name: "Renamed",
          role: "member",
          status: "active",
          must_change_password: false,
          timezone: null,
          locale: null,
          date_format: null,
          last_login_at: null,
          created_at: "2026-01-01T00:00:00Z",
        });
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <AccountPage />);

    const nameInput = await screen.findByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed");
    // Only the dirty Profile card's Save is enabled (Region's stays disabled).
    const [save] = screen.getAllByRole("button", { name: "Save" }).filter((button) => {
      return !(button as HTMLButtonElement).disabled;
    });
    expect(save).toBeDefined();
    await user.click(save);

    await waitFor(() => {
      expect(patched).toEqual({ full_name: "Renamed" });
    });
  });

  it("clears a personal override back to the org default with an explicit null", async () => {
    stubBackend({ locale: "ru", date_format: "DD.MM.YYYY" });
    let patched: unknown = null;
    server.use(
      http.patch(apiUrl("/auth/me"), async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json(profile());
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <AccountPage />);

    // The language select opens on its current value and offers the way back.
    await user.click(await screen.findByRole("combobox", { name: "Language" }));
    await user.click(await screen.findByRole("option", { name: "Organization default" }));

    const save = screen.getAllByRole("button", { name: "Save" }).find((button) => {
      return !(button as HTMLButtonElement).disabled;
    });
    expect(save).toBeDefined();
    await user.click(save as HTMLElement);

    // Absent fields stay untouched; the cleared one travels as null, which the
    // backend reads as "fall back to the org default".
    await waitFor(() => {
      expect(patched).toEqual({ locale: null });
    });
  });

  it("keeps revoked keys behind a collapsed section, apart from the active ones", async () => {
    stubBackend();
    server.use(
      http.get(apiUrl("/api-keys"), () =>
        HttpResponse.json({
          items: [
            {
              id: 1,
              user_id: 1,
              prefix: "ach_live",
              scope: { access: "read", sources: null },
              expires_at: null,
              last_used_at: null,
              is_revoked: false,
              revoked_at: null,
              created_at: "2026-01-01T00:00:00Z",
            },
            {
              id: 2,
              user_id: 1,
              prefix: "ach_dead",
              scope: { access: "read", sources: null },
              expires_at: null,
              last_used_at: null,
              is_revoked: true,
              revoked_at: "2026-02-01T00:00:00Z",
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    renderAs("member", <AccountPage />);

    expect(await screen.findByText("ach_live…")).toBeInTheDocument();
    expect(screen.queryByText("ach_dead…")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Revoked keys (1)" }));

    expect(await screen.findByText("ach_dead…")).toBeInTheDocument();
    // The revoked row is a record, not an action — no second Revoke button.
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
  });

  it("renames a key in place — Enter sends the PATCH", async () => {
    stubBackend();
    server.use(
      http.get(apiUrl("/api-keys"), () =>
        HttpResponse.json({
          items: [
            {
              id: 7,
              user_id: 1,
              prefix: "ach_live",
              name: "CI server",
              scope: { access: "read", sources: null },
              expires_at: null,
              last_used_at: null,
              is_revoked: false,
              revoked_at: null,
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
        }),
      ),
    );
    let patched: unknown = null;
    server.use(
      http.patch(apiUrl("/api-keys/7"), async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json({ name: "renamed" });
      }),
    );
    const user = userEvent.setup();
    renderAs("member", <AccountPage />);

    expect(await screen.findByText("CI server")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByDisplayValue("CI server");
    await user.clear(input);
    await user.type(input, "my laptop{Enter}");

    await waitFor(() => {
      expect(patched).toEqual({ name: "my laptop" });
    });
  });
});
