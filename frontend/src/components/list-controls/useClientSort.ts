import { useMemo, useState } from "react";

import type { SortState } from "./DataTable";

export type SortAccessors<T, K extends string> = Record<K, (item: T) => string | number>;

/** Client-side column sort for tables whose full result set is already in memory
 *  (small, unpaginated lists). Server-paginated tables must sort via the query
 *  instead — sorting a single page client-side would misrepresent the whole set.
 *  Define `accessors` as a module constant so its reference stays stable. */
export function useClientSort<T, K extends string>(
  items: T[],
  accessors: SortAccessors<T, K>,
  initial: SortState<NoInfer<K>>,
): { sorted: T[]; sort: SortState<K>; toggle: (key: K) => void } {
  const [sort, setSort] = useState<SortState<K>>(initial);
  const toggle = (key: K) => {
    setSort((current) =>
      current.key === key ? { key, desc: !current.desc } : { key, desc: false },
    );
  };
  const sorted = useMemo(() => {
    const value = accessors[sort.key];
    return [...items].sort((a, b) => {
      const left = value(a);
      const right = value(b);
      const order = left < right ? -1 : left > right ? 1 : 0;
      return sort.desc ? -order : order;
    });
  }, [items, sort, accessors]);
  return { sorted, sort, toggle };
}
