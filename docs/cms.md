# Mas CMS: the `mas.cms.json` reference

Mas lets the clients of a site edit its content (texts, images, articles) from the Mas dashboard,
without access to the code or the server. The site stays yours: Mas never touches its design, it
only writes data the site reads.

A repository opts in with **`mas.cms.json`** at its root. It declares what can be edited:

- **pages**: fixed content, one JSON file each (`content/home.json`): titles, texts, images, lists;
- **collections**: entries the client adds and deletes (articles, news, projects, recipes…), stored
  in the repository or in a Supabase table.

Mas reads `mas.cms.json` from the deployed branch on every edit (never from the browser), builds the
forms from it, checks every value against it, and writes only what it declares.

## Quick start

```json
{
  "$schema": "https://raw.githubusercontent.com/SyanCode/Mas/main/schema/mas.cms.schema.json",
  "pages": [{
    "id": "home", "label": "Home page", "file": "content/home.json",
    "fields": [
      { "key": "title", "type": "text", "label": "Title", "max": 80, "required": true },
      { "key": "intro", "type": "markdown" },
      { "key": "hero", "type": "image", "dir": "public/uploads", "url": "/uploads" }
    ]
  }],
  "collections": [{
    "id": "articles", "label": "Articles", "item_label": "Article", "dir": "content/articles",
    "fields": [
      { "key": "title", "type": "text", "max": 120 },
      { "key": "date", "type": "date", "required": true },
      { "key": "cover", "type": "image", "dir": "public/uploads", "url": "/uploads" },
      { "key": "excerpt", "type": "textarea", "max": 300 },
      { "key": "body", "type": "markdown" }
    ]
  }]
}
```

With `content/home.json` and `content/articles/index.json` (holding `[]`) committed, the site is
**Mas compatible**: the repository gets the badge in *Apps → New app*, and once the app is deployed
an administrator gives the client access from the **CMS** card of the app.

The `$schema` line is optional: it gives completion and checks in VS Code and other editors.

## Top level

| Key | Type | |
|---|---|---|
| `pages` | list of [pages](#pages), at most 50 | |
| `collections` | list of [collections](#collections), at most 20 | |
| `$schema` | string | ignored by Mas |

At least one page or one collection. `id`s are unique across pages and collections.

## Pages

```json
{ "id": "home", "label": "Home page", "description": "Shown above the form", "file": "content/home.json",
  "fields": [ … ] }
```

| Key | Required | |
|---|---|---|
| `id` | yes | letters, digits, `_` and `-`, starts with a letter or `_` |
| `file` | yes | `.json` file in the repository, relative path |
| `fields` | yes | 1 to 100 [fields](#fields) |
| `label` | | name shown to the client (default: the id) |
| `description` | | text shown above the form |

The file holds one JSON object. Mas only changes the keys of the declared fields: **other keys are
kept as they are**, so the file can hold things the client must not edit. A missing file is created
on the first save.

## Collections

```json
{ "id": "articles", "label": "Articles", "item_label": "Article", "dir": "content/articles",
  "fields": [ … ] }
```

| Key | Default | |
|---|---|---|
| `id` | required | as for pages |
| `fields` | required | 1 to 100 [fields](#fields); `slug` is reserved |
| `storage` | `"git"` | `"git"` (files in the repository) or `"supabase"` (a table) |
| `dir` | required with `git` | folder of the entries |
| `table` | the id (`-` → `_`) | table name with `supabase`: `a-z`, `0-9`, `_` |
| `label` | the id | name in the menu |
| `item_label` | `"Entry"` | name of one entry (“+ Add article”) |
| `description` | | text above the list |
| `title_field` | first `text` field | names each entry, gives its address; always required |
| `sort_field` | first `date` field | the list is **newest first** on it (a `date`, `number` or `text` field) |
| `index_fields` | every field but `markdown` | what the list contains (git: `index.json`) |
| `max_entries` | 1000 | at most 1000 |

Each entry has a **slug**: its address, made from the title (`Été à Paris !` → `ete-a-paris`,
`ete-a-paris-2` when taken) or typed by the client when creating it (`a-z`, `0-9`, dashes, 80
characters at most). **It never changes afterwards**, so links keep working.

### `storage: "git"` (default)

Every change is one commit on the deployed branch, which redeploys the site. Mas writes:

- `<dir>/<slug>.json`: the entry, `{ "slug": "ete-a-paris", "title": "…", "body": "…", … }`;
- `<dir>/index.json`: every entry, newest first, with `slug` and the `index_fields` (no markdown
  body), rewritten in the same commit.

`dir` holds only the entries of this collection (no page file, no other collection inside). Commit
`<dir>/index.json` holding `[]` at the start, so the site builds before the first entry. Entries
added by hand in the folder show up in the CMS and enter `index.json` at the next publication.

Reading them, at build time:

```js
// Next.js (app router), Astro, Vite…
import articles from "@/content/articles/index.json";       // [{ slug, title, date, cover, excerpt }, …]
const latest = articles.slice(0, 3);                         // home page section
// app/blog/[slug]/page.tsx
export const generateStaticParams = () => articles.map((a) => ({ slug: a.slug }));
const article = (await import(`@/content/articles/${slug}.json`)).default;
```

```php
<?php
$articles = json_decode(file_get_contents(__DIR__ . "/content/articles/index.json"), true);
$article = json_decode(file_get_contents(__DIR__ . "/content/articles/" . basename($_GET["slug"]) . ".json"), true);
```

A plain static site can `fetch("/content/articles/index.json")` when that folder is served.

### `storage: "supabase"`

The collection is a table of a Supabase project: a publication is online at once (no commit, no
rebuild), and the site can filter, paginate and search.

- **Which project**: the one in the app's environment (`SUPABASE_URL` and
  `SUPABASE_SERVICE_ROLE_KEY`, added by *Connect Supabase* or set by hand for supabase.com), otherwise
  the Supabase deployed by Mas on the server. The service_role key stays in Mas.
- **The table**: `slug` (primary key), one column per field, `created_at`, `updated_at`. Column
  types: `text` for text-like fields and images, `date`, `double precision` (or `bigint` with
  `integer`), `boolean`, `jsonb` for lists. Row level security: anyone may read, only Mas writes.
  The **CMS** card shows the SQL; for the Supabase of the server, **Create the table** runs it. Run
  it again after adding fields: it adds the missing columns.
- **Field keys** are column names: lowercase letters, digits and `_`; not `created_at` or
  `updated_at`.
- **Images** go to the public Storage bucket `mas-cms`, under `<collection id>/`; fields keep their
  full public URL (`dir` and `url` of image fields are ignored).

Reading it with the anon key, at request time (or with revalidation):

```js
import { createClient } from "@supabase/supabase-js";
const supabase = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);
const { data: latest } = await supabase.from("articles")
  .select("slug, title, date, cover, excerpt").order("date", { ascending: false }).limit(3);
const { data: article } = await supabase.from("articles").select("*").eq("slug", slug).single();
```

## Fields

```json
{ "key": "title", "type": "text", "label": "Title", "help": "Shown in the browser tab",
  "placeholder": "My site", "required": true, "max": 80 }
```

Every field takes `key` (required, unique in its page: letters, digits, `_`, `-`), `type` (default
`text`), `label` (default: the key), `help`, `placeholder` and `required`.

| Type | Stored as | Options | Accepted values |
|---|---|---|---|
| `text` | string | `min`, `max` (default 200) | one line |
| `textarea` | string | `min`, `max` (default 2000) | several lines |
| `markdown` | string | `min`, `max` (default 20000) | markdown; render it with your own library |
| `number` | number or `null` | `min`, `max`, `integer` | |
| `boolean` | `true` / `false` | | |
| `select` | string | `options`: `["a", "b"]` or `[{ "value": "a", "label": "A" }]` | one of the values |
| `url` | string | | `https://…`, `http://…`, `/page`, `#anchor`, `mailto:`, `tel:` |
| `email` | string | | an email address |
| `date` | string | | `YYYY-MM-DD` |
| `color` | string | | `#rrggbb` |
| `image` | string (URL) | `dir`, `url`, `max_kb` | an uploaded image |
| `list` | array of objects | `fields`, `min_items`, `max_items` (default 50, at most 200), `item_label` | |

An empty field is stored as `""`, `null` (number), `false` (boolean) or `[]` (list). In a list,
an optional key the client never filled stays absent, so the site may test for it. Lists hold
objects of the other types: **no list inside a list**, and nested objects are not editable.
Control characters are refused.

### Images

`dir` is the folder of the repository where Mas commits uploads, `url` the same folder as the
deployed site serves it. The value saved is `url` + `/` + a fresh file name (`photo-3fa9c2.webp`).

| Site | `dir` | `url` |
|---|---|---|
| Next.js, Vite, Astro, Nuxt (served from `public/`) | `public/uploads` (default) | `/uploads` (default) |
| static or PHP site served from the root | `uploads` | `/uploads` |
| static site served from `site/` | `site/uploads` | `/uploads` |

PNG, JPEG, GIF, WebP and AVIF are accepted, checked by their content (no SVG: it can carry
scripts); `max_kb` defaults to 2048 (at most 8192). The dashboard shrinks big photos before sending
them. An image path already in the file stays accepted; the client cannot type an arbitrary path.

## What Mas refuses

- paths outside the repository, absolute paths, `..`, `.git/`, `.github/`, `mas.cms.json` itself;
- content files that are not `.json`; unknown field types; duplicate ids, keys or files;
- values that do not fit the field (length, type, choices, links other than the ones above);
- a save made on an old version: when the file (or row) changed since the client opened it, the
  client is asked to reload instead of overwriting.

A wrong `mas.cms.json` shows its error in the **CMS** card of the app and as *invalid mas.cms.json*
in the repository list; the client sees nothing until it is fixed.

## Making a site compatible: checklist

1. Every text or image the client should change comes from a JSON file (pages) or a collection,
   not from the components. Keep layout, styles and code-only settings out of these files.
2. One page per screen or shared block (`content/home.json`, `content/contact.json`,
   `content/footer.json`), keys in `camelCase` or `snake_case`, values the site renders directly.
3. Repeated blocks (services, team, FAQ) are `list` fields; articles and anything with its own page
   are collections.
4. Images in the folder the site serves (`public/uploads` for most frameworks).
5. `mas.cms.json` at the root with `$schema`; commit the JSON files with today's content, and
   `index.json` holding `[]` for each git collection.
6. The site builds (git storage) or queries Supabase (supabase storage) with these files only.

Mas can also write `mas.cms.json` for you: the **CMS** card of an app lists the JSON files of the
repository and guesses the fields of the ones you pick.

## With Claude Code

The Mas repository is a Claude Code plugin marketplace. Install the plugin once:

```
/plugin marketplace add SyanCode/Mas
/plugin install mas-cms@mas
```

Then, in the project of a site, ask:

```
Rends ce site compatible Mas
```

or `/mas-cms:make-compatible`. Claude moves the editable content into JSON files, writes
`mas.cms.json` following this reference, wires the site to read them and checks the result. Add
what you want in plain words: “avec un blog stocké dans Supabase”, “les 3 derniers articles sur
l'accueil”.

Without the plugin, one line is enough:

```
Read https://raw.githubusercontent.com/SyanCode/Mas/main/docs/cms.md and make this site Mas compatible.
```
