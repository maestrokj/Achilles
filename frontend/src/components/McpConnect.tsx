import { ArrowUpRightIcon, ChevronDownIcon, PlugZapIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { CopyButton } from "@/components/ui/copy-button";
import { cn } from "@/lib/utils";

/** The MCP module's published design doc — where a curious admin or user goes to
 * understand what this endpoint is and how any client speaks to it. */
const MCP_DOCS_URL = "https://maestrokj.github.io/Achilles/architecture/modules/mcp/index.html";

/** A stand-in shown until the caller passes a real key (keys surface once, on
 * creation). An explicit angle-bracket placeholder reads as "fill me in" rather
 * than looking like a truncated real key. */
const KEY_PLACEHOLDER = "<your-api-key>";

/** One "field: value" line of the connection spec — the client-neutral facts a
 * user copies into whatever MCP client they run. */
function SpecRow({ label, value, copy }: { label: string; value: string; copy: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-muted-foreground w-24 shrink-0 text-xs">{label}</span>
      <code className="text-foreground min-w-0 flex-1 truncate font-mono text-xs" title={value}>
        {value}
      </code>
      <CopyButton text={copy} />
    </div>
  );
}

/** A quiet, collapsed-by-default disclosure that explains how to point any
 * MCP-capable AI assistant at this Achilles instance. It leads with the raw
 * connection parameters (transport · endpoint · auth header) so the answer reads
 * as client-agnostic, then shows the Claude Code one-liner as *one* example.
 *
 * Lives in two homes: the admin's MCP section (governance context) and a user's
 * API-keys card (self-service). Pass `apiKey` right after a key is minted so the
 * example command comes out ready to run; otherwise it falls back to `ach_…`. */
export function McpConnect({ apiKey, className }: { apiKey?: string; className?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  // In production the MCP endpoint sits behind the same origin as the app; in dev
  // this shows the dev origin, which is honest enough for a copy-me address.
  const endpoint = `${window.location.origin}/mcp`;
  const key = apiKey ?? KEY_PLACEHOLDER;
  const authHeader = `Authorization: Bearer ${key}`;
  const command = `claude mcp add --transport http achilles ${endpoint} \\\n  --header "${authHeader}"`;

  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
        }}
        className="text-primary hover:text-primary/80 group flex w-full items-center gap-2 text-xs font-medium transition-colors"
      >
        <PlugZapIcon className="size-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
        {t("common.mcpConnect.trigger")}
        <ChevronDownIcon
          aria-hidden="true"
          className={cn(
            "ml-auto size-3.5 shrink-0 transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="border-border/60 bg-muted/30 mt-3 flex flex-col gap-4 rounded-lg border p-4">
          <div className="flex flex-col gap-2.5">
            <div className="flex items-center gap-3">
              <span className="text-muted-foreground w-24 shrink-0 text-xs">
                {t("common.mcpConnect.transport")}
              </span>
              <code className="text-foreground font-mono text-xs">HTTP</code>
            </div>
            <SpecRow label={t("common.mcpConnect.endpoint")} value={endpoint} copy={endpoint} />
            <SpecRow label={t("common.mcpConnect.auth")} value={authHeader} copy={authHeader} />
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground/80 text-[0.7rem] font-medium tracking-wide uppercase">
                {t("common.mcpConnect.example")}
              </span>
              <span className="bg-border/60 h-px flex-1" aria-hidden="true" />
            </div>
            <div className="relative">
              <pre className="bg-background/70 border-border/60 text-foreground overflow-x-auto rounded-md border py-3 pr-11 pl-3.5 font-mono text-xs leading-relaxed">
                <code>{command}</code>
              </pre>
              <CopyButton
                text={command}
                className="bg-background/80 hover:bg-background absolute top-2 right-2 after:-inset-1"
              />
            </div>
            <p className="text-muted-foreground/80 text-[0.7rem] leading-relaxed">
              {apiKey ? t("common.mcpConnect.readyNote") : t("common.mcpConnect.placeholderNote")}
            </p>
          </div>

          <a
            href={MCP_DOCS_URL}
            target="_blank"
            rel="noreferrer noopener"
            className="text-primary group inline-flex items-center gap-1 self-start text-xs font-medium"
          >
            {t("common.mcpConnect.docs")}
            <ArrowUpRightIcon
              aria-hidden="true"
              strokeWidth={2}
              className="size-3.5 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
            />
          </a>
        </div>
      )}
    </div>
  );
}
