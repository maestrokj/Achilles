import { ArrowLeftIcon } from "lucide-react";
import { Link } from "react-router-dom";

const CLASSES =
  "text-muted-foreground hover:text-foreground inline-flex w-fit items-center gap-1.5 text-sm transition-colors";

/** A quiet "back" affordance sitting above a detail-page header: left arrow +
 * the parent screen's name, muted until hovered. Navigates via `to`, or steps
 * back inside the current page via `onClick` (e.g. a wizard's previous step). */
export function BackLink({
  to,
  onClick,
  label,
}: {
  to?: string;
  onClick?: () => void;
  label: string;
}) {
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={CLASSES}>
        <ArrowLeftIcon className="size-4" aria-hidden="true" />
        {label}
      </button>
    );
  }
  return (
    <Link to={to ?? ""} className={CLASSES}>
      <ArrowLeftIcon className="size-4" aria-hidden="true" />
      {label}
    </Link>
  );
}
