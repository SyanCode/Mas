---
name: launch-checklist
description: Everything a site needs before (and after) going live so it feels finished: domain and HTTPS, favicon set, social image, 404 page, legal pages and consent, forms and emails, analytics, SEO basics, performance, backups and monitoring. Load it for a launch, a "finish the site" goal or a full review.
---

# Launch checklist

Use Mas tools to check what can be checked; list the rest for the person.

## Domain and server
- [ ] The domain and `www` point to the server, one redirects to the other (301); `domain_status` is green.
- [ ] HTTPS everywhere, `http://` redirects; certificate renewal works (`health_checks`).
- [ ] The release is live and healthy; a rollback is possible (`list_sites`, `release_log`).

## Identity
- [ ] Real `<title>` and description on every page (no scaffold text).
- [ ] Favicon set: `favicon.ico` (32 px), `icon.svg`, `apple-touch-icon.png` (180 px), `site.webmanifest` with name
      and theme colour.
- [ ] Open Graph image 1200×630 per page type, with the brand, readable when small.
- [ ] A real 404 page (answers 404, keeps the navigation) and an error page.

## Legal and trust (EU)
- [ ] Legal notice, privacy policy, cookie policy, terms of sale if selling (`legal_publish` on a Mas site).
- [ ] Consent before any non-essential tracker; refusing is as easy as accepting (`compliance_report`).
- [ ] Contact details that work: email, phone, address if local.

## Forms and email
- [ ] Every form sends somewhere real, confirms on screen, protects against spam, and the person receives it.
- [ ] Transactional emails (if any) come from the domain with SPF/DKIM/DMARC.

## Search and speed
- [ ] `seo_report`: robots.txt, sitemap, canonicals, structured data, no broken links (load `seo-checklist`).
- [ ] Images compressed and sized, no blocking script (load `performance`).
- [ ] Search Console / Bing Webmaster verified and the sitemap submitted (a person does it).

## Quality
- [ ] `polish_report` clean: no placeholder, no scaffold leftover, no buzzword copy, no fake proof.
- [ ] Tested at 360 px and on desktop, keyboard only, with images off.

## After launch
- [ ] Statistics flowing (`site_stats`), uptime monitored (`site_uptime`), backups on.
- [ ] Automatic robot maintenance scheduled if wanted (daily/weekly in the Robot page).
