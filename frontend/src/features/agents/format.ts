/** Display helpers of the agents surface — generic formatting lives in @/lib/format. */

import type { TFunction } from "i18next";

import { weekdayShort } from "@/lib/format";

import type { ScheduleSpec } from "./types";

export function scheduleLabel(schedule: ScheduleSpec | null, t: TFunction, locale: string): string {
  if (schedule === null) return t("agents.schedule.manual");
  if (schedule.type === "interval") {
    return t("agents.schedule.everyHours", { count: schedule.every_hours });
  }
  if (schedule.cadence === "daily") return t("agents.schedule.daily", { time: schedule.time });
  return t("agents.schedule.weekly", {
    day: weekdayShort(schedule.weekday ?? 0, locale),
    time: schedule.time,
  });
}
