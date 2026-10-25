"use client";

import { Input, Label, Select, Textarea } from "@/components/ui";
import type { StackField } from "@/lib/api";
import { useT } from "@/lib/i18n";

export type StackValues = Record<string, string | number | boolean>;

export function defaults(fields: StackField[]): StackValues {
  return Object.fromEntries(fields.map((f) => [f.key, f.default ?? (f.type === "boolean" ? false : f.type === "number" ? 0 : "")]));
}

/** The form of a service template; labels and help come from the API in English (see lib/stackTexts.ts). */
export function StackFields({ fields, values, onChange }: { fields: StackField[]; values: StackValues; onChange: (v: StackValues) => void }) {
  const t = useT();
  const set = (key: string, value: string | number | boolean) => onChange({ ...values, [key]: value });
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {fields.map((f) => {
        const title = `${t(f.label)}${f.required && f.type !== "boolean" ? " *" : ""}`;
        const hint = f.help ? t(f.help) : undefined;
        if (f.type === "boolean") {
          return (
            <label key={f.key} className="flex items-start gap-2 text-sm sm:col-span-2">
              <input type="checkbox" className="mt-1" checked={values[f.key] === true} onChange={(e) => set(f.key, e.target.checked)} />
              <span>{t(f.label)}{hint && <span className="block text-xs text-zinc-500">{hint}</span>}</span>
            </label>
          );
        }
        return (
          <div key={f.key} className={f.type === "textarea" ? "sm:col-span-2" : ""}>
            <Label title={title} hint={hint}>
              {f.type === "select" ? (
                <Select value={String(values[f.key] ?? "")} onChange={(e) => set(f.key, e.target.value)}>
                  {f.options?.map((o) => <option key={o} value={o}>{o}</option>)}
                </Select>
              ) : f.type === "textarea" ? (
                <Textarea rows={4} value={String(values[f.key] ?? "")} maxLength={f.max} onChange={(e) => set(f.key, e.target.value)} />
              ) : f.type === "number" ? (
                <Input type="number" value={Number(values[f.key] ?? 0)} min={f.min} max={f.max}
                  onChange={(e) => set(f.key, e.target.value === "" ? 0 : Number(e.target.value))} />
              ) : (
                <Input value={String(values[f.key] ?? "")} maxLength={f.max} onChange={(e) => set(f.key, e.target.value)} />
              )}
            </Label>
          </div>
        );
      })}
    </div>
  );
}
