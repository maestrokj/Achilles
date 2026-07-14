import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { apiUrl, server } from "@/test/msw";
import { renderAs } from "@/test/session";

import { LinkPage } from "../LinkPage";

/** LinkPage reads the platform from the route, so mount it under a matching path. */
const linkRoute = (
  <Routes>
    <Route path="/link/:platform" element={<LinkPage />} />
    <Route path="/account" element={<div>account</div>} />
  </Routes>
);

describe("LinkPage", () => {
  it("issues a linking code for the route platform and shows its validity", async () => {
    server.use(
      http.post(apiUrl("/link/telegram"), () =>
        HttpResponse.json({ code: "K7P2-9XQ4", expires_in_seconds: 900 }, { status: 201 }),
      ),
    );
    renderAs("member", linkRoute, { route: "/link/telegram" });

    expect(await screen.findByText("K7P2-9XQ4")).toBeInTheDocument();
    expect(screen.getByText("Valid for 15 min.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    // The heading interpolates the platform name.
    expect(screen.getByRole("heading", { name: "Link Telegram" })).toBeInTheDocument();
  });

  it("surfaces a failure to issue the code", async () => {
    server.use(http.post(apiUrl("/link/slack"), () => new HttpResponse(null, { status: 500 })));
    renderAs("member", linkRoute, { route: "/link/slack" });

    expect(await screen.findByText("Could not generate a code. Try again.")).toBeInTheDocument();
  });

  it("redirects an unknown platform back to the account page", async () => {
    renderAs("member", linkRoute, { route: "/link/whatsapp" });

    expect(await screen.findByText("account")).toBeInTheDocument();
  });
});
