"use client";

/**
 * Adapters between Mas and the Ruixen UI components (components/ruixen, MIT): no sound, Mas defaults,
 * controlled-looking props. The vendored files stay as published so they can be refreshed.
 */

import { animate, motion, useMotionValue, useReducedMotion, useTransform } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";
import { Bell } from "lucide-react";
import BetterTimePicker from "@/components/ruixen/better-time-picker";
import CleanTagInput from "@/components/ruixen/clean-tag-input";
import ColorPickerInput from "@/components/ruixen/color-picker-input";
import { InvertTabs } from "@/components/ruixen/invert-tabs";
import { MagneticTabs } from "@/components/ruixen/magnetic-tabs";
import { NotificationInboxPopover, type InboxItem } from "@/components/ruixen/notification-inbox-popover";
import { SlidingPagination } from "@/components/ruixen/sliding-pagination";
import { SparkChart } from "@/components/ruixen/spark-chart";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/cms/ui/popover";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { BRAND } from "@/lib/brand";

export type TabItem = { value: string; label: string };

/** A number that counts up when it first appears and whenever it changes (instant with reduced motion). */
export function RollingNumber({ value, format }: { value: number; format: (n: number) => string }) {
  const reduce = useReducedMotion();
  const mv = useMotionValue(reduce ? value : 0);
  const text = useTransform(mv, (v) => format(Math.round(v)));
  useEffect(() => {
    if (reduce) { mv.set(value); return; }
    const controls = animate(mv, value, { duration: 0.9, ease: [0.16, 1, 0.3, 1] });
    return () => controls.stop();
  }, [value, reduce, mv]);
  return <motion.span>{text}</motion.span>;
}

/** Sparkline with a scrub label: data, optional colour and a formatter for the value under the cursor. */
export function Trend({ data, color = BRAND, height = 72, format, className }: {
  data: number[]; color?: string; height?: number; format?: (n: number) => string; className?: string;
}) {
  if (data.length < 2) return null;
  return <SparkChart data={data} color={color} height={height} formatValue={format} className={className} />;
}

/** Pill tabs that follow the pointer (Ruixen MagneticTabs). Uncontrolled: `value` is the first selection. */
export function PillTabs({ items, value, onChange, className }: { items: TabItem[]; value: string; onChange: (v: string) => void; className?: string }) {
  return <MagneticTabs items={items} defaultValue={value} onChange={onChange} sound={false} className={className} />;
}

/** Segmented filter with an inverted active pill (Ruixen InvertTabs). */
export function SegmentTabs({ items, value, onChange, className }: { items: TabItem[]; value: string; onChange: (v: string) => void; className?: string }) {
  return <InvertTabs items={items} defaultValue={value} onChange={onChange} sound={false} className={className} />;
}

/** Tag input for lists such as e-mail addresses. Remount (change `key`) to load other values. */
export function TagField({ label, hint, placeholder, value, onChange }: { label: string; hint?: string; placeholder?: string; value: string[]; onChange: (v: string[]) => void }) {
  return <CleanTagInput label={label} hint={hint} placeholder={placeholder} defaultTags={value} onChange={onChange} sound={false} style={{ width: "100%" }} />;
}

/** Colour picker with swatches (Ruixen ColorPickerInput); gives #rrggbb. */
export function ColorField({ value, onChange }: { value: string; onChange: (hex: string) => void }) {
  return <ColorPickerInput initialColor={value} initialOpacity={100} sound={false} onChange={(hex) => onChange(hex.slice(0, 7))} style={{ width: "100%" }} />;
}

/** Time of day (Ruixen BetterTimePicker), 24 h. */
export function TimeField({ hour, minute, onChange }: { hour: number; minute: number; onChange: (h: number, m: number) => void }) {
  return <BetterTimePicker defaultHour={hour} defaultMinute={minute} use24 minuteStep={5} sound={false} onChange={onChange} style={{ width: "100%" }} />;
}

/** Page numbers that slide (Ruixen SlidingPagination); pages start at 1. */
export function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  return <SlidingPagination totalPages={pages} currentPage={page} maxVisiblePages={5} onPageChange={onPage} sound={false} />;
}

export type { InboxItem };

/** The bell of the top bar: a popover with the Ruixen inbox inside. Read marks are kept by the caller. */
export function InboxBell({ items, onMarkRead, onMarkAllRead, trigger }: {
  items: InboxItem[]; onMarkRead: (id: string) => void; onMarkAllRead: () => void; trigger?: ReactNode;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const unread = items.filter((i) => !i.read).length;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {trigger ?? (
          <button type="button" aria-label={t("Notifications")} className="relative inline-flex size-9 items-center justify-center rounded-md hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Bell className="size-4" />
            {unread > 0 && <span className={cn("absolute right-1.5 top-1.5 size-2 rounded-full bg-brand ring-2 ring-card")} />}
          </button>
        )}
      </PopoverTrigger>
      <PopoverContent>
        <NotificationInboxPopover items={items} onMarkRead={onMarkRead} onMarkAllRead={onMarkAllRead} sound={false}
          labels={{ inbox: t("Inbox"), markAllRead: t("Mark all read") }} />
      </PopoverContent>
    </Popover>
  );
}
