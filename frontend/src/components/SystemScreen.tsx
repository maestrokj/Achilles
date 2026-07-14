import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/** Full-screen platform stub of system-screens.html — the shared scaffold behind
 * 404 / 500 / 403 / maintenance: a centered code-or-icon, title, body, and an
 * action slot. Each screen supplies its data and its buttons via `children`. */
export function SystemScreen({
  code,
  icon: Icon,
  title,
  body,
  children,
}: {
  code?: string;
  icon?: LucideIcon;
  title: string;
  body: string;
  children?: ReactNode;
}) {
  return (
    <div className="bg-background text-foreground flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      {code !== undefined && (
        <div className="text-muted-foreground/60 text-5xl font-bold tabular-nums">{code}</div>
      )}
      {Icon && <Icon className="text-muted-foreground size-10" aria-hidden="true" />}
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="text-muted-foreground max-w-sm text-sm">{body}</p>
      {children}
    </div>
  );
}
