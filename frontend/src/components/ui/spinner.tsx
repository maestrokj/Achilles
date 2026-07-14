import { cn } from "@/lib/utils";

/** The one loading circle — size via className (`size-6` by default). */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "border-muted-foreground/40 border-t-primary size-6 animate-spin rounded-full border-2",
        className,
      )}
      aria-hidden="true"
    />
  );
}
