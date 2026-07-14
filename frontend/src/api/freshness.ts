/** Query freshness overrides for the global `staleTime` (providers/query.tsx). */

/** Live status boards fed from *outside* the screen that shows them — background
 *  workers and the scheduler (sync/curation/backup runs, agent ticks, usage
 *  counters) or actions taken on other screens (the audit journal). No mutation
 *  on the viewing screen invalidates their key, so the global 30s staleTime would
 *  serve a stale snapshot on every revisit until it expired. Pin to 0: refetch on
 *  each visit. The cache still backs the view (or keepPreviousData does), so the
 *  refetch is a quiet background swap — no skeleton flash, no layout shift.
 *
 *  While a screen stays OPEN, freshness is push: the events stream
 *  (features/live/useEventStream) invalidates a board's keys on every server
 *  nudge. This staleTime-0 is the safety net for the stream being down —
 *  screens never poll. */
export const LIVE_STALE_TIME = 0;
