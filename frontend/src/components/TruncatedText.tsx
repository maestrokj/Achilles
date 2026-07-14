import {
  cloneElement,
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
  type Ref,
} from "react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Hover delay before the reveal opens. A pointer merely passing over a value on
 * its way elsewhere must not flash the tooltip; only a deliberate rest on the
 * clipped text should. Instant tooltips read as noise in dense tables. */
const REVEAL_DELAY_MS = 600;

/** The element `TruncatedText` renders as — a span by default, or any element
 * passed via `render` (e.g. a router `<Link/>` or a `<div/>` for a table cell).
 * It receives the truncation classes and the measuring ref; its own props
 * (onClick, to, …) are preserved. */
type RenderElement = ReactElement<{ className?: string; ref?: Ref<HTMLElement> }>;

/** One-line content that clips with an ellipsis and reveals the full text in a
 * styled tooltip — but only when it is actually cut off. The overflow is measured
 * live (initial + on resize), so the tooltip never fires on values that already
 * fit. A drop-in replacement for a bare `truncate` span or a `title=` attribute.
 *
 * - Plain text: `<TruncatedText>{email}</TruncatedText>`.
 * - Link / custom element: pass `render` (the element keeps its own props).
 * - Wrapping markup (e.g. a link that already holds the text): put the markup in
 *   `children` and the raw string in `tooltip` so the reveal has something to show.
 *
 * The host must constrain the width (flex/grid child with `min-w-0`, a fixed
 * width, a `max-w-*` cap) — this only owns the clip, the measure and the reveal.
 *
 * `plain` drops the reveal entirely — clip only, no tooltip. Use it where the
 * text is a *signpost* the user recognises and clicks (conversation history,
 * nav labels, account menu), not *data* they read or copy (tables, ids, emails).
 * A tooltip firing on every pass-through of a navigation list is noise. */
export function TruncatedText({
  children,
  tooltip,
  className,
  side = "top",
  render,
  plain = false,
}: {
  /** What is displayed inside the clipping element. */
  children: ReactNode;
  /** Full text for the reveal. Defaults to `children` when it is a plain string;
   *  required when `children` is markup. */
  tooltip?: string;
  className?: string;
  side?: React.ComponentProps<typeof TooltipContent>["side"];
  render?: RenderElement;
  /** Clip without any hover reveal — for navigation, not reference data. */
  plain?: boolean;
}) {
  const clipClasses = cn("block min-w-0 truncate", className);

  // Navigation context: just clip. No measure, no tooltip, no pointer noise.
  // Kept hook-free (an early return) so it stays cheap on long lists — the
  // reveal machinery lives in its own component below.
  if (plain) {
    if (render) {
      return cloneElement(render, { className: cn(render.props.className, clipClasses) }, children);
    }
    return <span className={clipClasses}>{children}</span>;
  }

  const label = tooltip ?? (typeof children === "string" ? children : undefined);
  return (
    <ClippedWithReveal clipClasses={clipClasses} label={label} side={side} render={render}>
      {children}
    </ClippedWithReveal>
  );
}

/** The reveal variant: measures overflow and shows the styled tooltip only when
 * the text is actually cut off. Split out so its hooks never sit behind the
 * `plain` early return (rules-of-hooks). */
function ClippedWithReveal({
  children,
  clipClasses,
  label,
  side,
  render,
}: {
  children: ReactNode;
  clipClasses: string;
  label: string | undefined;
  side: React.ComponentProps<typeof TooltipContent>["side"];
  render?: RenderElement;
}) {
  // Typed to the trigger's element contract; we only read the geometry props
  // (scrollWidth/clientWidth) that every HTMLElement carries.
  const ref = useRef<HTMLButtonElement>(null);
  const [clipped, setClipped] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      setClipped(el.scrollWidth > el.clientWidth);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      observer.disconnect();
    };
  }, [label]);

  return (
    <TooltipProvider delay={REVEAL_DELAY_MS}>
      <Tooltip>
        {/* Base UI merges this ref + className onto the `render` element (or the
            default span), so we avoid cloneElement and its ref-in-render lint. */}
        <TooltipTrigger ref={ref} className={clipClasses} render={render ?? <span />}>
          {children}
        </TooltipTrigger>
        {clipped && label !== undefined && <TooltipContent side={side}>{label}</TooltipContent>}
      </Tooltip>
    </TooltipProvider>
  );
}
