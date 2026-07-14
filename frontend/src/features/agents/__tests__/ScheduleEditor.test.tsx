import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ScheduleEditor } from "../ScheduleEditor";
import type { ScheduleSpec } from "../types";

/** Stateful host mirroring the editor: ScheduleEditor is controlled, so a real
 *  round-trip needs the parent to feed back the spec it emits. `onSpec` records
 *  every emitted value — the exact JSONB the backend receives. */
function Harness({ onSpec }: { onSpec: (next: ScheduleSpec | null) => void }) {
  const [value, setValue] = useState<ScheduleSpec | null>(null);
  return (
    <ScheduleEditor
      value={value}
      onChange={(next) => {
        setValue(next);
        onSpec(next);
      }}
    />
  );
}

/** The select whose trigger currently shows `label` — several coexist once a
 *  calendar schedule is active (mode + cadence + weekday). */
function comboboxShowing(label: string): HTMLElement {
  const match = screen.getAllByRole("combobox").find((box) => box.textContent.includes(label));
  if (match === undefined) throw new Error(`no combobox showing "${label}"`);
  return match;
}

describe("ScheduleEditor", () => {
  it("shapes each mode into the discriminated schedule spec the backend expects", async () => {
    const onSpec = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSpec={onSpec} />);

    // Manual → Interval: preset of 24h, matching the every_hours ≥1 ≤24 contract.
    await user.click(comboboxShowing("Manually"));
    await user.click(await screen.findByRole("option", { name: "Interval" }));
    expect(onSpec).toHaveBeenLastCalledWith({ type: "interval", every_hours: 24 });

    // Changing the interval preset emits the new hour count, not a fresh object shape.
    await user.click(comboboxShowing("24 h"));
    await user.click(await screen.findByRole("option", { name: "12 h" }));
    expect(onSpec).toHaveBeenLastCalledWith({ type: "interval", every_hours: 12 });

    // Interval → Calendar: daily slot at 09:00, no weekday (daily omits it).
    await user.click(comboboxShowing("Interval"));
    await user.click(await screen.findByRole("option", { name: "Calendar" }));
    expect(onSpec).toHaveBeenLastCalledWith({
      type: "calendar",
      cadence: "daily",
      time: "09:00",
    });

    // Daily → Weekly: weekday defaults to 0 (Monday) — the contract requires it
    // for weekly cadence, so it must be sent, never left undefined.
    await user.click(comboboxShowing("Daily"));
    await user.click(await screen.findByRole("option", { name: "Weekly" }));
    expect(onSpec).toHaveBeenLastCalledWith({
      type: "calendar",
      cadence: "weekly",
      weekday: 0,
      time: "09:00",
    });
  });

  it("edits the calendar slot's weekday and time in place", async () => {
    const onSpec = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSpec={onSpec} />);

    await user.click(comboboxShowing("Manually"));
    await user.click(await screen.findByRole("option", { name: "Calendar" }));
    await user.click(comboboxShowing("Daily"));
    await user.click(await screen.findByRole("option", { name: "Weekly" }));

    // Weekday picker only exists for weekly cadence; pick Wednesday (2).
    await user.click(comboboxShowing("Mon"));
    await user.click(await screen.findByRole("option", { name: "Wed" }));
    expect(onSpec).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "calendar", cadence: "weekly", weekday: 2 }),
    );

    // The time field feeds time straight through (HH:MM), the pattern's shape.
    const time = screen.getByLabelText("Time");
    await user.clear(time);
    await user.type(time, "07:30");
    expect(onSpec).toHaveBeenLastCalledWith(
      expect.objectContaining({ cadence: "weekly", weekday: 2, time: "07:30" }),
    );
  });

  it("collapses back to manual (null schedule)", async () => {
    const onSpec = vi.fn();
    const user = userEvent.setup();
    render(<Harness onSpec={onSpec} />);

    await user.click(comboboxShowing("Manually"));
    await user.click(await screen.findByRole("option", { name: "Interval" }));
    await user.click(comboboxShowing("Interval"));
    await user.click(await screen.findByRole("option", { name: "Manually" }));
    expect(onSpec).toHaveBeenLastCalledWith(null);
  });
});
