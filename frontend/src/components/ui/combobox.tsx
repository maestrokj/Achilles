"use client";

import { Combobox as ComboboxPrimitive } from "@base-ui/react/combobox";
import { CheckIcon, ChevronsUpDownIcon, XIcon } from "lucide-react";

import { cn } from "@/lib/utils";

const Combobox = ComboboxPrimitive.Root;
const ComboboxValue = ComboboxPrimitive.Value;
const ComboboxList = ComboboxPrimitive.List;

/** `clearLabel` opts the field into a clear button, which Base UI mounts only
 * while a value is set — the way a nullable field is emptied back to its
 * fallback. Without it there is no path from a chosen item back to `null`. */
function ComboboxInput({
  className,
  clearLabel,
  ...props
}: ComboboxPrimitive.Input.Props & { clearLabel?: string }) {
  return (
    <div data-slot="combobox-input-wrapper" className="relative w-full">
      <ComboboxPrimitive.Input
        data-slot="combobox-input"
        className={cn(
          "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 disabled:bg-input/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 h-9 w-full min-w-0 rounded-lg border bg-transparent py-1 pl-3 text-base transition-colors outline-none focus-visible:ring-3 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:ring-3 md:text-sm",
          clearLabel === undefined ? "pr-9" : "pr-15",
          className,
        )}
        {...props}
      />
      {clearLabel !== undefined && (
        <ComboboxPrimitive.Clear
          data-slot="combobox-clear"
          aria-label={clearLabel}
          className="text-muted-foreground hover:text-foreground absolute inset-y-0 right-7 flex items-center px-1.5 transition-colors outline-none disabled:pointer-events-none"
        >
          <XIcon className="pointer-events-none size-3.5" />
        </ComboboxPrimitive.Clear>
      )}
      <ComboboxPrimitive.Trigger
        data-slot="combobox-trigger"
        aria-label="Toggle options"
        className="text-muted-foreground hover:text-foreground absolute inset-y-0 right-0 flex items-center px-2.5 transition-colors outline-none disabled:pointer-events-none"
      >
        <ComboboxPrimitive.Icon
          render={<ChevronsUpDownIcon className="pointer-events-none size-4" />}
        />
      </ComboboxPrimitive.Trigger>
    </div>
  );
}

function ComboboxContent({
  className,
  children,
  sideOffset = 4,
  ...props
}: ComboboxPrimitive.Popup.Props & Pick<ComboboxPrimitive.Positioner.Props, "sideOffset">) {
  return (
    <ComboboxPrimitive.Portal>
      <ComboboxPrimitive.Positioner sideOffset={sideOffset} className="isolate z-50">
        <ComboboxPrimitive.Popup
          data-slot="combobox-content"
          className={cn(
            "bg-popover text-popover-foreground ring-foreground/10 data-[side=bottom]:slide-in-from-top-2 data-[side=top]:slide-in-from-bottom-2 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95 relative isolate z-50 max-h-[min(var(--available-height),18rem)] w-(--anchor-width) min-w-36 origin-(--transform-origin) overflow-x-hidden overflow-y-auto rounded-lg p-1 shadow-md ring-1 duration-100",
            className,
          )}
          {...props}
        >
          {children}
        </ComboboxPrimitive.Popup>
      </ComboboxPrimitive.Positioner>
    </ComboboxPrimitive.Portal>
  );
}

function ComboboxEmpty({ className, ...props }: ComboboxPrimitive.Empty.Props) {
  return (
    // Base UI keeps this element mounted for screen-reader announcements even when
    // the list is non-empty (its text just clears). Pad it only when it actually
    // holds text, so an empty list adds no phantom gap at the top of the popup.
    <ComboboxPrimitive.Empty
      data-slot="combobox-empty"
      className={cn(
        "text-muted-foreground text-center text-sm [&:not(:empty)]:px-2 [&:not(:empty)]:py-4",
        className,
      )}
      {...props}
    />
  );
}

function ComboboxItem({ className, children, ...props }: ComboboxPrimitive.Item.Props) {
  return (
    <ComboboxPrimitive.Item
      data-slot="combobox-item"
      className={cn(
        "data-highlighted:bg-accent data-highlighted:text-accent-foreground relative flex w-full cursor-default items-center gap-1.5 rounded-md py-1.5 pr-8 pl-2 text-sm outline-hidden select-none data-disabled:pointer-events-none data-disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <span className="flex flex-1 shrink-0 items-center gap-2 truncate">{children}</span>
      <ComboboxPrimitive.ItemIndicator
        render={
          <span className="pointer-events-none absolute right-2 flex size-4 items-center justify-center" />
        }
      >
        <CheckIcon className="pointer-events-none size-4" />
      </ComboboxPrimitive.ItemIndicator>
    </ComboboxPrimitive.Item>
  );
}

export {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxValue,
};
