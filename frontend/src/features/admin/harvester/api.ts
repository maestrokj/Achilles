/** Calls to the harvester backend (/sources/*). */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { api } from "@/api/client";
import { toastApiError } from "@/api/errors";

import type {
  CatalogItem,
  ConnectorType,
  DeadLetter,
  Diagnosis,
  Source,
  SourceCreateBody,
  SourcePatchBody,
  SyncRun,
  WebhookSecret,
} from "./types";

export const harvesterKeys = {
  sources: ["admin", "harvester", "sources"] as const,
  source: (id: number) => ["admin", "harvester", "sources", id] as const,
  connectors: ["admin", "harvester", "connectors"] as const,
  catalog: (id: number) => ["admin", "harvester", "catalog", id] as const,
  runs: (id: number) => ["admin", "harvester", "runs", id] as const,
  deadLetters: (id: number) => ["admin", "harvester", "dlq", id] as const,
};

export function listSources(): Promise<Source[]> {
  return api.get("sources").json<Source[]>();
}

export function getSource(id: number): Promise<Source> {
  return api.get(`sources/${String(id)}`).json<Source>();
}

export function listConnectorTypes(): Promise<ConnectorType[]> {
  return api.get("sources/connectors").json<ConnectorType[]>();
}

export function createSource(body: SourceCreateBody): Promise<Source> {
  return api.post("sources", { json: body }).json<Source>();
}

export function patchSource(id: number, body: SourcePatchBody): Promise<Source> {
  return api.patch(`sources/${String(id)}`, { json: body }).json<Source>();
}

/** The source-card save mutation: PATCH + refresh the card + saved/failed toasts. */
export function usePatchSource(sourceId: number) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SourcePatchBody) => patchSource(sourceId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harvesterKeys.source(sourceId) });
      toast.success(t("admin.platform.saved"));
    },
    onError: (error) => void toastApiError(error, t("admin.platform.saveFailed")),
  });
}

export async function deleteSource(id: number, confirm: string): Promise<void> {
  await api.delete(`sources/${String(id)}`, { json: { confirm } });
}

/** Rotate the webhook signing secret — the plaintext is returned once. */
export function rotateWebhookSecret(id: number): Promise<WebhookSecret> {
  return api.post(`sources/${String(id)}/webhook/rotate`).json<WebhookSecret>();
}

/** The live container catalog of a connected source — the "selected only" picker. */
export async function getCatalog(id: number): Promise<CatalogItem[]> {
  const out = await api.get(`sources/${String(id)}/catalog`).json<{ items: CatalogItem[] }>();
  return out.items;
}

export function testConnection(id: number): Promise<Diagnosis> {
  return api.post(`sources/${String(id)}/test-connection`).json<Diagnosis>();
}

export function startSync(id: number, mode: string): Promise<{ run_id: number }> {
  return api.post(`sources/${String(id)}/sync`, { json: { mode } }).json<{ run_id: number }>();
}

export function syncAll(): Promise<{ run_ids: number[] }> {
  return api.post("sources/sync").json<{ run_ids: number[] }>();
}

export function cancelSync(id: number): Promise<{ run_id: number }> {
  return api.post(`sources/${String(id)}/cancel`).json<{ run_id: number }>();
}

export function listRuns(id: number): Promise<SyncRun[]> {
  return api.get(`sources/${String(id)}/runs`).json<SyncRun[]>();
}

export function listDeadLetters(id: number): Promise<DeadLetter[]> {
  return api.get(`sources/${String(id)}/dead-letters`).json<DeadLetter[]>();
}

export function retryDeadLetters(id: number): Promise<{ run_id: number }> {
  return api.post(`sources/${String(id)}/dead-letters/retry`).json<{ run_id: number }>();
}
