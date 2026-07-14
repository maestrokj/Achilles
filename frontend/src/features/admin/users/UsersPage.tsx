import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { platformKeys, getPlatformSettings } from "@/features/admin/platform/api";

import { IdentityMappingTab } from "./IdentityMappingTab";
import { InviteDialog } from "./InviteDialog";
import { InvitesTab } from "./InvitesTab";
import { UsersListTab } from "./UsersListTab";

/** Admin · Users: list / invites / identity mapping in three tabs.
 * Wireframe: admin-panel/_wireframes/users.html. */
export function UsersPage() {
  const { t } = useTranslation();
  const [inviteOpen, setInviteOpen] = useState(false);
  // The bulk-import wizard lands here on ?tab=invites so the sent invites are in view.
  const [searchParams] = useSearchParams();
  const initialTab = searchParams.get("tab") ?? "list";
  // The invite button obeys the SMTP gate; the flag rides on /admin/settings.
  const settings = useQuery({ queryKey: platformKeys.settings, queryFn: getPlatformSettings });
  const smtpConfigured = settings.data?.smtp_configured ?? false;

  // The bulk import wizard sits behind the same SMTP gate as single invites.
  const actionButtons = (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={!smtpConfigured}
        render={smtpConfigured ? <Link to="/admin/users/import" /> : undefined}
      >
        {t("admin.users.import")}
      </Button>
      <Button
        size="sm"
        disabled={!smtpConfigured}
        onClick={() => {
          setInviteOpen(true);
        }}
      >
        {t("admin.users.invite")}
      </Button>
    </div>
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.nav.users")}</h1>
        {smtpConfigured ? (
          actionButtons
        ) : (
          <TooltipProvider>
            <Tooltip>
              {/* A disabled button swallows hover — the tooltip needs the wrapper. */}
              <TooltipTrigger render={<span />}>{actionButtons}</TooltipTrigger>
              <TooltipContent>{t("admin.users.smtpGate")}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>

      <Tabs defaultValue={initialTab}>
        <TabsList variant="line" className="border-border w-full justify-start border-b pb-1">
          <TabsTrigger value="list" className="flex-none px-2.5">
            {t("admin.users.tabs.list")}
          </TabsTrigger>
          <TabsTrigger value="invites" className="flex-none px-2.5">
            {t("admin.users.tabs.invites")}
          </TabsTrigger>
          <TabsTrigger value="identity" className="flex-none px-2.5">
            {t("admin.users.tabs.identity")}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="list">
          <UsersListTab />
        </TabsContent>
        <TabsContent value="invites">
          <InvitesTab smtpConfigured={smtpConfigured} />
        </TabsContent>
        <TabsContent value="identity">
          <IdentityMappingTab />
        </TabsContent>
      </Tabs>

      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />
    </div>
  );
}
