import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OfflineBanner } from "../OfflineBanner";

function setOnLine(value: boolean): void {
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(value);
}

describe("OfflineBanner", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("stays hidden while online", () => {
    setOnLine(true);
    render(<OfflineBanner />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows the offline strip while offline", () => {
    setOnLine(false);
    render(<OfflineBanner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("No connection")).toBeInTheDocument();
  });
});
