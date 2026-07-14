import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/render";

import { NotFoundPage } from "../NotFoundPage";

describe("NotFoundPage", () => {
  it("shows the 404 stub with a link home", () => {
    renderWithProviders(<NotFoundPage />, { route: "/unknown-path" });

    expect(screen.getByText("404")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go home" })).toHaveAttribute("href", "/");
  });
});
