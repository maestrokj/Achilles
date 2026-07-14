import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

/** A big-number tile in a flex row — label, value, muted hint, optional ceiling bar. */
export function StatTile({
  value,
  label,
  hint,
  progress,
  tone,
}: {
  value: string;
  label: string;
  hint?: string;
  /** Percent of the ceiling already used; renders a token-colored bar when set. */
  progress?: number;
  /** "warning" colors the value — a threshold has been crossed. */
  tone?: "warning";
}) {
  return (
    <Card className="flex-1 shadow-2xs">
      <CardContent className="flex flex-col gap-1">
        <span className="text-muted-foreground text-xs">{label}</span>
        <span
          className={cn(
            "text-2xl font-semibold tabular-nums",
            tone === "warning" && "text-warning",
          )}
        >
          {value}
        </span>
        {progress !== undefined && (
          <Progress
            value={Math.min(progress, 100)}
            className={cn(
              "mt-1",
              progress >= 100 && "[&_[data-slot=progress-indicator]]:bg-destructive",
            )}
          />
        )}
        {hint !== undefined && <span className="text-muted-foreground text-xs">{hint}</span>}
      </CardContent>
    </Card>
  );
}
