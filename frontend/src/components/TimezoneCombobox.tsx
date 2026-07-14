import { MonitorIcon } from "lucide-react";

import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { browserTimeZone, timeZoneOffset, timeZones } from "@/lib/timezones";

type TimezoneComboboxProps = {
  id: string;
  value: string | null;
  onValueChange: (timezone: string | null) => void;
  placeholder: string;
  emptyLabel: string;
  detectedLabel: string;
  /** Pass on a nullable field to offer a clear button back to the fallback zone. */
  clearLabel?: string;
  disabled?: boolean;
};

/** Shared IANA time-zone picker: the browser-detected zone is pinned first (marked
 * with a monitor icon) and every zone shows its current UTC offset. `detected` is
 * resolved once per mount, not per rendered item. */
export function TimezoneCombobox({
  id,
  value,
  onValueChange,
  placeholder,
  emptyLabel,
  detectedLabel,
  clearLabel,
  disabled,
}: TimezoneComboboxProps) {
  const detected = browserTimeZone();
  return (
    <Combobox items={timeZones()} value={value} disabled={disabled} onValueChange={onValueChange}>
      <div className="w-64">
        <ComboboxInput id={id} placeholder={placeholder} clearLabel={clearLabel} />
      </div>
      <ComboboxContent>
        <ComboboxEmpty>{emptyLabel}</ComboboxEmpty>
        <ComboboxList>
          {(zone: string) => (
            <ComboboxItem key={zone} value={zone}>
              {zone === detected && (
                <MonitorIcon
                  className="text-muted-foreground size-3.5 shrink-0"
                  aria-label={detectedLabel}
                />
              )}
              <span className="truncate">{zone}</span>
              <span className="text-muted-foreground ml-auto shrink-0 pl-2 text-xs tabular-nums">
                {timeZoneOffset(zone)}
              </span>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
