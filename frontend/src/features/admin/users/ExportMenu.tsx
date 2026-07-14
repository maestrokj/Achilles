import { useMutation } from "@tanstack/react-query";
import { DownloadIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ListQuery } from "@/api/lists";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { downloadBlob } from "@/lib/download";

import { exportUsers } from "./api";

/** "Export" (users.html, legend 1): downloads the current list — search and
 * facets applied — as CSV or JSON. The request is authorized, so we fetch the
 * blob and hand it to the browser. */
export function ExportMenu({ query }: { query: ListQuery }) {
  const { t } = useTranslation();
  const download = useMutation({
    mutationFn: (format: "csv" | "json") =>
      exportUsers(query, format).then((blob) => {
        downloadBlob(`users.${format}`, blob);
      }),
  });

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="outline" size="sm" disabled={download.isPending} />}
      >
        <DownloadIcon className="size-4" />
        {t("admin.users.export.label")}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          onClick={() => {
            download.mutate("csv");
          }}
        >
          {t("admin.users.export.csv")}
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => {
            download.mutate("json");
          }}
        >
          {t("admin.users.export.json")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
