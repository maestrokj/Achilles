import { cn } from "@/lib/utils";

const TONES = {
  destructive: "border-destructive/30 bg-destructive/10 text-destructive",
  warning: "border-warning/30 bg-warning/10 text-warning",
} as const;

/** The inline notice above a form: admin pause, missing model, budget, errors.
 * `compact` is the in-thread variant (chat failure plaques). */
export function Banner({
  tone,
  compact,
  children,
}: {
  tone: keyof typeof TONES;
  compact?: boolean;
  children: React.ReactNode;
}) {
  return (
    <p
      className={cn(
        "rounded-lg border",
        compact ? "px-3 py-2 text-xs" : "px-4 py-2.5 text-sm",
        TONES[tone],
      )}
    >
      {children}
    </p>
  );
}
