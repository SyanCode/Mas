import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium tracking-wide transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "text-foreground",
        success: "border-transparent bg-emerald-500/10 text-emerald-700 before:size-1.5 before:rounded-full before:bg-current dark:text-emerald-300",
        warning: "border-transparent bg-amber-500/10 text-amber-700 before:size-1.5 before:rounded-full before:bg-current dark:text-amber-300",
        info: "border-transparent bg-indigo-500/10 text-indigo-700 before:size-1.5 before:rounded-full before:bg-current dark:text-indigo-300",
        destructive: "border-transparent bg-red-500/10 text-red-700 before:size-1.5 before:rounded-full before:bg-current dark:text-red-300",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
