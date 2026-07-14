import { APP_NAME } from "@/constants";
import { useBranding } from "@/features/admin/platform/api";
import { cn } from "@/lib/utils";

/** The product mark: logo square + org name. Reads the cached branding query;
 * APP_NAME covers the first paint.
 * `compact` keeps the square only — collapsed-sidebar rails. */
export function BrandMark({
  compact = false,
  className,
}: {
  compact?: boolean;
  className?: string;
}) {
  const { data } = useBranding();
  const name = data?.org_name ?? APP_NAME;

  return (
    <span className={cn("flex items-center gap-2 font-semibold", className)}>
      {data?.org_logo_url ? (
        <img src={data.org_logo_url} alt="" className="size-7 rounded-md object-cover" />
      ) : (
        <span className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-md text-sm font-bold">
          {name.charAt(0).toUpperCase()}
        </span>
      )}
      {!compact && name}
    </span>
  );
}
