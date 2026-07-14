import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/render";

import { McpConnect } from "../McpConnect";

describe("McpConnect", () => {
  it("keeps the connection panel collapsed until the disclosure is opened", () => {
    renderWithProviders(<McpConnect />);

    const trigger = screen.getByRole("button", { name: /connect mcp/i });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Transport")).not.toBeInTheDocument();
  });

  it("reveals the client-neutral connection spec built from the current origin", async () => {
    const user = userEvent.setup();
    renderWithProviders(<McpConnect />);

    await user.click(screen.getByRole("button", { name: /connect mcp/i }));

    // Endpoint is origin + /mcp — the copy-me address every client posts to.
    expect(screen.getByText(`${window.location.origin}/mcp`)).toBeInTheDocument();
    // The example command carries the placeholder key when none is minted yet.
    expect(screen.getByText(/claude mcp add/)).toHaveTextContent("<your-api-key>");
    expect(screen.getByText(/Replace <your-api-key>/)).toBeInTheDocument();
  });

  it("fills a freshly minted key into the example command", async () => {
    const user = userEvent.setup();
    renderWithProviders(<McpConnect apiKey="ach_live_key" />);

    await user.click(screen.getByRole("button", { name: /connect mcp/i }));

    expect(screen.getByText(/claude mcp add/)).toHaveTextContent("ach_live_key");
    expect(screen.getByText(/Ready to run/)).toBeInTheDocument();
  });
});
