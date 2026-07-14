import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DownloadIcon, FileTextIcon, UploadIcon, XIcon } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { apiErrorReason } from "@/api/errors";
import { BackLink } from "@/components/BackLink";
import { DataTable, TableFrame, TruncateCell } from "@/components/list-controls/DataTable";
import { SelectField } from "@/components/SelectField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { getPlatformSettings, platformKeys } from "@/features/admin/platform/api";
import { downloadBlob } from "@/lib/download";

import { bulkInvite } from "./api";
import { ROLES, roleLabel } from "./format";
import type { BulkRow, BulkRowStatus } from "./types";

const TEMPLATE_CSV = "anna@company.com,member\ndmitry@company.com,admin\nigor@company.com\n";

// Lightweight client gate: at least one address-shaped token before the dry-run.
// Not a full RFC check — the backend dry-run is the authority; this only stops
// a list of pure gibberish from reaching step 2.
const EMAIL_RE = /[^\s,;@]+@[^\s,;@]+\.[^\s,;@]+/;

const STATUS_TONE: Record<BulkRowStatus, "secondary" | "outline" | "destructive"> = {
  created: "secondary",
  conflict: "outline",
  duplicate: "outline",
  invalid: "destructive",
};

function downloadCsv(name: string, content: string) {
  downloadBlob(name, new Blob([content], { type: "text/csv" }));
}

function reportCsv(results: BulkRow[]): string {
  const lines = results.map(
    (row) => `${String(row.row)},${row.email},${row.status},${row.message ?? ""}`,
  );
  return `row,email,status,message\n${lines.join("\n")}\n`;
}

/** Bulk invite wizard (auth-security/_features/user-onboarding): step 1 —
 * a CSV file or a pasted list; step 2 — the dry-run report as a preview with
 * status counters; then the real 207 report as the summary. Skipped rows
 * (conflicts, duplicates, bad emails) never block the valid ones. */
export function BulkInvitePage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [defaultRole, setDefaultRole] = useState<(typeof ROLES)[number]>("member");
  const [preview, setPreview] = useState<BulkRow[] | null>(null);
  const [summary, setSummary] = useState<BulkRow[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<BulkRowStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Same SMTP gate as the single invite — letters are the only channel.
  const settings = useQuery({ queryKey: platformKeys.settings, queryFn: getPlatformSettings });
  const smtpConfigured = settings.data?.smtp_configured ?? false;

  // The pasted list travels as a file too — one backend contract for both inputs.
  const payload = useMemo(
    () => file ?? new File([pasted], "pasted.csv", { type: "text/csv" }),
    [file, pasted],
  );

  // A file is trusted to the dry-run; a pasted list must show at least one
  // address before we let the wizard advance.
  const pastedHasEmail = EMAIL_RE.test(pasted);
  const pastedInvalid = file === null && pasted.trim() !== "" && !pastedHasEmail;
  const canProceed = file !== null || pastedHasEmail;

  const dryRun = useMutation({
    // The role is passed explicitly so a step-2 change previews against the new
    // value immediately, without waiting for the state update to settle.
    mutationFn: (role: string) => bulkInvite({ file: payload, dryRun: true, defaultRole: role }),
    onSuccess: (report) => {
      setPreview(report.results);
      setStatusFilter(null);
      setError(null);
    },
    onError: async (err) => {
      setError(await apiErrorReason(err));
    },
  });
  const send = useMutation({
    mutationFn: () => bulkInvite({ file: payload, dryRun: false, defaultRole }),
    onSuccess: (report) => {
      setSummary(report.results);
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
    onError: async (err) => {
      setError(await apiErrorReason(err));
    },
  });

  const step: 1 | 2 = preview === null ? 1 : 2;
  const counts = useMemo(() => {
    const by: Record<BulkRowStatus, number> = { created: 0, conflict: 0, duplicate: 0, invalid: 0 };
    for (const row of preview ?? []) by[row.status] += 1;
    return by;
  }, [preview]);
  const visible = (preview ?? []).filter(
    (row) => statusFilter === null || row.status === statusFilter,
  );
  // Backend sends a stable token per skipped row; we localize the known ones.
  const rowMessages: Record<string, string> = {
    email: t("admin.users.bulk.rowMessages.email"),
    role: t("admin.users.bulk.rowMessages.role"),
    role_forbidden: t("admin.users.bulk.rowMessages.roleForbidden"),
    error: t("admin.users.bulk.rowMessages.error"),
  };

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      {/* Step 2 steps back to the upload form; step 1 (and the final summary)
       * leaves the wizard for the users list. */}
      {summary === null && step === 2 ? (
        <BackLink
          onClick={() => {
            setPreview(null);
            setError(null);
          }}
          label={t("admin.users.bulk.backToUpload")}
        />
      ) : (
        <BackLink to="/admin/users" label={t("admin.nav.users")} />
      )}
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.users.bulk.title")}</h1>
        <span className="text-muted-foreground text-sm tabular-nums">
          {t("admin.users.bulk.step", { step, total: 2 })}
        </span>
      </div>

      {!smtpConfigured && !settings.isPending && (
        <p className="text-muted-foreground rounded-xl border p-4 text-sm">
          {t("admin.users.smtpGate")}{" "}
          <Link
            to="/admin/platform#smtp"
            className="text-warning font-medium underline-offset-4 hover:underline"
          >
            {t("admin.users.smtpGateAction")}
          </Link>
        </p>
      )}

      {summary !== null ? (
        <BulkSummary results={summary} />
      ) : step === 1 ? (
        <div className="bg-card flex flex-col gap-4 rounded-xl border p-6 shadow-2xs">
          <p className="text-muted-foreground text-sm">{t("admin.users.bulk.uploadHint")}</p>

          <input
            ref={fileInput}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            data-testid="bulk-file-input"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
            }}
          />
          <button
            type="button"
            className="border-border hover:bg-muted/40 flex cursor-pointer flex-col items-center gap-2 rounded-xl border border-dashed p-8 transition-colors"
            onClick={() => {
              fileInput.current?.click();
            }}
          >
            {file ? (
              <FileTextIcon className="text-muted-foreground size-6" />
            ) : (
              <UploadIcon className="text-muted-foreground size-6" />
            )}
            <span className="text-sm font-medium">
              {file ? file.name : t("admin.users.bulk.pickFile")}
            </span>
            <span className="text-muted-foreground text-xs">
              {file ? t("admin.users.bulk.replaceFile") : t("admin.users.bulk.fileFormat")}
            </span>
          </button>
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                downloadCsv("invite-template.csv", TEMPLATE_CSV);
              }}
            >
              <DownloadIcon />
              {t("admin.users.bulk.template")}
            </Button>
            {file && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setFile(null);
                  if (fileInput.current) fileInput.current.value = "";
                }}
              >
                <XIcon />
                {t("admin.users.bulk.clearFile")}
              </Button>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="bulk-paste">{t("admin.users.bulk.pasteLabel")}</Label>
            <Textarea
              id="bulk-paste"
              rows={5}
              value={pasted}
              // One source at a time (bulk-invite-upload.html, legend 4).
              disabled={file !== null}
              placeholder={"anna@company.com\ndmitry@company.com, admin"}
              onChange={(event) => {
                setPasted(event.target.value);
              }}
            />
            <span
              className={
                pastedInvalid ? "text-destructive text-xs" : "text-muted-foreground text-xs"
              }
            >
              {file
                ? t("admin.users.bulk.pasteLocked")
                : pastedInvalid
                  ? t("admin.users.bulk.pasteInvalid")
                  : t("admin.users.bulk.pasteHint")}
            </span>
          </div>

          {error && <p className="text-destructive text-sm">{error}</p>}
          <Button
            className="self-end"
            disabled={!smtpConfigured || dryRun.isPending || !canProceed}
            onClick={() => {
              dryRun.mutate(defaultRole);
            }}
          >
            {t("admin.users.bulk.toPreview")}
          </Button>
        </div>
      ) : (
        <div className="bg-card flex flex-col gap-4 rounded-xl border p-6 shadow-2xs">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-sm font-semibold">{t("admin.users.bulk.previewTitle")}</span>
            <div className="flex items-center gap-2">
              <Label className="text-muted-foreground text-xs">
                {t("admin.users.bulk.defaultRole")}
              </Label>
              <SelectField
                size="sm"
                className="w-32"
                options={ROLES.map((value) => ({ value, label: roleLabel(value, t) }))}
                value={defaultRole}
                onValueChange={(value) => {
                  setDefaultRole(value);
                  // Re-preview with the new role so the statuses and "Send N"
                  // count never disagree with what the real send will do.
                  dryRun.mutate(value);
                }}
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              variant={statusFilter === null ? "secondary" : "outline"}
              size="xs"
              onClick={() => {
                setStatusFilter(null);
              }}
            >
              {t("admin.users.bulk.filters.all")}
              <Badge variant="outline" className="ml-1 px-1.5">
                {(preview ?? []).length}
              </Badge>
            </Button>
            {(Object.keys(counts) as BulkRowStatus[]).map((status) => (
              <Button
                key={status}
                variant={statusFilter === status ? "secondary" : "outline"}
                size="xs"
                onClick={() => {
                  setStatusFilter(statusFilter === status ? null : status);
                }}
              >
                {t(`admin.users.bulk.statuses.${status}`)}
                <Badge variant="outline" className="ml-1 px-1.5">
                  {counts[status]}
                </Badge>
              </Button>
            ))}
          </div>

          <TableFrame>
            <DataTable>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>{t("admin.users.columns.email")}</TableHead>
                  <TableHead>{t("admin.users.columns.role")}</TableHead>
                  <TableHead>{t("admin.users.columns.status")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((row) => (
                  <TableRow key={row.row} className="hover:bg-muted/40">
                    <TableCell className="text-muted-foreground tabular-nums">{row.row}</TableCell>
                    <TruncateCell className="max-w-[16rem] font-medium" text={row.email} />
                    <TableCell>
                      {(ROLES as readonly string[]).includes(row.role)
                        ? roleLabel(row.role, t)
                        : row.role}
                      {row.role_from_default && (
                        <span className="text-muted-foreground ml-1.5 text-xs">
                          {t("admin.users.bulk.roleDefault")}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_TONE[row.status]}>
                        {t(`admin.users.bulk.statuses.${row.status}`)}
                      </Badge>
                      {row.message && (
                        <span className="text-muted-foreground ml-2 text-xs">
                          {rowMessages[row.message] ?? row.message}
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </DataTable>
          </TableFrame>

          {error && <p className="text-destructive text-sm">{error}</p>}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-muted-foreground text-xs">
              {t("admin.users.bulk.skippedHint")}
            </span>
            <Button
              disabled={counts.created === 0 || send.isPending || dryRun.isPending}
              onClick={() => {
                send.mutate();
              }}
            >
              {t("admin.users.bulk.send", { count: counts.created })}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** The 207 outcome: queued / skipped / errors + a client-side CSV report. */
function BulkSummary({ results }: { results: BulkRow[] }) {
  const { t } = useTranslation();
  const created = results.filter((row) => row.status === "created").length;
  const invalid = results.filter((row) => row.status === "invalid").length;
  const skipped = results.length - created - invalid;

  return (
    <div className="bg-card flex flex-col items-center gap-3 rounded-xl border p-8 text-center shadow-2xs">
      <h2 className="text-lg font-semibold">{t("admin.users.bulk.sentTitle")}</h2>
      <p className="text-muted-foreground text-sm">
        {t("admin.users.bulk.sentSummary", { created, skipped, invalid })}
      </p>
      <div className="flex gap-2">
        <Button
          variant="outline"
          onClick={() => {
            downloadCsv("invite-report.csv", reportCsv(results));
          }}
        >
          {t("admin.users.bulk.downloadReport")}
        </Button>
        <Button render={<Link to="/admin/users?tab=invites" />}>
          {t("admin.users.bulk.toInvites")}
        </Button>
      </div>
    </div>
  );
}
