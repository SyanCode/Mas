---
name: seo-checklist
description: The 22-point SEO checklist (indexing, breadcrumbs, canonicals, Core Web Vitals, orphans, schema, headings, content, URLs, 404s, JavaScript, cannibalization, mobile, E-E-A-T, titles, metas, thin pages, images, intent, keywords, internal links), with how to check each point and how to fix it in the code. Load it for any SEO goal or before publishing pages.
---

# SEO checklist (22 points)

Work from facts, never from guesses. On a Mas site, `seo_report` crawls the live site and returns a `checklist`
array with the status of each point below: `ok`, `fix` (with the finding ids) or `manual` (needs judgement).
Fix the `fix` points in the code, deploy, then run `seo_report` again. For `manual` points, do what the content of the
repository lets you do honestly and list the rest for the person.

Priority order: what stops indexing (1, 11, 12) → what search engines read first (16, 17, 7, 3) → structure
(2, 5, 6, 10, 22) → speed and mobile (4, 14, 19) → content quality (8, 13, 15, 18, 20) → strategy (9, 21).

## Technical

1. **Fix indexing issues.** No `noindex` on pages meant to rank; `robots.txt` does not `Disallow: /`; a `sitemap.xml`
   lists every public page and robots.txt points to it (`seo_optimize` writes both on a Mas site); `http://` and
   `www.` redirect (301) to one canonical origin; every listed page answers 200.
2. **Add breadcrumbs.** On every page deeper than one level: a visible trail (`Home › Blog › Title`) in a `<nav
   aria-label="Breadcrumb">` with an `<ol>`, and the same trail as `BreadcrumbList` JSON-LD.
3. **Add canonical tags.** Each page has `<link rel="canonical" href="https://domain/clean-path">` pointing to itself
   (absolute URL, the one in the sitemap). Variants (filters, `?utm=`, pagination duplicates) point to the main page.
4. **Fix Core Web Vitals.** LCP < 2.5 s: the hero image is preloaded or in the first HTML, compressed (AVIF/WebP),
   never lazy-loaded; fonts with `font-display: swap` and at most two families. CLS < 0.1: every `<img>`/`<video>`/
   iframe has `width` and `height` (or `aspect-ratio`), no banner pushed in above the content. INP < 200 ms: scripts
   `defer`, no heavy work on click, third-party scripts kept to the strict minimum.
5. **Fix orphan pages.** Every page of the sitemap is linked from at least one other page (menu, footer, hub page,
   related posts). An orphan either gets links or leaves the sitemap.
6. **Add schema markup.** JSON-LD in `<script type="application/ld+json">`: `Organization` (or `LocalBusiness` with
   real address and opening hours) on the home page, `WebSite`, `Article`/`BlogPosting` (author, dates), `Product`
   (price, availability), `FAQPage` only for a visible FAQ, `BreadcrumbList`. Values must match what the page shows.
10. **Use clean, clear URLs.** Lowercase, words separated by hyphens, short, descriptive, no IDs or parameters for
    content pages (`/blog/sourdough-starter`, not `/post?id=42`). Changing an existing URL needs a 301 from the old.
11. **Fix broken links & 404s.** No internal link to a missing page (`links-broken` lists them). Removed pages get a
    301 to their closest replacement. The 404 page is real: it answers 404, keeps the site navigation, offers a search
    or the main links.
12. **Make JS crawlable.** The text and links of each page are in the HTML the server sends (SSR, static export,
    prerendering): not only after JavaScript runs. Links are real `<a href>`, not `onClick` navigation.
14. **Go mobile-friendly.** Viewport meta, no horizontal scroll at 360 px, tap targets ≥ 44 px, body text ≥ 16 px,
    no content hidden on mobile that exists on desktop.

## On-page

7. **Fix H1 / H2 structure.** One `h1` per page that says what the page is about (contains the main topic); `h2` for
   each section; no level skipped; headings are headings, not styling.
16. **Titles: 50–60 characters.** Unique per page, the main topic first, the brand last (`Sourdough starter guide –
    Dupont Bakery`). No `Home`, no `Untitled`, no keyword stuffing.
17. **Write unique metas.** A `meta description` of 120–160 characters per page, written for that page: what the
    reader gets and why click. Open Graph (`og:title`, `og:description`, `og:image` 1200×630) and `twitter:card`.
19. **Optimize images + alt.** Modern formats, sized to their display size (`srcset`), lazy below the fold, files named
    with words (`sourdough-loaf.webp`). `alt` describes the image for someone who cannot see it; `alt=""` for decoration.
22. **Internal linking.** Every important page receives links from related pages with descriptive anchor text (never
    "click here"); hub pages link to their children and back.

## Content

8. **Write original content.** Use what only this business knows: its products, prices, places, process, cases,
   photos. Never paste generic text. Never invent facts: leave a clear placeholder and tell the person.
13. **Fix cannibalization.** One page per search intent. Two pages with the same title/h1/topic: merge them (301 the
    weaker) or give each a distinct angle.
15. **Author bio + E-E-A-T.** Articles show a real author with a short bio, credentials, photo and a link to an author
    page; the site has an About page, contact details, legal notice. Do not invent a person or credentials: ask.
18. **Fix thin pages.** A page under ~300 useful words that should rank gets real content (answers, examples, data),
    or is merged, or is `noindex` if it only exists for navigation.
20. **Match search intent.** Look at what the page must answer (buy, learn, compare, find a place) and shape it for
    that: a product page sells, a guide explains step by step, a local page shows address, hours, map.

## Strategy (needs a person or external data)

9. **Get quality backlinks.** Not something to fake. Suggest concrete, honest actions: local directories, partners,
   press, guest content, useful free resources. Never buy links or generate spam.
21. **High volume, low KD.** Keyword research needs a tool (Search Console, Ahrefs, Semrush…). `site_stats` shows the
    pages that already bring visits: strengthen those. Ask the person for their target queries if the goal needs them.

## Report

End with the checklist: each point ok / fixed now / needs a person (and what exactly).
