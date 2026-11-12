import * as React from "react";
import { cn } from "@/lib/utils";
import { useFormItemId } from "./form-item";

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, type, id, ...props }, ref) => (
  <input
    id={id ?? useFormItemId() ?? undefined}
    type={type}
    ref={ref}
    className={cn(
      "flex h-10 w-full rounded-lg border border-transparent bg-muted/70 px-3.5 py-1 text-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground/70 hover:bg-muted focus-visible:border-brand/40 focus-visible:bg-card focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/10 disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";

const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, id, ...props }, ref) => (
  <textarea
    id={id ?? useFormItemId() ?? undefined}
    ref={ref}
    className={cn(
      "flex min-h-[88px] w-full rounded-lg border border-transparent bg-muted/70 px-3.5 py-2.5 text-sm transition-colors placeholder:text-muted-foreground/70 hover:bg-muted focus-visible:border-brand/40 focus-visible:bg-card focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/10 disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export { Input, Textarea };
