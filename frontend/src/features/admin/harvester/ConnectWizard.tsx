import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, SearchIcon, XIcon } from "lucide-react";
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "@/lib/toast";

import { api } from "@/api/client";
import { toastApiError } from "@/api/errors";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";

import { createSource, harvesterKeys, listConnectorTypes } from "./api";
import type { CatalogItem, ConnectorType, DiagnosisStep } from "./types";

interface ProbeResult {
  ok: boolean;
  steps: DiagnosisStep[];
  catalog: CatalogItem[] | null;
}

function probeDraft(body: {
  connector_type: string;
  base_url?: string | null;
  credential?: string | null;
}): Promise<ProbeResult> {
  return api.post("sources/probe", { json: body }).json<ProbeResult>();
}

const STEP_KEYS = ["1", "2", "3", "4"] as const;

/** The 4-step connect wizard: type → connection → probe → scope.
 * Wireframe: admin-panel/_wireframes/data-sources.html#source-wizard. */
export function ConnectWizard({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [type, setType] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [credential, setCredential] = useState("");
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [scopeMode, setScopeMode] = useState<"all" | "selected">("all");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [catalogFilter, setCatalogFilter] = useState("");

  const connectors = useQuery({
    queryKey: harvesterKeys.connectors,
    queryFn: listConnectorTypes,
    enabled: open,
  });
  const manifest: ConnectorType | undefined = connectors.data?.find((item) => item.type === type);

  const reset = () => {
    setStep(1);
    setType(null);
    setName("");
    setBaseUrl("");
    setCredential("");
    setProbe(null);
    setScopeMode("all");
    setPicked(new Set());
    setCatalogFilter("");
  };

  const runProbe = useMutation({
    mutationFn: () =>
      probeDraft({
        connector_type: type ?? "",
        base_url: baseUrl || null,
        credential: credential || null,
      }),
    onSuccess: setProbe,
    onError: (error) => void toastApiError(error, t("admin.harvester.wizard.probeFailed")),
  });

  const connect = useMutation({
    mutationFn: () =>
      createSource({
        name,
        connector_type: type ?? "",
        base_url: baseUrl || null,
        credential: credential || null,
        scope_mode: scopeMode,
        scope_list: scopeMode === "selected" ? [...picked] : [],
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: harvesterKeys.sources });
      toast.success(t("admin.harvester.wizard.connected"));
      onOpenChange(false);
      reset();
    },
    onError: (error) => void toastApiError(error, t("admin.harvester.wizard.connectFailed")),
  });

  const catalog = probe?.catalog ?? [];
  const visibleCatalog = catalog.filter((item) =>
    item.name.toLowerCase().includes(catalogFilter.toLowerCase()),
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      {/* The base DialogContent carries sm:max-w-sm — a plain max-w-lg loses to it. */}
      <DialogContent className="p-6 sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("admin.harvester.wizard.title")}</DialogTitle>
        </DialogHeader>
        <div className="flex items-center gap-2">
          {STEP_KEYS.map((key, index) => {
            const number = index + 1;
            const done = number < step;
            const current = number === step;
            return (
              <Fragment key={key}>
                {index > 0 && <span aria-hidden="true" className="bg-border h-px min-w-2 flex-1" />}
                <span
                  aria-hidden="true"
                  className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium tabular-nums transition-colors ${
                    current
                      ? "bg-primary text-primary-foreground"
                      : done
                        ? "bg-primary/15 text-primary"
                        : "text-muted-foreground border"
                  }`}
                >
                  {done ? <CheckIcon className="size-3.5" /> : number}
                </span>
                <span
                  className={`text-xs whitespace-nowrap ${
                    current ? "text-foreground font-medium" : "text-muted-foreground"
                  }`}
                >
                  {t(`admin.harvester.wizard.steps.${key}`)}
                </span>
              </Fragment>
            );
          })}
        </div>

        {step === 1 && (
          <div className="flex flex-col gap-3">
            {connectors.isPending ? (
              <Spinner className="size-5 self-center" />
            ) : (
              <RadioGroup
                className="gap-2"
                value={type ?? ""}
                onValueChange={(value) => {
                  if (typeof value === "string" && value) setType(value);
                }}
              >
                {(connectors.data ?? []).map((item) => (
                  <div
                    key={item.type}
                    className={`hover:bg-muted/40 flex items-center gap-2.5 rounded-lg border px-3 py-2.5 transition-colors ${
                      type === item.type ? "border-primary/40 bg-muted/40" : ""
                    }`}
                  >
                    <RadioGroupItem value={item.type} id={`connector-${item.type}`} />
                    <Label className="flex-1 cursor-pointer" htmlFor={`connector-${item.type}`}>
                      {item.title}
                    </Label>
                  </div>
                ))}
              </RadioGroup>
            )}
            <div className="flex justify-between pt-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  onOpenChange(false);
                  reset();
                }}
              >
                {t("admin.platform.cancel")}
              </Button>
              <Button
                size="sm"
                disabled={!type}
                onClick={() => {
                  setStep(2);
                }}
              >
                {t("admin.harvester.wizard.next")}
              </Button>
            </div>
          </div>
        )}

        {step === 2 && manifest && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wizard-name">{t("admin.harvester.wizard.name")}</Label>
              <Input
                id="wizard-name"
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                }}
              />
            </div>
            {manifest.needs_base_url && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="wizard-url">{t("admin.harvester.wizard.baseUrl")}</Label>
                <Input
                  id="wizard-url"
                  placeholder="https://…"
                  value={baseUrl}
                  onChange={(event) => {
                    setBaseUrl(event.target.value);
                  }}
                />
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wizard-credential">{manifest.credential_label}</Label>
              <Input
                id="wizard-credential"
                type="password"
                value={credential}
                onChange={(event) => {
                  setCredential(event.target.value);
                }}
              />
            </div>
            <div className="flex justify-between">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setStep(1);
                }}
              >
                {t("admin.harvester.wizard.back")}
              </Button>
              <Button
                size="sm"
                disabled={!name || (manifest.needs_base_url && !baseUrl)}
                onClick={() => {
                  setStep(3);
                  setProbe(null);
                  runProbe.mutate();
                }}
              >
                {t("admin.harvester.wizard.next")}
              </Button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="flex flex-col gap-3">
            {runProbe.isPending ? (
              <div className="flex items-center gap-2 text-sm">
                <Spinner className="size-4" />
                {t("admin.harvester.wizard.probing")}
              </div>
            ) : probe ? (
              <div className="flex flex-col gap-1.5">
                {probe.steps.map((item) => (
                  <div key={item.name} className="flex items-center gap-2 text-sm">
                    {item.ok ? (
                      <CheckIcon aria-hidden="true" className="text-success size-4" />
                    ) : (
                      <XIcon aria-hidden="true" className="text-destructive size-4" />
                    )}
                    <span>{t(`admin.harvester.probeSteps.${item.name}`)}</span>
                    {item.detail && (
                      <span className="text-muted-foreground text-xs">{item.detail}</span>
                    )}
                  </div>
                ))}
              </div>
            ) : null}
            <div className="flex justify-between">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setStep(2);
                }}
              >
                {t("admin.harvester.wizard.back")}
              </Button>
              <Button
                size="sm"
                disabled={!probe?.ok}
                onClick={() => {
                  setStep(4);
                }}
              >
                {t("admin.harvester.wizard.next")}
              </Button>
            </div>
          </div>
        )}

        {step === 4 && (
          <div className="flex flex-col gap-3">
            <Select
              items={[
                { value: "all", label: t("admin.harvester.scopeAll") },
                { value: "selected", label: t("admin.harvester.scopeSelected") },
              ]}
              value={scopeMode}
              onValueChange={(value) => {
                if (value) setScopeMode(value);
              }}
            >
              <SelectTrigger size="sm" className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("admin.harvester.scopeAll")}</SelectItem>
                <SelectItem value="selected">{t("admin.harvester.scopeSelected")}</SelectItem>
              </SelectContent>
            </Select>
            {scopeMode === "selected" && (
              <>
                <div className="relative">
                  <SearchIcon
                    aria-hidden="true"
                    className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
                  />
                  <Input
                    className="pl-9"
                    placeholder={t("admin.harvester.wizard.catalogFilter")}
                    value={catalogFilter}
                    onChange={(event) => {
                      setCatalogFilter(event.target.value);
                    }}
                  />
                </div>
                <div className="max-h-48 divide-y overflow-y-auto rounded-lg border">
                  {visibleCatalog.map((item) => (
                    <label
                      key={item.native_id}
                      className="hover:bg-muted/40 flex items-center gap-2.5 px-3 py-2 text-sm transition-colors"
                    >
                      <Checkbox
                        checked={picked.has(item.native_id)}
                        onCheckedChange={(checked) => {
                          const next = new Set(picked);
                          if (checked) next.add(item.native_id);
                          else next.delete(item.native_id);
                          setPicked(next);
                        }}
                      />
                      {item.name}
                      <span className="text-muted-foreground text-xs">{item.kind}</span>
                    </label>
                  ))}
                </div>
              </>
            )}
            <div className="flex justify-between">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setStep(3);
                }}
              >
                {t("admin.harvester.wizard.back")}
              </Button>
              <Button
                size="sm"
                disabled={connect.isPending || (scopeMode === "selected" && picked.size === 0)}
                onClick={() => {
                  connect.mutate();
                }}
              >
                {t("admin.harvester.wizard.connect")}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
