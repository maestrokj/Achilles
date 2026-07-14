import { ChevronDownIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/** One facet: values combine as OR, distinct facets combine as AND server-side.
 * The trigger stays a plain label — the active selection shows as ticks once the
 * menu is open, so no count badge clutters the toolbar.
 *
 * Option labels render lowercase by convention (enum values read as quiet filter
 * tags, not headings). Facets whose options are proper nouns — connected-source
 * or people's names — pass `preserveOptionCase` to keep them verbatim. */
export function FacetSelect({
  label,
  options,
  selected,
  onToggle,
  preserveOptionCase,
}: {
  label: string;
  options: { value: string; label: string }[];
  selected: string[];
  onToggle: (value: string) => void;
  preserveOptionCase?: boolean;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="outline" />}>
        {label}
        <ChevronDownIcon className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {options.map((option) => (
          <DropdownMenuCheckboxItem
            key={option.value}
            className={preserveOptionCase ? undefined : "lowercase"}
            checked={selected.includes(option.value)}
            closeOnClick={false}
            onCheckedChange={() => {
              onToggle(option.value);
            }}
          >
            {option.label}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
