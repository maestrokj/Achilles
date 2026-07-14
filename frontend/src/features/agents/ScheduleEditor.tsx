import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { WEEKDAYS, weekdayShort } from "@/lib/format";

import type { ScheduleSpec } from "./types";

const INTERVAL_PRESETS = [1, 2, 3, 6, 12, 24];

type Mode = "manual" | "interval" | "calendar";

/** Schedule block of the editor: mode + the parameter under the mode.
 * The section heading is owned by the parent card; this renders the controls row.
 * Wireframe: web-app/_wireframes/agent-editor.html. */
export function ScheduleEditor({
  value,
  onChange,
}: {
  value: ScheduleSpec | null;
  onChange: (next: ScheduleSpec | null) => void;
}) {
  const { t, i18n } = useTranslation();
  const mode: Mode = value === null ? "manual" : value.type;

  const setMode = (next: Mode) => {
    if (next === "manual") onChange(null);
    else if (next === "interval") onChange({ type: "interval", every_hours: 24 });
    else onChange({ type: "calendar", cadence: "daily", time: "09:00" });
  };

  const modeOptions = [
    { value: "manual", label: t("agents.schedule.modeManual") },
    { value: "interval", label: t("agents.schedule.modeInterval") },
    { value: "calendar", label: t("agents.schedule.modeCalendar") },
  ];
  const intervalOptions = INTERVAL_PRESETS.map((hours) => ({
    value: String(hours),
    label: t("agents.schedule.hours", { count: hours }),
  }));
  const cadenceOptions = [
    { value: "daily", label: t("agents.schedule.cadenceDaily") },
    { value: "weekly", label: t("agents.schedule.cadenceWeekly") },
  ];
  const weekdayOptions = WEEKDAYS.map((day) => ({
    value: String(day),
    label: weekdayShort(day, i18n.language),
  }));

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        items={modeOptions}
        value={mode}
        onValueChange={(next) => {
          setMode(next as Mode);
        }}
      >
        <SelectTrigger className="w-40">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {modeOptions.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {value?.type === "interval" && (
        <Select
          items={intervalOptions}
          value={String(value.every_hours)}
          onValueChange={(next) => {
            onChange({ type: "interval", every_hours: Number(next) });
          }}
        >
          <SelectTrigger className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {intervalOptions.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {value?.type === "calendar" && (
        <>
          <Select
            items={cadenceOptions}
            value={value.cadence}
            onValueChange={(next) => {
              const cadence = next as "daily" | "weekly";
              onChange({
                type: "calendar",
                cadence,
                weekday: cadence === "weekly" ? (value.weekday ?? 0) : undefined,
                time: value.time,
              });
            }}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {cadenceOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {value.cadence === "weekly" && (
            <Select
              items={weekdayOptions}
              value={String(value.weekday ?? 0)}
              onValueChange={(next) => {
                onChange({ ...value, weekday: Number(next) });
              }}
            >
              <SelectTrigger className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {weekdayOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          <Input
            type="time"
            className="w-28"
            value={value.time}
            onChange={(event) => {
              onChange({ ...value, time: event.target.value });
            }}
            aria-label={t("agents.schedule.time")}
          />
        </>
      )}
    </div>
  );
}
