/** Calls to the knowledge-store admin backend (/admin/knowledge/*). */

import { api } from "@/api/client";

import type {
  BackupSettings,
  BackupSettingsPatchBody,
  BackupSnapshot,
  CurationStatus,
  KnowledgeMetrics,
} from "./types";

export const knowledgeKeys = {
  metrics: (sourceId: number | null) => ["admin", "knowledge", "metrics", sourceId] as const,
  curation: ["admin", "knowledge", "curation"] as const,
  backupSettings: ["admin", "knowledge", "backup-settings"] as const,
  backups: ["admin", "knowledge", "backups"] as const,
};

export function getMetrics(sourceId: number | null): Promise<KnowledgeMetrics> {
  return api
    .get("admin/knowledge/metrics", {
      searchParams: sourceId === null ? {} : { source_id: sourceId },
    })
    .json<KnowledgeMetrics>();
}

export function getCurationStatus(): Promise<CurationStatus> {
  return api.get("admin/knowledge/curation").json<CurationStatus>();
}

export function startCuration(): Promise<{ run_id: number }> {
  return api.post("admin/knowledge/reindex").json<{ run_id: number }>();
}

export function cancelCuration(runId: number): Promise<{ run_id: number }> {
  return api.post(`admin/knowledge/curation/${String(runId)}/cancel`).json<{ run_id: number }>();
}

export function getBackupSettings(): Promise<BackupSettings> {
  return api.get("admin/knowledge/backup-settings").json<BackupSettings>();
}

export function patchBackupSettings(body: BackupSettingsPatchBody): Promise<BackupSettings> {
  return api.patch("admin/knowledge/backup-settings", { json: body }).json<BackupSettings>();
}

export function listBackups(): Promise<BackupSnapshot[]> {
  return api.get("admin/knowledge/backups").json<BackupSnapshot[]>();
}

export function startRestore(snapshotId: number): Promise<{ snapshot_id: number }> {
  return api
    .post("admin/knowledge/restore", { json: { snapshot_id: snapshotId } })
    .json<{ snapshot_id: number }>();
}
