import { Check, ChevronsUpDown } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "../components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "../components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { cn } from "../lib/utils";

export type SearchSelectOption = {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
};

export function SearchSelect({
  id,
  value,
  options,
  placeholder,
  emptyText,
  disabled = false,
  onValueChange,
}: {
  id?: string;
  value: string;
  options: SearchSelectOption[];
  placeholder: string;
  emptyText: string;
  disabled?: boolean;
  onValueChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const selectedValueDescriptionId = useId();
  const selected = options.find((option) => option.value === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-describedby={selected ? selectedValueDescriptionId : undefined}
          disabled={disabled}
          className="min-h-9 h-auto w-full justify-between gap-3 py-2 font-normal"
        >
          {selected && (
            <span id={selectedValueDescriptionId} className="sr-only">
              {selected.description
                ? `${selected.label}, ${selected.description}`
                : selected.label}
            </span>
          )}
          <span
            className="min-w-0 flex-1 text-left"
            aria-hidden={selected ? true : undefined}
          >
            <span className="block truncate">{selected ? selected.label : placeholder}</span>
            {selected?.description && (
              <span className="block truncate text-xs text-muted-foreground">
                {selected.description}
              </span>
            )}
          </span>
          <ChevronsUpDown data-icon="inline-end" className="shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] bg-popover p-0 text-popover-foreground shadow-lg"
      >
        <Command className="bg-popover">
          <CommandInput placeholder={placeholder} />
          <CommandList>
            <CommandEmpty>{emptyText}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => (
                <CommandItem
                  key={option.value}
                  value={`${option.label} ${option.description ?? ""} ${option.value}`}
                  disabled={option.disabled}
                  onSelect={() => {
                    onValueChange(option.value);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn(
                      value === option.value ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <span className="min-w-0">
                    <span className="block truncate">{option.label}</span>
                    {option.description && (
                      <span className="block truncate text-xs text-muted-foreground">
                        {option.description}
                      </span>
                    )}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
