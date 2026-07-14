import { useState } from "react";

import { Input } from "@/components/ui/input";

import { minuteToTime, timeToMinute } from "./timeOfDay";

/** A small "HH:MM" field that commits the minute-of-day on blur/Enter and
 * snaps back to the last saved value on invalid input. Remount (key) it on
 * server value change — the draft is local. */
export function TimeOfDayInput({
  value,
  label,
  disabled = false,
  onCommit,
}: {
  value: string;
  label: string;
  disabled?: boolean;
  onCommit: (minuteOfDay: number) => void;
}) {
  const [draft, setDraft] = useState(value);

  const commit = () => {
    const minute = timeToMinute(draft);
    if (minute === null) {
      setDraft(value);
      return;
    }
    if (minuteToTime(minute) !== value) onCommit(minute);
  };

  return (
    <Input
      className="w-20"
      value={draft}
      disabled={disabled}
      aria-label={label}
      placeholder="03:00"
      onChange={(event) => {
        setDraft(event.target.value);
      }}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") commit();
      }}
    />
  );
}
