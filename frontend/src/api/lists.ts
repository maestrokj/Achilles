/** Offset-list plumbing shared by the admin list endpoints. */

/** The `{items, total, page, per_page}` envelope every offset list returns. */
export interface OffsetPage<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

export type ListQuery = Record<string, string | number | (string | number)[]>;

/** Repeated facet params (role=a&role=b) need URLSearchParams — ky's object
 * form takes primitives only. */
export function qs(query: ListQuery): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    for (const item of Array.isArray(value) ? value : [value]) params.append(key, String(item));
  }
  return params;
}
