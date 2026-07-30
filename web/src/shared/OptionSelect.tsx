import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";

export type OptionSelectItem<TValue extends string = string> = {
  value: TValue;
  label: string;
  disabled?: boolean;
};

export function OptionSelect<TValue extends string>({
  id,
  value,
  options,
  placeholder,
  disabled = false,
  onValueChange,
}: {
  id?: string;
  value: TValue;
  options: OptionSelectItem<TValue>[];
  placeholder?: string;
  disabled?: boolean;
  onValueChange: (value: TValue) => void;
}) {
  return (
    <Select value={value} onValueChange={(nextValue) => onValueChange(nextValue as TValue)}>
      <SelectTrigger id={id} className="w-full" disabled={disabled}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent position="popper">
        <SelectGroup>
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              disabled={option.disabled}
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}
