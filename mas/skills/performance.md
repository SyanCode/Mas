---
name: performance
description: How to make a site fast in the code (Core Web Vitals: LCP, CLS, INP): images, fonts, scripts, CSS, caching, third parties, framework settings. Load it for speed goals or when the SEO report shows Core Web Vitals findings.
---

# Performance (Core Web Vitals)

Targets on a mid-range phone: LCP < 2.5 s, CLS < 0.1, INP < 200 ms, page weight under ~1 MB.

## Images (usually the biggest win)
- AVIF/WebP, sized for their display (`srcset` + `sizes`), compressed (quality 70–80).
- The LCP image (hero) is in the HTML, `fetchpriority="high"`, never `loading="lazy"`; other images `loading="lazy"`
  and `decoding="async"`.
- `width` and `height` (or `aspect-ratio`) on every image, video and iframe. Next.js: `next/image`.

## Fonts
- Two families at most, only used weights, WOFF2, `font-display: swap`, preload the main text font, a fallback with
  `size-adjust` to avoid layout shift. Next.js: `next/font`.

## JavaScript
- Scripts `defer` (or `type="module"`); no blocking script in `<head>`.
- Ship less: remove unused libraries, import only what is used, split by route, load heavy widgets (maps, chats,
  video players) on interaction.
- Static pages do not need a JavaScript framework at runtime: prefer static export / SSR with little hydration.
- Long tasks: break them up; no heavy work in click handlers.

## CSS
- One stylesheet strategy, unused CSS purged (Tailwind does it), critical CSS inline only if measured.

## Network and caching
- Hashed static files cached `public, max-age=31536000, immutable`; HTML short or revalidated.
- Compression (gzip/brotli) on text; HTTP/2; no redirect chains.
- Third parties (analytics, chat, fonts from other origins) cost time: keep the strict minimum, `preconnect` the
  ones kept. Mas statistics need no third-party script.

## Layout stability
- Reserve space for banners, embeds, ads and cookie consent; do not insert content above what is already shown.

Check with Lighthouse / PageSpeed Insights when available; `seo_report` flags images without size and blocking
scripts.
