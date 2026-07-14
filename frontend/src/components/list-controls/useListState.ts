import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import type { ListQuery } from "@/api/lists";

// Shared list behavior for admin tables — admin-panel/_workzone/list-controls.html.
// The URL is the single carrier of q / facets / page: refresh and links
// reproduce the selection. Screens only declare their facet names.

export const SEARCH_DEBOUNCE_MS = 300;
const SEARCH_MIN_CHARS = 2;

const PAGE_KEY = "page";
const PER_PAGE_KEY = "per_page";
const QUERY_KEY = "q";
const RESERVED_KEYS = new Set([PAGE_KEY, PER_PAGE_KEY, QUERY_KEY]);

export const PER_PAGE_CHOICES = [10, 25, 50, 100] as const;
export type PerPage = (typeof PER_PAGE_CHOICES)[number];
const DEFAULT_PER_PAGE: PerPage = 50;

export interface ListState {
  /** Effective server-side query (already debounced + thresholded). */
  q: string;
  /** Raw input value — what the search box renders. */
  input: string;
  setInput: (value: string) => void;
  clearSearch: () => void;
  /** Clear the search term *and* every facet — the reset an empty filtered view
   *  offers, matching exactly what `isFiltered` counts (clearSearch drops q only). */
  clearFilters: () => void;
  /** A search term or any facet is active — an empty result is "no matches"
   *  (offer a reset), not "this section has nothing yet". */
  isFiltered: boolean;
  facets: Record<string, string[]>;
  toggleFacet: (name: string, value: string) => void;
  /** Replace a facet's values wholesale — the single-select counterpart of toggleFacet. */
  setFacet: (name: string, values: string[]) => void;
  page: number;
  setPage: (page: number) => void;
  perPage: PerPage;
  setPerPage: (perPage: PerPage) => void;
}

function parsePerPage(raw: string | null, fallback: PerPage): PerPage {
  const value = Number(raw);
  return (PER_PAGE_CHOICES as readonly number[]).includes(value) ? (value as PerPage) : fallback;
}

/** The server-side query the current list state describes (defaults omitted).
 * Pass the same `defaultPerPage` the list was created with so its default page
 * size stays out of the URL. */
export function buildListQuery(
  list: ListState,
  defaultPerPage: PerPage = DEFAULT_PER_PAGE,
): ListQuery {
  const query: ListQuery = {};
  if (list.q) query["q"] = list.q;
  for (const [name, values] of Object.entries(list.facets)) {
    if (values.length > 0) query[name] = values;
  }
  if (list.page > 1) query["page"] = list.page;
  if (list.perPage !== defaultPerPage) query["per_page"] = list.perPage;
  return query;
}

/** @param defaultPerPage the page size a screen opens on (kept out of the URL). */
export function useListState(
  facetNames: readonly string[] = [],
  defaultPerPage: PerPage = DEFAULT_PER_PAGE,
): ListState {
  const [params, setParams] = useSearchParams();
  const q = params.get(QUERY_KEY) ?? "";
  const [input, setInput] = useState(q);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Any change of the query resets to page 1 — the old page may not exist in
  // the new result set.
  const commit = (mutate: (next: URLSearchParams) => void) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        mutate(next);
        next.delete(PAGE_KEY);
        return next;
      },
      { replace: true },
    );
  };

  useEffect(() => {
    const trimmed = input.trim();
    if (trimmed === q) return;
    // One significant char is below the launch threshold — no call, no URL churn.
    if (trimmed.length > 0 && trimmed.length < SEARCH_MIN_CHARS) return;
    debounceRef.current = setTimeout(() => {
      commit((next) => {
        if (trimmed) next.set(QUERY_KEY, trimmed);
        else next.delete(QUERY_KEY);
      });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- commit is stable per render of setParams
  }, [input, q]);

  const facets: Record<string, string[]> = {};
  for (const name of facetNames) {
    if (RESERVED_KEYS.has(name)) continue;
    facets[name] = params.getAll(name);
  }

  return {
    q,
    input,
    setInput,
    clearSearch: () => {
      setInput("");
      commit((next) => {
        next.delete(QUERY_KEY);
      });
    },
    clearFilters: () => {
      setInput("");
      commit((next) => {
        next.delete(QUERY_KEY);
        for (const name of facetNames) {
          if (!RESERVED_KEYS.has(name)) next.delete(name);
        }
      });
    },
    isFiltered: q !== "" || Object.values(facets).some((values) => values.length > 0),
    facets,
    toggleFacet: (name, value) => {
      commit((next) => {
        const current = next.getAll(name);
        next.delete(name);
        const toggled = current.includes(value)
          ? current.filter((v) => v !== value)
          : [...current, value];
        for (const v of toggled) next.append(name, v);
      });
    },
    setFacet: (name, values) => {
      commit((next) => {
        next.delete(name);
        for (const v of values) next.append(name, v);
      });
    },
    page: Math.max(1, Number(params.get(PAGE_KEY)) || 1),
    setPage: (page) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (page > 1) next.set(PAGE_KEY, String(page));
          else next.delete(PAGE_KEY);
          return next;
        },
        { replace: true },
      );
    },
    perPage: parsePerPage(params.get(PER_PAGE_KEY), defaultPerPage),
    setPerPage: (perPage) => {
      commit((next) => {
        if (perPage === defaultPerPage) next.delete(PER_PAGE_KEY);
        else next.set(PER_PAGE_KEY, String(perPage));
      });
    },
  };
}
