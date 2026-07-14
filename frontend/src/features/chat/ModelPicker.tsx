import { useQuery } from "@tanstack/react-query";
import { ChevronDownIcon, CpuIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";

import { chatQueryKeys, getChatModels } from "./api";

const MODELS_STALE_MS = 5 * 60 * 1000;

/** Composer model picker fed by GET /chat/models; the default is only a
 * display fallback — the server owns stickiness and the default itself. */
export function ModelPicker({
  selected,
  onSelect,
}: {
  /** The user's explicit pick or the conversation's sticky model; null = server default. */
  selected: string | null;
  onSelect: (modelId: string) => void;
}) {
  const { t } = useTranslation();
  const { data, isPending } = useQuery({
    queryKey: chatQueryKeys.models,
    queryFn: getChatModels,
    staleTime: MODELS_STALE_MS,
  });

  // Reserve the trigger's footprint while the catalogue loads, or the composer's
  // toolbar reshuffles under the cursor the moment it lands.
  if (isPending) return <Skeleton className="h-7 w-32" />;
  const items = data?.items ?? [];
  if (items.length === 0) return null;

  // Same order the server resolves a turn in: this session's pick / the
  // conversation's sticky → the user's personal default → the admin default.
  const current =
    items.find((item) => item.model_id === selected) ??
    items.find((item) => item.model_id === data?.selected) ??
    items.find((item) => item.is_default) ??
    items[0];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t("chat.model.label")}
        className="text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring flex h-7 items-center gap-1.5 rounded-md px-2 text-xs font-medium outline-none focus-visible:ring-2"
      >
        <CpuIcon className="size-3.5" aria-hidden="true" />
        {current.display_name}
        <ChevronDownIcon className="size-3" aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="min-w-48">
        <DropdownMenuRadioGroup
          value={current.model_id}
          onValueChange={(modelId) => {
            onSelect(String(modelId));
          }}
        >
          {items.map((item) => (
            <DropdownMenuRadioItem key={item.model_id} value={item.model_id}>
              {item.display_name}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
