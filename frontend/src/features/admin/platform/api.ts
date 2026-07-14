/** Calls to /admin/settings and /platform/branding (admin/routes.py). */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";

import type {
  Branding,
  MattermostSettings,
  MattermostSettingsPatch,
  MattermostTestResult,
  PlatformSettings,
  PlatformSettingsPatch,
  SlackSettings,
  SlackSettingsPatch,
  SlackTestResult,
  SmtpSettings,
  SmtpSettingsPatch,
  SmtpTestResult,
  TelegramSettings,
  TelegramSettingsPatch,
  TelegramTestResult,
} from "./types";

const BRANDING_STALE_MS = 5 * 60_000;

export const platformKeys = {
  settings: ["platform", "settings"] as const,
  branding: ["platform", "branding"] as const,
  slack: ["platform", "slack"] as const,
  telegram: ["platform", "telegram"] as const,
  mattermost: ["platform", "mattermost"] as const,
  smtp: ["platform", "smtp"] as const,
};

export function getPlatformSettings(): Promise<PlatformSettings> {
  return api.get("admin/settings").json<PlatformSettings>();
}

export function patchPlatformSettings(patch: PlatformSettingsPatch): Promise<PlatformSettings> {
  return api.patch("admin/settings", { json: patch }).json<PlatformSettings>();
}

export function getSmtpSettings(): Promise<SmtpSettings> {
  return api.get("admin/smtp").json<SmtpSettings>();
}

export function patchSmtpSettings(patch: SmtpSettingsPatch): Promise<SmtpSettings> {
  return api.patch("admin/smtp", { json: patch }).json<SmtpSettings>();
}

export function testSmtpConnection(): Promise<SmtpTestResult> {
  return api.post("admin/smtp/test").json<SmtpTestResult>();
}

export function getSlackSettings(): Promise<SlackSettings> {
  return api.get("admin/slack").json<SlackSettings>();
}

export function patchSlackSettings(patch: SlackSettingsPatch): Promise<SlackSettings> {
  return api.patch("admin/slack", { json: patch }).json<SlackSettings>();
}

export function testSlackConnection(): Promise<SlackTestResult> {
  return api.post("admin/slack/test").json<SlackTestResult>();
}

export function getTelegramSettings(): Promise<TelegramSettings> {
  return api.get("admin/telegram").json<TelegramSettings>();
}

export function patchTelegramSettings(patch: TelegramSettingsPatch): Promise<TelegramSettings> {
  return api.patch("admin/telegram", { json: patch }).json<TelegramSettings>();
}

export function testTelegramConnection(): Promise<TelegramTestResult> {
  return api.post("admin/telegram/test").json<TelegramTestResult>();
}

export function getMattermostSettings(): Promise<MattermostSettings> {
  return api.get("admin/mattermost").json<MattermostSettings>();
}

export function patchMattermostSettings(
  patch: MattermostSettingsPatch,
): Promise<MattermostSettings> {
  return api.patch("admin/mattermost", { json: patch }).json<MattermostSettings>();
}

export function testMattermostConnection(): Promise<MattermostTestResult> {
  return api.post("admin/mattermost/test").json<MattermostTestResult>();
}

function getBranding(): Promise<Branding> {
  return api.get("platform/branding").json<Branding>();
}

/** The org branding query every surface shares (BrandMark, DisplayPrefs). */
export function useBranding() {
  return useQuery({
    queryKey: platformKeys.branding,
    queryFn: getBranding,
    staleTime: BRANDING_STALE_MS,
  });
}
