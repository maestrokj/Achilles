import { MINUTES_PER_HOUR } from "@/lib/format";

export const MINUTES_PER_DAY = 1440;

/** "HH:MM" for a minute-of-day value (schedule windows are minute-based). */
export function minuteToTime(minute: number): string {
  const hours = String(Math.floor(minute / MINUTES_PER_HOUR)).padStart(2, "0");
  const minutes = String(minute % MINUTES_PER_HOUR).padStart(2, "0");
  return `${hours}:${minutes}`;
}

const TIME_OF_DAY = /^([01]?\d|2[0-3]):([0-5]\d)$/;

/** Parse "HH:MM" into a minute-of-day, or null when the text is not a valid time. */
export function timeToMinute(time: string): number | null {
  const match = TIME_OF_DAY.exec(time.trim());
  if (!match) return null;
  return Number(match[1]) * MINUTES_PER_HOUR + Number(match[2]);
}
