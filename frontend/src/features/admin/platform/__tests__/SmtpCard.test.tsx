import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { SmtpCard } from "../SmtpCard";
import type { SmtpSettings } from "../types";

const SETTINGS: SmtpSettings = {
  is_enabled: true,
  host: "smtp.company.com",
  port: 587,
  security: "starttls",
  username: "mailer",
  password_mask: "••••abcd",
  from_address: "Achilles <no-reply@company.com>",
  is_available: true,
  last_test_ok: true,
  last_test_at: "2026-07-04T10:00:00Z",
};

function stubGet(settings: SmtpSettings = SETTINGS) {
  server.use(http.get(apiUrl("/admin/smtp"), () => HttpResponse.json(settings)));
}

describe("SmtpCard", () => {
  it("shows the saved config and the password mask", async () => {
    stubGet();
    renderAs("owner", <SmtpCard readOnly={false} />);

    expect(await screen.findByLabelText("Host")).toHaveValue("smtp.company.com");
    expect(screen.getByPlaceholderText("••••abcd")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Working" })).toBeInTheDocument();
  });

  it("PATCHes only the touched fields", async () => {
    stubGet();
    let patchBody: unknown = null;
    server.use(
      http.patch(apiUrl("/admin/smtp"), async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json(SETTINGS);
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <SmtpCard readOnly={false} />);

    const host = await screen.findByLabelText("Host");
    await user.clear(host);
    await user.type(host, "mailpit");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchBody).toEqual({ host: "mailpit" });
    });
  });

  it("runs the inline test against the saved settings", async () => {
    stubGet();
    let posted = false;
    server.use(
      http.post(apiUrl("/admin/smtp/test"), () => {
        posted = true;
        return HttpResponse.json({ ok: true, error: null });
      }),
    );
    const user = userEvent.setup();
    renderAs("owner", <SmtpCard readOnly={false} />);

    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });

  it("keeps Test disabled while unavailable or while a draft lingers", async () => {
    stubGet({ ...SETTINGS, is_available: false });
    const user = userEvent.setup();
    renderAs("owner", <SmtpCard readOnly={false} />);

    const testButton = await screen.findByRole("button", { name: "Test connection" });
    expect(testButton).toBeDisabled(); // not available → nothing to probe

    await user.type(screen.getByLabelText("Host"), "x");
    expect(testButton).toBeDisabled(); // an unsaved draft would not be what's probed
  });

  it("is read-only for the Admin role", async () => {
    stubGet();
    renderAs("admin", <SmtpCard readOnly />);

    expect(await screen.findByLabelText("Host")).toBeDisabled();
    expect(screen.getByRole("switch")).toHaveAttribute("data-disabled");
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });
});
