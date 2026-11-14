"use client";

import { useState } from "react";

export interface Named { username: string; nickname?: string | null; display_name?: string | null }

/** What to call someone: their nickname, else their full name, else their login. */
export function shownName(p: Named): string {
  return p.nickname || p.display_name || p.username;
}

function initials(name: string): string {
  const words = name.trim().split(/[\s._-]+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2);
  return letters.toUpperCase();
}

function hue(name: string): number {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

/** A round picture; without one (or when it cannot load) the initials on a colour taken from the name. */
export function Avatar({ name, src, size = 32, className = "" }: { name: string; src?: string | null; size?: number; className?: string }) {
  const [failed, setFailed] = useState(false);
  const box = { width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.38)) };
  if (src && !failed) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt="" onError={() => setFailed(true)} style={box} className={`shrink-0 rounded-full object-cover ring-2 ring-white/10 ${className}`} />;
  }
  return (
    <span style={{ ...box, background: `hsl(${hue(name)} 55% 42%)` }} aria-hidden
      className={`inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white ring-2 ring-white/10 ${className}`}>
      {initials(name)}
    </span>
  );
}

/** URL of the picture of the signed-in account (the version changes when it does). */
export const myAvatarUrl = (s: { has_avatar?: boolean; avatar_v?: number }) => (s.has_avatar ? `/api/account/avatar?v=${s.avatar_v ?? 0}` : null);

/** URL of the picture of another account (administrators). */
export const userAvatarUrl = (u: { id: number; has_avatar?: boolean; avatar_v?: number }) => (u.has_avatar ? `/api/users/${u.id}/avatar?v=${u.avatar_v ?? 0}` : null);

/** Shrink a chosen picture to a 256 x 256 JPEG (centred crop) before it is sent: small, and re-encoded by the browser. */
export async function shrinkPicture(file: File, size = 256): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const side = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas");
  ctx.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, size, size);
  return canvas.toDataURL("image/jpeg", 0.85);
}
