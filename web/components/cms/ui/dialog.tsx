"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as AlertPrimitive from "@radix-ui/react-alert-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "./button";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;

const DialogContent = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Content>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>>(
  ({ className, children, ...props }, ref) => (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="cms fixed inset-0 z-50 bg-black/60" />
      <DialogPrimitive.Content ref={ref}
        className={cn("cms fixed left-1/2 top-1/2 z-50 grid max-h-[90vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 overflow-y-auto rounded-2xl bg-background p-6 text-foreground shadow-[0_0_0_1px_var(--border),0_24px_60px_-16px_rgb(0_0_0/0.35)]", className)}
        {...props}>
        {children}
        <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring">
          <X className="size-4" />
          <span className="sr-only">Close</span>
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  ),
);
DialogContent.displayName = "DialogContent";

const DialogHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col gap-1.5 text-left", className)} {...props} />
);
const DialogFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)} {...props} />
);
const DialogTitle = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Title>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>>(
  ({ className, ...props }, ref) => <DialogPrimitive.Title ref={ref} className={cn("text-lg font-semibold leading-none", className)} {...props} />,
);
DialogTitle.displayName = "DialogTitle";
const DialogDescription = React.forwardRef<React.ElementRef<typeof DialogPrimitive.Description>, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>>(
  ({ className, ...props }, ref) => <DialogPrimitive.Description ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />,
);
DialogDescription.displayName = "DialogDescription";

/** Confirmation (alert dialog): `onConfirm` runs when the person agrees. */
function ConfirmDialog({ open, title, description, confirmLabel, cancelLabel, destructive, onConfirm, onCancel }: {
  open: boolean; title: string; description?: string; confirmLabel: string; cancelLabel: string;
  destructive?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <AlertPrimitive.Root open={open} onOpenChange={(o) => !o && onCancel()}>
      <AlertPrimitive.Portal>
        <AlertPrimitive.Overlay className="cms fixed inset-0 z-50 bg-black/60" />
        <AlertPrimitive.Content className="cms fixed left-1/2 top-1/2 z-50 grid w-full max-w-md -translate-x-1/2 -translate-y-1/2 gap-4 rounded-2xl bg-background p-6 text-foreground shadow-[0_0_0_1px_var(--border),0_24px_60px_-16px_rgb(0_0_0/0.35)]">
          <DialogHeader>
            <AlertPrimitive.Title className="text-lg font-semibold">{title}</AlertPrimitive.Title>
            {description && <AlertPrimitive.Description className="text-sm text-muted-foreground">{description}</AlertPrimitive.Description>}
          </DialogHeader>
          <DialogFooter>
            <AlertPrimitive.Cancel className={buttonVariants({ variant: "outline" })}>{cancelLabel}</AlertPrimitive.Cancel>
            <AlertPrimitive.Action className={buttonVariants({ variant: destructive ? "destructive" : "default" })} onClick={onConfirm}>{confirmLabel}</AlertPrimitive.Action>
          </DialogFooter>
        </AlertPrimitive.Content>
      </AlertPrimitive.Portal>
    </AlertPrimitive.Root>
  );
}

export { Dialog, DialogTrigger, DialogClose, DialogContent, DialogHeader, DialogFooter, DialogTitle, DialogDescription, ConfirmDialog };
