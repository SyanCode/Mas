"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

const FormItemContext = React.createContext<string | null>(null);

/** The id of the control of the enclosing FormItem: Label, Input, Textarea and SelectTrigger pick it up. */
export const useFormItemId = () => React.useContext(FormItemContext);

/** A label and its control, tied together (for screen readers and for clicking the label). */
export function FormItem({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const id = React.useId();
  return <FormItemContext.Provider value={id}><div className={cn("space-y-1.5", className)} {...props} /></FormItemContext.Provider>;
}
