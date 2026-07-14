import { useTranslation } from "react-i18next";

import { InDevelopmentBadge } from "@/components/InDevelopmentBadge";
import { isOwner } from "@/features/auth/roles";
import { useSession } from "@/features/auth/session-context";

import { ForbiddenPage } from "../ForbiddenPage";
import type { AdminGroupDef, AdminSectionDef } from "../nav";

interface SectionStubProps {
  group: AdminGroupDef;
  section: AdminSectionDef;
}

/** Placeholder for a not-yet-implemented section: crumb, "coming soon" badge and
 * the badge of the domain module that owns the section (as on the wireframes). */
export function SectionStub({ group, section }: SectionStubProps) {
  const { t } = useTranslation();
  const session = useSession();
  const role = session.status === "authenticated" ? session.user.role : "member";

  if (section.ownerOnly && !isOwner(role)) return <ForbiddenPage />;

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
      <div className="bg-muted text-muted-foreground ring-border flex size-12 items-center justify-center rounded-full ring-1">
        <section.icon className="size-5" aria-hidden="true" />
      </div>
      <div className="flex flex-col items-center gap-1">
        <h1 className="text-foreground/80 text-2xl font-semibold tracking-tight">
          {t(section.labelKey)}
        </h1>
        <p className="text-muted-foreground text-xs">
          {t(group.labelKey)} · {section.owner}
        </p>
      </div>
      <InDevelopmentBadge />
    </div>
  );
}
