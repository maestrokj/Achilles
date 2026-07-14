/** Sidebar catalogue of the admin shell — groups, sections and their
 * domain owners, per docs/architecture/modules/admin-panel/_wireframes/layout.html. */

import {
  BellIcon,
  BotIcon,
  ChartColumnIcon,
  CpuIcon,
  DatabaseIcon,
  HouseIcon,
  KeyRoundIcon,
  MessageSquareTextIcon,
  ScrollTextIcon,
  SettingsIcon,
  ShieldCheckIcon,
  UsersIcon,
  WaypointsIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react";

import type en from "@/i18n/locales/en";

type NavLabelKey = `admin.nav.${keyof (typeof en)["admin"]["nav"]}`;
type GroupLabelKey = `admin.groups.${keyof (typeof en)["admin"]["groups"]}`;

export interface AdminSectionDef {
  /** URL segment under /admin; "" is the index (Dashboard). */
  path: string;
  labelKey: NavLabelKey;
  /** Domain module that fronts the section — a proper noun, never translated. */
  owner: string;
  icon: LucideIcon;
  /** Audit Log is visible to the Owner role only. */
  ownerOnly?: boolean;
}

export interface AdminGroupDef {
  labelKey: GroupLabelKey;
  items: AdminSectionDef[];
}

export const ADMIN_NAV: AdminGroupDef[] = [
  {
    labelKey: "admin.groups.overview",
    items: [{ path: "", labelKey: "admin.nav.dashboard", owner: "Admin Panel", icon: HouseIcon }],
  },
  {
    labelKey: "admin.groups.management",
    items: [
      { path: "users", labelKey: "admin.nav.users", owner: "Auth & Security", icon: UsersIcon },
    ],
  },
  {
    labelKey: "admin.groups.security",
    items: [
      {
        path: "api-keys",
        labelKey: "admin.nav.apiKeys",
        owner: "Auth & Security",
        icon: KeyRoundIcon,
      },
      {
        path: "audit-log",
        labelKey: "admin.nav.auditLog",
        owner: "Auth & Security",
        icon: ScrollTextIcon,
        ownerOnly: true,
      },
      {
        path: "platform-acl",
        labelKey: "admin.nav.platformAcl",
        owner: "Auth & Security",
        icon: ShieldCheckIcon,
      },
    ],
  },
  {
    labelKey: "admin.groups.data",
    items: [
      {
        path: "harvester",
        labelKey: "admin.nav.harvester",
        owner: "Harvester",
        icon: DatabaseIcon,
      },
      {
        path: "knowledge-store",
        labelKey: "admin.nav.knowledgeStore",
        owner: "Knowledge Store",
        icon: WaypointsIcon,
      },
    ],
  },
  {
    labelKey: "admin.groups.ai",
    items: [
      { path: "ai-models", labelKey: "admin.nav.aiModels", owner: "AI Foundation", icon: CpuIcon },
      { path: "agents", labelKey: "admin.nav.agents", owner: "Agent Engine", icon: BotIcon },
      {
        path: "ai-usage",
        labelKey: "admin.nav.aiUsage",
        owner: "AI Foundation",
        icon: ChartColumnIcon,
      },
      {
        path: "ai-prompt",
        labelKey: "admin.nav.aiPrompt",
        owner: "AI Foundation",
        icon: MessageSquareTextIcon,
      },
      { path: "ai-tools", labelKey: "admin.nav.aiTools", owner: "AI Foundation", icon: WrenchIcon },
    ],
  },
  {
    labelKey: "admin.groups.settings",
    items: [
      { path: "platform", labelKey: "admin.nav.platform", owner: "Core", icon: SettingsIcon },
      {
        path: "notifications",
        labelKey: "admin.nav.notifications",
        owner: "Notifications",
        icon: BellIcon,
      },
    ],
  },
];
