import { useQuery } from "@tanstack/react-query";
import { CheckIcon, CopyIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useParams } from "react-router-dom";

import { BackLink } from "@/components/BackLink";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { issueLinkCode } from "./api";
import { PLATFORM_NAMES, toPlatform } from "./link-platforms";

/** The countdown shows whole minutes, so a coarse tick is enough — no need to
 * re-render the page every second for a value that changes once a minute. */
const TICK_MS = 30_000;

/** /link/:platform — issue a short-lived code the user hands to the bot in DM.
 * Slack and Telegram share this screen. Wireframe:
 * auth-security/_wireframes/messenger-link.html. */
export function LinkPage() {
  const { t } = useTranslation();
  const params = useParams<{ platform: string }>();
  const platform = toPlatform(params.platform);
  const [copied, setCopied] = useState(false);
  // A ticking clock; `remaining` is derived from it against when the code arrived.
  const [now, setNow] = useState(() => Date.now());

  const code = useQuery({
    queryKey: ["account", "link-code", platform],
    queryFn: () => issueLinkCode(platform ?? ""),
    // The code is a live credential — never served from cache across visits.
    enabled: platform !== null,
    gcTime: 0,
    staleTime: 0,
  });

  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now());
    }, TICK_MS);
    return () => {
      clearInterval(timer);
    };
  }, []);

  const elapsed = Math.max(0, Math.floor((now - code.dataUpdatedAt) / 1000));
  const remaining = code.data ? Math.max(0, code.data.expires_in_seconds - elapsed) : 0;
  const minutes = Math.ceil(remaining / 60);
  const expired = code.data !== undefined && remaining <= 0;

  const copyCode = () => {
    if (!code.data) return;
    void navigator.clipboard.writeText(code.data.code);
    setCopied(true);
    setTimeout(() => {
      setCopied(false);
    }, 1500);
  };

  // An unknown platform in the URL is not a linkable surface — back to profile.
  // (After the hooks, so hook order stays stable across renders.)
  if (platform === null) return <Navigate to="/account" replace />;
  const platformName = PLATFORM_NAMES[platform];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-lg flex-col gap-4 px-6 py-8">
        <div className="flex flex-col gap-1">
          <BackLink to="/account" label={t("account.title")} />
          <h1 className="text-2xl font-semibold tracking-tight">
            {t("account.link.title", { platform: platformName })}
          </h1>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">{t("account.link.codeTitle")}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {code.isLoading && (
              <div className="bg-muted/40 flex flex-col items-center gap-3 rounded-lg border py-8">
                <Skeleton className="h-9 w-44" />
                <Skeleton className="h-3 w-24" />
              </div>
            )}
            {code.isError && <p className="text-destructive text-sm">{t("account.link.failed")}</p>}
            {code.data && (
              <>
                <button
                  type="button"
                  onClick={copyCode}
                  disabled={expired}
                  aria-label={t("account.link.copyCode")}
                  className="bg-muted/40 hover:bg-muted/70 disabled:hover:bg-muted/40 flex w-full cursor-pointer flex-col items-center gap-2 rounded-lg border px-4 py-6 text-center transition-colors disabled:cursor-default disabled:opacity-50"
                >
                  <code className="font-mono text-3xl font-semibold tracking-[0.35em] break-all">
                    {code.data.code}
                  </code>
                  <span className="text-muted-foreground text-xs">
                    {expired ? t("account.link.expired") : t("account.link.validFor", { minutes })}
                  </span>
                </button>
                <div className="flex flex-wrap items-center gap-3">
                  {expired ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={code.isFetching}
                      onClick={() => {
                        void code.refetch();
                      }}
                    >
                      {t("account.link.regenerate")}
                    </Button>
                  ) : (
                    <>
                      <span className="text-muted-foreground flex items-center gap-2 text-xs">
                        <span className="relative flex size-2" aria-hidden>
                          <span className="bg-primary/60 absolute inline-flex h-full w-full animate-ping rounded-full" />
                          <span className="bg-primary relative inline-flex size-2 rounded-full" />
                        </span>
                        {t("account.link.waiting")}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        className="ml-auto shrink-0"
                        onClick={copyCode}
                      >
                        {copied ? (
                          <CheckIcon className="size-4" />
                        ) : (
                          <CopyIcon className="size-4" />
                        )}
                        {copied ? t("account.link.copied") : t("account.link.copy")}
                      </Button>
                    </>
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-semibold">{t("account.link.howTitle")}</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="text-muted-foreground flex list-decimal flex-col gap-2 pl-5 text-sm">
              <li>{t("account.link.step1", { platform: platformName })}</li>
              <li>{t("account.link.step2")}</li>
            </ol>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
