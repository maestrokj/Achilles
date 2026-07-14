import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** A single-value dropdown from an options list — collapses the Base UI
 * double-enumeration (the `items` prop drives the trigger label, the children
 * the menu). Generic over the value union so callers keep their narrow types. */
export function SelectField<T extends string>({
  options,
  value,
  onValueChange,
  className,
  ariaLabel,
  size,
}: {
  options: { value: T; label: string }[];
  value: T;
  onValueChange: (value: T) => void;
  className?: string;
  ariaLabel?: string;
  size?: "sm" | "default";
}) {
  return (
    <Select
      items={options}
      value={value}
      onValueChange={(next) => {
        onValueChange(next ?? options[0]?.value);
      }}
    >
      <SelectTrigger size={size} className={className} aria-label={ariaLabel}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
