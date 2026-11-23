---
name: design-craft
description: Visual craft rules for a professional-looking site: typography, spacing, colour, layout, components and their states, imagery, icons, motion and responsive behaviour. Load it for any goal about style, layout, a redesign or a new page.
---

# Design craft

Keep the site's identity: change its execution, not its personality. Start by reading the existing styles (CSS
variables, Tailwind config, theme files) and extend them instead of adding a second system.

## Tokens first
- Define (or reuse) tokens: colours, font sizes, spacing, radii, shadows, durations. Components use tokens, never
  one-off values (`#6d28d9`, `margin: 13px`).
- Spacing on a 4/8 px scale (4, 8, 12, 16, 24, 32, 48, 64, 96). Sections breathe: 64–128 px vertical on desktop.
- Radius: one small (4–6 px) for inputs and buttons, one larger (12–16 px) for surfaces. Not everything rounded-full.
- Shadows: two levels at most, soft and low-contrast; borders often work better than shadows.

## Typography
- One family for text, optionally one with character for headings (or one family in two weights). Load only the
  weights used, `font-display: swap`, self-hosted or `next/font`.
- A modular scale (1.2–1.333): e.g. 14 / 16 / 18 / 22 / 28 / 36 / 48 / 60. Body 16–18 px, line-height 1.5–1.7,
  measure 60–75 characters (`max-width: 65ch`).
- Headings: tighter line-height (1.1–1.25), slight negative letter-spacing on large sizes, sentence case.
- Hierarchy by size and weight, not by colour alone. Two or three text colours: primary, secondary, muted.
- French: non-breaking space before `: ; ! ?` and inside « guillemets »; typographic apostrophe ’.

## Colour
- Neutrals tinted toward the brand hue (not pure grey), one accent for actions and focus, semantic colours for
  success / warning / error. The accent is rare: if everything is highlighted, nothing is.
- Contrast: 4.5:1 for text, 3:1 for large text and UI borders, in light and dark mode.
- Dark mode only if it is designed (not inverted): lower contrast surfaces, desaturated accents.

## Layout
- A container with a max width (1100–1280 px) and consistent side padding (16–24 px mobile, 32–48 px desktop).
- A grid: align things to it. Asymmetry is fine when it is intended; random offsets are not.
- Rhythm: alternate dense and airy sections; do not give every section the same template.
- Content first on mobile: the order of the DOM is the order on a phone.

## Components
- Every interactive element has hover, focus-visible, active, disabled and loading states. Focus is always visible.
- Buttons: one primary per view, secondary and ghost variants; 40–48 px high; verbs as labels.
- Forms: visible labels (not placeholder-only), help text, inline error messages, success state, sensible input
  types (`email`, `tel`, `autocomplete`).
- Empty, loading (skeletons sized like the content) and error states exist for every data view.

## Imagery and icons
- Real photos, consistent in colour treatment and crop; `object-fit: cover` with fixed aspect ratios.
- One icon set, one stroke width, sized with the text (16/20/24). No emoji as UI icons.

## Motion
- 150–250 ms, ease-out for entrances, ease-in for exits; animate opacity and transform only.
- Motion explains (a panel opens from its button); it never delays reading. Respect `prefers-reduced-motion`.

## Responsive
- Check 360, 768, 1024, 1440 px. No horizontal scroll, tap targets ≥ 44 px, images never stretched, tables scroll
  inside their container, navigation usable with one thumb.
