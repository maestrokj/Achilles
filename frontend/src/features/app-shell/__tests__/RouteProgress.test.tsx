import { render, screen } from "@testing-library/react";
import { createMemoryRouter, Link, Outlet, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { RouteProgress } from "../RouteProgress";

describe("RouteProgress", () => {
  // With synchronous element routes navigation stays "idle", so the bar renders
  // nothing. useNavigation requires a data router, so mount inside
  // createMemoryRouter (as it lives inside createBrowserRouter in production).
  it("renders nothing while navigation is idle", () => {
    const router = createMemoryRouter([{ path: "/", element: <RouteProgress /> }]);
    render(<RouterProvider router={router} />);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  // The visible path: a lazy child chunk that never resolves keeps navigation in
  // "loading", so the bar mounts. Mirrors production, where every leaf page is a
  // lazy route under the RootShell that hosts RouteProgress.
  it("shows the bar while a lazy route chunk is loading", async () => {
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: (
            <>
              <RouteProgress />
              <Outlet />
            </>
          ),
          children: [
            { index: true, element: <Link to="/slow">go</Link> },
            { path: "slow", lazy: () => new Promise<never>(() => {}) },
          ],
        },
      ],
      { initialEntries: ["/"] },
    );
    render(<RouterProvider router={router} />);

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    screen.getByText("go").click();
    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
  });
});
