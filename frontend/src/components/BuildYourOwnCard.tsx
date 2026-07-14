import { ArrowUpRightIcon } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

/** Architecture docs are published to GitHub Pages, where the HTML renders as a
 * page instead of a source listing; this is the one place their public URL is
 * spelled out, so callers point at a block with a short relative path. */
const DOCS_BASE = "https://maestrokj.github.io/Achilles/architecture/modules/";

/** A quiet "extend here" slot: a dashed link-card that tells self-hosters they
 * can bring their own connector / tool / API client and sends them to the exact
 * design block on GitHub. Understated by default (dashed, muted), it fills in on
 * hover so it reads as actionable without competing with the real controls. */
export function BuildYourOwnCard({
  icon: Icon,
  title,
  description,
  /** Path under docs/architecture/modules/, including the block anchor. */
  doc,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  doc: string;
}) {
  const { t } = useTranslation();
  return (
    <a
      href={`${DOCS_BASE}${doc}`}
      target="_blank"
      rel="noreferrer noopener"
      className="group border-primary/30 bg-primary/[0.04] hover:border-primary/50 hover:bg-primary/[0.07] flex items-start gap-3.5 rounded-xl border border-dashed px-4 py-3.5 transition-colors"
    >
      <span className="bg-primary/10 text-primary ring-primary/20 grid size-9 shrink-0 place-items-center rounded-lg ring-1 transition-colors">
        <Icon aria-hidden="true" className="size-[1.15rem]" strokeWidth={1.75} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="font-heading text-foreground text-sm leading-snug font-medium">
          {title}
        </span>
        <span className="text-muted-foreground text-xs leading-relaxed">{description}</span>
      </div>
      <span className="text-primary flex shrink-0 items-center gap-1 self-center text-xs font-medium whitespace-nowrap">
        {t("common.buildYourOwn")}
        <ArrowUpRightIcon
          aria-hidden="true"
          strokeWidth={2}
          className="size-3.5 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
        />
      </span>
    </a>
  );
}
