"use client";

import { useId } from "react";

/** What the robot is doing, shown on its face and its antenna. */
export type Mood = "idle" | "working" | "waiting" | "happy" | "sad" | "sleep";

const LIGHT: Record<Mood, string> = {
  idle: "#818cf8", working: "#38bdf8", waiting: "#f59e0b", happy: "#34d399", sad: "#fb7185", sleep: "#a1a1aa",
};

/**
 * Mas's robot: a rounded head with a dark visor (the eyes show its mood), an antenna whose light gives its state,
 * and the Mas hexagon on its chest. Pure SVG, animated with CSS (see .mascot-* in globals.css), which stops
 * for people who ask for reduced motion.
 */
export function MascotBot({ mood = "idle", size = 64, wave = false, className = "" }: {
  mood?: Mood; size?: number; wave?: boolean; className?: string;
}) {
  const id = useId().replace(/:/g, "");
  const light = LIGHT[mood];
  const eye = `url(#${id}-eye)`;
  return (
    <svg viewBox="0 0 120 140" width={size} height={(size * 140) / 120} className={`mascot mascot-${mood} ${className}`} aria-hidden>
      <defs>
        <linearGradient id={`${id}-shell`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="1" stopColor="#dfe3ee" />
        </linearGradient>
        <linearGradient id={`${id}-visor`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#1b2036" />
          <stop offset="1" stopColor="#0b0e1a" />
        </linearGradient>
        <linearGradient id={`${id}-eye`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={mood === "idle" || mood === "working" ? "#a5f3fc" : light} />
          <stop offset="1" stopColor={mood === "idle" || mood === "working" ? "#818cf8" : light} />
        </linearGradient>
        <linearGradient id={`${id}-logo`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="0.5" stopColor="#38bdf8" />
          <stop offset="1" stopColor="#e879f9" />
        </linearGradient>
        <radialGradient id={`${id}-glow`}>
          <stop offset="0" stopColor={light} stopOpacity="0.9" />
          <stop offset="1" stopColor={light} stopOpacity="0" />
        </radialGradient>
        <clipPath id={`${id}-clip`}><rect x="29" y="33" width="62" height="40" rx="14" /></clipPath>
      </defs>

      <ellipse className="mascot-shadow" cx="60" cy="133" rx="26" ry="4" fill="#0f172a" opacity="0.16" />

      <g className="mascot-float">
        {/* antenna */}
        <line x1="60" y1="23" x2="60" y2="11" stroke="#9aa3b8" strokeWidth="3" strokeLinecap="round" />
        <circle className="mascot-halo" cx="60" cy="8" r="11" fill={`url(#${id}-glow)`} />
        <circle className="mascot-light" cx="60" cy="8" r="5" fill={light} />

        {/* ears */}
        <rect x="12" y="43" width="10" height="22" rx="5" fill="#c7cde0" />
        <rect x="98" y="43" width="10" height="22" rx="5" fill="#c7cde0" />
        <circle cx="17" cy="54" r="2" fill={light} opacity="0.8" />
        <circle cx="103" cy="54" r="2" fill={light} opacity="0.8" />

        {/* head */}
        <rect x="19" y="21" width="82" height="64" rx="24" fill={`url(#${id}-shell)`} stroke="#c3c9da" strokeWidth="1.2" />
        <rect x="29" y="33" width="62" height="40" rx="14" fill={`url(#${id}-visor)`} />
        <path d="M36 39c6-3 15-4 22-3" stroke="#ffffff" strokeOpacity="0.18" strokeWidth="3" strokeLinecap="round" fill="none" />

        {/* face */}
        <g clipPath={`url(#${id}-clip)`}>
          {mood === "working" && <rect className="mascot-scan" x="29" y="33" width="14" height="40" fill="#38bdf8" opacity="0.18" />}
          {(mood === "idle" || mood === "working") && (
            <g className={mood === "working" ? "mascot-look" : "mascot-blink"}>
              <rect x="42" y="45" width="10" height="16" rx="5" fill={eye} />
              <rect x="68" y="45" width="10" height="16" rx="5" fill={eye} />
            </g>
          )}
          {mood === "waiting" && (
            <g>
              <circle cx="47" cy="52" r="7" fill={eye} />
              <circle cx="73" cy="52" r="7" fill={eye} />
              <circle cx="49" cy="50" r="2.2" fill="#0b0e1a" />
              <circle cx="75" cy="50" r="2.2" fill="#0b0e1a" />
              <ellipse cx="60" cy="66" rx="3" ry="2.5" fill="none" stroke={light} strokeWidth="2" />
            </g>
          )}
          {mood === "happy" && (
            <g fill="none" stroke={eye} strokeWidth="4" strokeLinecap="round">
              <path d="M41 56q6-9 12 0" />
              <path d="M67 56q6-9 12 0" />
              <path d="M54 64q6 5 12 0" strokeWidth="2.5" />
            </g>
          )}
          {mood === "sad" && (
            <g fill="none" stroke={eye} strokeWidth="4" strokeLinecap="round">
              <path d="M41 55l11-5M79 55l-11-5" />
              <path d="M54 67q6-5 12 0" strokeWidth="2.5" />
            </g>
          )}
          {mood === "sleep" && (
            <g fill="none" stroke="#7c8399" strokeWidth="3.5" strokeLinecap="round">
              <path d="M41 55q6 4 12 0M67 55q6 4 12 0" />
            </g>
          )}
          {(mood === "idle" || mood === "working") && (
            <path d="M55 66q5 3 10 0" fill="none" stroke="#818cf8" strokeOpacity="0.7" strokeWidth="2" strokeLinecap="round" />
          )}
        </g>
        {mood === "sleep" && <text className="mascot-z" x="92" y="26" fontSize="13" fontWeight="700" fill="#a1a1aa">z</text>}

        {/* neck and body */}
        <rect x="52" y="84" width="16" height="7" rx="2" fill="#b7bed2" />
        <g className={wave ? "mascot-wave" : "mascot-arm"}>
          <rect x="88" y="94" width="10" height="24" rx="5" fill="#c7cde0" />
        </g>
        <rect x="22" y="94" width="10" height="24" rx="5" fill="#c7cde0" />
        <rect x="30" y="89" width="60" height="38" rx="16" fill={`url(#${id}-shell)`} stroke="#c3c9da" strokeWidth="1.2" />
        <g transform="translate(48 96) scale(0.75)">
          <path d="M16 2 29 9.5v13L16 30 3 22.5v-13Z" fill="#11152a" stroke={`url(#${id}-logo)`} strokeWidth="2.6" strokeLinejoin="round" />
          <path d="M9.5 21V12l6.5 6 6.5-6v9" fill="none" stroke={`url(#${id}-logo)`} strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        </g>
        <circle className="mascot-light" cx="80" cy="99" r="2.2" fill={light} />
      </g>
    </svg>
  );
}
