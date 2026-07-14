import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** Loading placeholder in the shape of a page frame: a heading bar over a body
 * block, centered at the same max-width as the content it replaces so nothing
 * shifts when data arrives. Used on detail screens whose title is the loaded
 * entity's name and can't be shown until fetched. Default frame matches the
 * common detail width (max-w-3xl); pass `className` to override width/padding. */
export function PageSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("mx-auto flex w-full max-w-3xl flex-col gap-6", className)}>
      <Skeleton className="h-8 w-56" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
