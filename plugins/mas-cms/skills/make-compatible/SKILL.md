---
name: make-compatible
description: Make a website editable from the Mas CMS ("compatible Mas"). Moves the texts, images and articles the client should edit into JSON files (or a Supabase table), writes mas.cms.json, wires the site to read them without changing its design, and checks the result. Use when the user asks to make a site Mas compatible, editable with Mas, to add mas.cms.json, a Mas blog or articles section, or "rends ce site compatible Mas".
---

# Make a site Mas compatible

Mas is a self-hosted deployment dashboard with a git-based CMS. A site is **Mas compatible** when
`mas.cms.json` at the root of its repository declares what the client may edit. Mas builds forms from
it and writes only those values: page content in JSON files, collections (articles…) as JSON files
or Supabase rows. The site keeps its design and reads the data.

The full format is in `reference.md`, next to this file (the same text as `docs/cms.md` in the Mas
repository). Read it before writing `mas.cms.json`. The JSON Schema for editors is
`https://raw.githubusercontent.com/SyanCode/Mas/main/schema/mas.cms.schema.json`.

## Method

1. **Understand the site.** Framework and rendering (Next.js app/pages router, Astro, Vite/React,
   Nuxt, SvelteKit, PHP, plain HTML), where texts and images live today, how it is built, whether it
   already uses Supabase. The site's language is usually the client's: write labels in it.

2. **List what the client should edit**, page by page: headings, paragraphs, buttons and links,
   contact details, images, repeated blocks (services, team, FAQ, testimonials, prices), and any
   blog/news/projects with their own pages. Leave out layout, styles and code-only settings. If the
   request names a collection ("un blog", "des actus"), add it even if the site has none yet.

3. **Choose the storage of each collection.** `git` by default (simple, versioned, the site
   rebuilds on each change). `supabase` when the user asks for it, when the site already uses
   Supabase, or when entries must appear without a rebuild; field keys are then `snake_case`.

4. **Create the content files** with today's content, verbatim:
   - one JSON object per page or shared block: `content/home.json`, `content/footer.json`…
     (use the project's existing content folder if there is one);
   - repeated blocks become lists of objects; long rich text becomes `markdown`;
   - git collections: `<dir>/<slug>.json` per existing entry (with its `slug`) and
     `<dir>/index.json` = every entry newest first without the markdown body (`[]` if none);
     existing markdown posts are converted (front matter → fields, body → `body`);
   - editable images go to the folder the site serves (`public/uploads` for Next.js, Vite, Astro,
     Nuxt; the web root for static and PHP sites), or keep their current folder and set the image
     field's `dir`/`url` to it.

5. **Wire the site to the files** without changing its look: replace hard-coded strings and image
   paths in components by values from the JSON files, keep markup, classes and styles. Render
   `markdown` fields with the markdown tool the project already has (otherwise a small, common one).
   For collections build what the user asked, in the site's style: typically the latest 3 on the home
   page (`index.slice(0, 3)` or `.limit(3)`), a page listing all entries, and one page per slug.
   Supabase: read with the anon key (`NEXT_PUBLIC_SUPABASE_URL`/`_ANON_KEY` or the project's
   variables), at request time or with revalidation.

6. **Write `mas.cms.json`** at the repository root, starting with the `$schema` line. One page per
   content file, fields in the order they appear on screen, clear `label`s and short `help` in the
   site's language, sensible `max` lengths, `required` where an empty value would break the layout,
   `item_label` for lists and collections.

7. **Check.** From the repository root, run `check.py`, which sits next to this file
   (`python3 <this skill's folder>/check.py`, standard library only). It applies Mas's rules to
   `mas.cms.json` and to the files it points to. Fix every error it prints. Then build or run the
   site the way the project does and make sure every page still shows the same content.

8. **Report** briefly: what the client can now edit (pages, fields, collections and their storage),
   the files created, and the next steps in Mas: deploy the app, then give the client access from
   the **CMS** card of the app; for Supabase collections, create the table from the same card (or run
   its SQL in the Supabase SQL editor) and connect Supabase to the app.

## Rules that are easy to miss

- Paths are relative to the repository root; never `..`, `.git/`, `.github/` or `mas.cms.json`.
- Page files are `.json` objects. A collection `dir` holds only its entries (no page file in it).
- Field types: `text`, `textarea`, `markdown`, `number`, `boolean`, `select`, `url`, `email`,
  `date`, `color`, `image`, `list`. No list inside a list; nested objects are not editable, so
  flatten them (`seo.title` → `seoTitle`) or make them their own page.
- `url` values: `https://`, `http://`, `/path`, `#anchor`, `mailto:`, `tel:`. `date`: `YYYY-MM-DD`.
  `color`: `#rrggbb`. Existing values must already respect these, or the client cannot save.
- `slug` is reserved in collections. Supabase collections: keys `^[a-z_][a-z0-9_]*$`, not
  `created_at`/`updated_at`.
- Keys not declared in `mas.cms.json` stay untouched in the files: keep code-only values there if
  the site needs them, they are not shown to the client.
