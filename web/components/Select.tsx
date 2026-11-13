"use client";

import { Children, Fragment, isValidElement, type ReactElement, type ReactNode } from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { ChevronsUpDown } from "lucide-react";
import { SelectContent, SelectItem } from "@/components/cms/ui/select";
import { cn } from "@/lib/utils";

export const FIELD =
  "w-full rounded-lg border border-zinc-300 bg-white/80 px-3 py-2 text-sm text-zinc-900 outline-none transition placeholder:text-zinc-400 " +
  "focus:border-indigo-400 focus:ring-4 focus:ring-indigo-500/15 dark:border-white/10 dark:bg-black/40 dark:text-zinc-100 dark:placeholder:text-zinc-600 dark:focus:border-indigo-400/60";

type PickOption = { value: string; label: ReactNode; disabled?: boolean };

/** Reads the <option> children so call sites keep the markup of a native select. */
function readOptions(children: ReactNode, out: PickOption[] = []): PickOption[] {
  Children.forEach(children, (c) => {
    if (!isValidElement(c)) return;
    const el = c as ReactElement<{ value?: string | number; children?: ReactNode; disabled?: boolean }>;
    if (el.type === Fragment) { readOptions(el.props.children, out); return; }
    const label = el.props.children;
    const value = el.props.value ?? (typeof label === "string" || typeof label === "number" ? String(label) : "");
    out.push({ value: String(value), label, disabled: el.props.disabled });
  });
  return out;
}

const NONE = "__empty__"; // Radix items cannot have an empty value

/**
 * A listbox in the dashboard's own style instead of the browser's native menu.
 * Same props as a native <select> (value, onChange(e) with e.target.value, <option> children).
 */
export function Select({ value, onChange, children, className, disabled, id, required, name, ...rest }: Omit<React.ComponentProps<"select">, "onChange" | "value"> & { value?: string | number; onChange?: (e: { target: { value: string } }) => void }) {
  const options = readOptions(children);
  const current = value === undefined ? "" : String(value);
  const shown = options.find((o) => o.value === current);
  return (
    <SelectPrimitive.Root value={current === "" ? undefined : current} onValueChange={(v) => onChange?.({ target: { value: v === NONE ? "" : v } })} disabled={disabled} required={required} name={name}>
      <SelectPrimitive.Trigger id={id} aria-label={rest["aria-label"]}
        className={cn(FIELD, "flex items-center justify-between gap-2 pr-2.5 text-left hover:border-zinc-400 data-[placeholder]:text-zinc-400 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:border-white/20 [&>span]:truncate", className)}>
        <SelectPrimitive.Value placeholder={options.find((o) => o.disabled && o.value === "")?.label}>{shown?.label}</SelectPrimitive.Value>
        <SelectPrimitive.Icon asChild><ChevronsUpDown className="size-3.5 shrink-0 opacity-50" /></SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectContent position="popper" align="start">
        {options.filter((o) => !o.disabled).map((o) => (
          <SelectItem key={o.value} value={o.value === "" ? NONE : o.value}>{o.label}</SelectItem>
        ))}
      </SelectContent>
    </SelectPrimitive.Root>
  );
}

