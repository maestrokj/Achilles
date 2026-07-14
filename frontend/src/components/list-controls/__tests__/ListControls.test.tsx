import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { FacetSelect } from "../FacetSelect";
import { Pagination } from "../Pagination";
import { SearchInput } from "../SearchInput";
import { SEARCH_DEBOUNCE_MS, useListState } from "../useListState";

function Harness({ total = 120 }: { total?: number }) {
  const list = useListState(["role"]);
  return (
    <div>
      <SearchInput
        value={list.input}
        onChange={list.setInput}
        onClear={list.clearSearch}
        placeholder="search"
      />
      <FacetSelect
        label="Role"
        options={[
          { value: "admin", label: "Admin" },
          { value: "member", label: "Member" },
        ]}
        selected={list.facets["role"] ?? []}
        onToggle={(value) => {
          list.toggleFacet("role", value);
        }}
      />
      <Pagination
        page={list.page}
        perPage={list.perPage}
        total={total}
        onPageChange={list.setPage}
        onPerPageChange={list.setPerPage}
      />
      <output data-testid="q">{list.q}</output>
      <output data-testid="page">{String(list.page)}</output>
      <output data-testid="role">{(list.facets["role"] ?? []).join(",")}</output>
    </div>
  );
}

function renderHarness(total?: number) {
  return render(
    <MemoryRouter>
      <Harness total={total} />
    </MemoryRouter>,
  );
}

function pastDebounce(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_MS + 150));
}

describe("search threshold", () => {
  it("one significant char never launches a search", async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.type(screen.getByPlaceholderText("search"), "a");
    await pastDebounce();

    expect(screen.getByTestId("q")).toHaveTextContent("");
  });

  it("two chars land in the query only after the debounce pause", async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.type(screen.getByPlaceholderText("search"), "ab");
    expect(screen.getByTestId("q")).toHaveTextContent("");

    await waitFor(() => {
      expect(screen.getByTestId("q")).toHaveTextContent("ab");
    });
  });

  it("Escape clears the search at once", async () => {
    const user = userEvent.setup();
    renderHarness();

    const input = screen.getByPlaceholderText("search");
    await user.type(input, "ab");
    await waitFor(() => {
      expect(screen.getByTestId("q")).toHaveTextContent("ab");
    });

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(screen.getByTestId("q")).toHaveTextContent("");
    });
    expect(input).toHaveValue("");
  });
});

describe("query changes reset the page", () => {
  it("search input from page 2 returns to page 1", async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByRole("button", { name: "2" }));
    expect(screen.getByTestId("page")).toHaveTextContent("2");

    await user.type(screen.getByPlaceholderText("search"), "ab");
    await waitFor(() => {
      expect(screen.getByTestId("q")).toHaveTextContent("ab");
    });
    expect(screen.getByTestId("page")).toHaveTextContent("1");
  });
});

describe("facets", () => {
  it("values within a facet accumulate as an OR set and toggle off", async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByRole("button", { name: /Role/ }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Admin" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Member" }));
    expect(screen.getByTestId("role")).toHaveTextContent("admin,member");

    await user.click(screen.getByRole("menuitemcheckbox", { name: "Admin" }));
    expect(screen.getByTestId("role")).toHaveTextContent(/^member$/);
  });
});

describe("pagination", () => {
  it("shows the X–Y of N range and disables edges", () => {
    renderHarness(120);

    expect(screen.getByText("1–50 of 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();
  });

  it("zero results render an honest 0–0 of 0", () => {
    renderHarness(0);

    expect(screen.getByText("0–0 of 0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });
});
