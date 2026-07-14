import type { ComponentType } from "react";
import { createBrowserRouter, Navigate, type RouteObject } from "react-router-dom";

import { Spinner } from "@/components/ui/spinner";

import { AdminLayout } from "@/features/admin/AdminLayout";
import { ADMIN_NAV } from "@/features/admin/nav";
import { RoleGuard } from "@/features/admin/RoleGuard";
import { SectionStub } from "@/features/admin/sections/SectionStub";
import { AppLayout } from "@/features/app-shell/AppLayout";
import { NotFoundPage } from "@/features/app-shell/NotFoundPage";
import { RootRedirect } from "@/features/app-shell/RootRedirect";
import { RootShell } from "@/features/app-shell/RootShell";
import { LoginPage } from "@/features/auth/LoginPage";

/** Wrap a named page module as a lazy route: its chunk downloads on first
 * navigation, which drives useNavigation → the RouteProgress bar. Typed so
 * `name` must be a component export of the imported module. */
function lazy<K extends string>(importer: () => Promise<Record<K, ComponentType>>, name: K) {
  return async () => ({ Component: (await importer())[name] });
}

// Pages reached from more than one route share a single lazy loader (and thus
// one chunk) instead of duplicating the import.
const chat = lazy(() => import("@/features/chat/ChatPage"), "ChatPage");
const agentEditor = lazy(() => import("@/features/agents/AgentEditorPage"), "AgentEditorPage");

/** Admin pages implemented so far; the rest render the stub and are
 * replaced stage by stage. */
const adminPageRoutes: RouteObject[] = [
  {
    index: true,
    lazy: lazy(() => import("@/features/admin/dashboard/DashboardPage"), "DashboardPage"),
  },
  {
    path: "agents",
    lazy: lazy(() => import("@/features/agents/admin/AdminAgentsPage"), "AdminAgentsPage"),
  },
  {
    path: "agents/:agentId",
    lazy: lazy(() => import("@/features/agents/admin/AdminAgentCardPage"), "AdminAgentCardPage"),
  },
  {
    path: "platform",
    lazy: lazy(
      () => import("@/features/admin/platform/PlatformSettingsPage"),
      "PlatformSettingsPage",
    ),
  },
  { path: "users", lazy: lazy(() => import("@/features/admin/users/UsersPage"), "UsersPage") },
  {
    path: "users/import",
    lazy: lazy(() => import("@/features/admin/users/BulkInvitePage"), "BulkInvitePage"),
  },
  {
    path: "users/:userId",
    lazy: lazy(() => import("@/features/admin/users/UserCardPage"), "UserCardPage"),
  },
  {
    path: "api-keys",
    lazy: lazy(() => import("@/features/admin/security/ApiKeysPage"), "ApiKeysPage"),
  },
  {
    path: "audit-log",
    lazy: lazy(() => import("@/features/admin/security/AuditLogPage"), "AuditLogPage"),
  },
  {
    path: "ai-models",
    lazy: lazy(() => import("@/features/admin/ai/AiModelsPage"), "AiModelsPage"),
  },
  { path: "ai-usage", lazy: lazy(() => import("@/features/admin/ai/UsagePage"), "UsagePage") },
  {
    path: "ai-usage/:userId",
    lazy: lazy(() => import("@/features/admin/ai/UsageDetailPage"), "UsageDetailPage"),
  },
  {
    path: "ai-prompt",
    lazy: lazy(() => import("@/features/admin/ai/AiBehaviorPage"), "AiBehaviorPage"),
  },
  { path: "ai-tools", lazy: lazy(() => import("@/features/admin/ai/ToolsPage"), "ToolsPage") },
  {
    path: "harvester",
    lazy: lazy(() => import("@/features/admin/harvester/HarvesterPage"), "HarvesterPage"),
  },
  {
    path: "harvester/sources/:sourceId",
    lazy: lazy(() => import("@/features/admin/harvester/SourceCardPage"), "SourceCardPage"),
  },
  {
    path: "knowledge-store",
    lazy: lazy(() => import("@/features/admin/knowledge/KnowledgeStorePage"), "KnowledgeStorePage"),
  },
  {
    path: "notifications",
    lazy: lazy(
      () => import("@/features/admin/notifications/NotificationsPage"),
      "NotificationsPage",
    ),
  },
  {
    path: "notifications/inbox",
    lazy: lazy(() => import("@/features/admin/notifications/AdminInboxPage"), "AdminInboxPage"),
  },
];

/** Derived from the routes above — a nav section without a page gets the stub. */
const implementedAdmin = new Set(adminPageRoutes.map((route) => route.path?.split("/")[0] ?? ""));

const sectionRoutes = ADMIN_NAV.flatMap((group) =>
  group.items
    .filter((section) => !implementedAdmin.has(section.path))
    .map((section) => ({
      index: section.path === "",
      path: section.path === "" ? undefined : section.path,
      element: <SectionStub group={group} section={section} />,
    })),
);

export const router = createBrowserRouter([
  {
    // Pathless root layout: the route-transition progress bar mounts once here,
    // covering every surface (auth screens included).
    element: <RootShell />,
    // Shown while lazy route chunks for the initial URL download on a cold
    // deep-link (no previous page for useNavigation to bridge from).
    hydrateFallbackElement: (
      <div className="bg-background flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    ),
    children: [
      { path: "/", element: <RootRedirect /> },
      { path: "/login", element: <LoginPage /> },
      {
        path: "/setup",
        lazy: lazy(() => import("@/features/auth/SetupWizardPage"), "SetupWizardPage"),
      },
      // Public letter landings (email/_workzone/templates.html links).
      {
        path: "/forgot-password",
        lazy: lazy(() => import("@/features/auth/ForgotPasswordPage"), "ForgotPasswordPage"),
      },
      {
        path: "/reset-password/:token",
        lazy: lazy(() => import("@/features/auth/ResetPasswordPage"), "ResetPasswordPage"),
      },
      {
        path: "/invite/:token",
        lazy: lazy(() => import("@/features/auth/InviteAcceptPage"), "InviteAcceptPage"),
      },
      {
        path: "/chat",
        element: <AppLayout />,
        children: [
          { index: true, lazy: chat },
          { path: ":conversationId", lazy: chat },
        ],
      },
      {
        path: "/agents",
        element: <AppLayout />,
        children: [
          {
            index: true,
            lazy: lazy(() => import("@/features/agents/MyAgentsPage"), "MyAgentsPage"),
          },
          { path: "new", lazy: agentEditor },
          { path: ":agentId", lazy: agentEditor },
        ],
      },
      {
        path: "/inbox",
        element: <AppLayout />,
        children: [
          {
            index: true,
            lazy: lazy(() => import("@/features/notifications/InboxPage"), "InboxPage"),
          },
          {
            path: "settings",
            lazy: lazy(
              () => import("@/features/notifications/NotificationSettingsPage"),
              "NotificationSettingsPage",
            ),
          },
        ],
      },
      {
        path: "/account",
        element: <AppLayout />,
        children: [
          {
            index: true,
            lazy: lazy(() => import("@/features/account/AccountPage"), "AccountPage"),
          },
          {
            path: "sessions",
            lazy: lazy(() => import("@/features/account/SessionsPage"), "SessionsPage"),
          },
        ],
      },
      {
        path: "/link",
        element: <AppLayout />,
        children: [
          // Bare /link carries no platform — send it back to the profile rather
          // than rendering an empty shell (the old Slack-link bookmark hit here).
          { index: true, element: <Navigate to="/account" replace /> },
          {
            path: ":platform",
            lazy: lazy(() => import("@/features/account/LinkPage"), "LinkPage"),
          },
        ],
      },
      {
        path: "/admin",
        element: <RoleGuard />,
        children: [
          {
            element: <AdminLayout />,
            // Unknown /admin subpaths render the 404 inside the admin shell,
            // still behind RoleGuard — not the bare root catch-all, which would
            // bypass the auth gate.
            children: [
              ...sectionRoutes,
              ...adminPageRoutes,
              { path: "*", element: <NotFoundPage /> },
            ],
          },
        ],
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
