---
name: accessibility
description: WCAG 2.2 AA essentials to check and fix in the code of a site: semantics, keyboard, focus, contrast, images, forms, motion, language. Load it for accessibility or compliance goals and whenever you build a component.
---

# Accessibility (WCAG 2.2 AA essentials)

- **Semantics:** landmarks (`header`, `nav`, `main`, `footer`), one `h1`, ordered headings, lists for lists,
  `button` for actions and `a href` for navigation, tables with `th` and `scope`.
- **Keyboard:** everything works with Tab / Shift+Tab / Enter / Space / Escape, in a logical order; a "Skip to
  content" link; no keyboard trap; menus and dialogs close with Escape and return focus.
- **Focus:** always visible (`:focus-visible` outline ≥ 2 px, 3:1 contrast); never `outline: none` without a
  replacement; not hidden under sticky headers.
- **Contrast:** 4.5:1 text, 3:1 large text, icons and input borders; information never by colour alone.
- **Images:** meaningful `alt`; `alt=""` for decoration; text not baked into images; SVG icons in buttons get an
  accessible name (`aria-label`) when there is no visible text.
- **Forms:** a `<label for>` per field, required fields marked in text, errors announced (`aria-describedby`,
  `aria-invalid`), `autocomplete` attributes, no CAPTCHA without an alternative.
- **Targets:** at least 24×24 px (44 px recommended on touch), enough spacing.
- **Motion and media:** respect `prefers-reduced-motion`; no autoplay with sound; captions for videos; nothing
  flashes more than 3 times a second.
- **Language:** `lang` on `<html>`, `lang` on passages in another language.
- **Dynamic content:** status messages in `role="status"` / `aria-live="polite"`; dialogs with `role="dialog"`,
  `aria-modal`, a label and focus kept inside.
- **Zoom:** usable at 200 % and at 320 px wide without horizontal scroll; no `maximum-scale=1`.

On a Mas site, `compliance_report` lists the accessibility findings of the audit; fix them, deploy, audit again.
