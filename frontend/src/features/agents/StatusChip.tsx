import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import type { AgentStatus } from "./types";

const STATUS_CLASSES: Record<AgentStatus, string> = {
  active: "bg-success/15 text-success",
  disabled: "bg-warning/15 text-warning",
  admin_paused: "bg-destructive/10 text-destructive",
  budget_exceeded: "bg-warning/15 text-warning",
  model_missing: "bg-destructive/10 text-destructive",
};

export function StatusChip({ status, className }: { status: AgentStatus; className?: string }) {
  const { t } = useTranslation();
  return (
    <Badge variant="secondary" className={cn(STATUS_CLASSES[status], className)}>
      {t(`agents.status.${status}`)}
    </Badge>
  );
}
