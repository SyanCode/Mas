"""The polish report: mechanical signs that a project was generated and never finished ("vibecoded").

It reads the text files of a repository (paths and contents given by the caller) and reports placeholders, scaffold
leftovers, buzzword copy, emoji used as icons, unverifiable numbers, dead links, debug code and missing finishing files
(favicon, 404 page, social image, README). It judges nothing it cannot see: design and wording still need the
``not-vibecoded`` skill and a person. Pure functions, no network: the robot feeds it files read from GitHub.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from pathlib import PurePosixPath

TEXT_EXT = {".html", ".htm", ".tsx", ".jsx", ".ts", ".js", ".mjs", ".vue", ".svelte", ".astro", ".md", ".mdx",
            ".css", ".scss", ".json", ".php", ".twig", ".njk", ".liquid", ".hbs", ".ejs", ".py", ".txt"}
UI_EXT = {".html", ".htm", ".tsx", ".jsx", ".vue", ".svelte", ".astro", ".md", ".mdx", ".php", ".twig", ".njk",
          ".liquid", ".hbs", ".ejs", ".json"}
CODE_EXT = {".tsx", ".jsx", ".ts", ".js", ".mjs", ".vue", ".svelte", ".astro"}
SKIP_PARTS = {"node_modules", ".git", ".github", "dist", "build", ".next", "out", ".nuxt", ".svelte-kit", "vendor",
              "coverage", ".cache", ".claude", "__pycache__", ".venv", "venv"}
SKIP_NAMES = re.compile(r"(lock\.json|\.lock|lock\.yaml|\.min\.(js|css)|\.map|\.d\.ts|CHANGELOG|LICENSE)$", re.I)
MAX_FILES = 60
MAX_BYTES = 120_000

BUZZWORDS = [
    "unlock", "unleash", "elevate your", "empower", "supercharge", "revolutioni", "seamless", "effortless",
    "cutting-edge", "cutting edge", "state-of-the-art", "game-changer", "game changer", "next level", "next-level",
    "world-class", "best-in-class", "harness the power", "fast-paced world", "delve", "look no further",
    "tailored solutions", "welcome to our website", "take your business to", "transform your",
    "révolutionne", "boostez", "sublimez", "de pointe", "n'attendez plus", "n’attendez plus",
    "monde en constante évolution", "solutions sur mesure", "bienvenue sur notre site",
]
PLACEHOLDERS = [
    (re.compile(r"lorem ipsum|dolor sit amet", re.I), "lorem ipsum"),
    (re.compile(r"\b(your company|company name|your brand|nom de (?:votre|l')entreprise|votre entreprise)\b", re.I), "company placeholder"),
    (re.compile(r"\b(john|jane) doe\b", re.I), "John Doe"),
    (re.compile(r"\b123 (main|fake) st", re.I), "fake address"),
    (re.compile(r"\(555\)|\b555-\d{4}\b|\+1 ?\(?555", re.I), "fake phone"),
    (re.compile(r"\b(?:contact|hello|info|test)@example\.(?:com|org)\b", re.I), "example.com email"),
]
SCAFFOLD = [
    (re.compile(r"<title>\s*(Vite \+ \w+|React App|Create Next App|Vite App|Astro|SvelteKit app|Nuxt)\s*</title>|title:\s*['\"]Create Next App['\"]", re.I), "default page title"),
    (re.compile(r"Edit <code>src/App|Get started by editing|Click on the Vite and React logos|count is \{count\}", re.I), "framework demo text"),
    (re.compile(r"This is a \[Next\.js\]\(https://nextjs\.org\) project bootstrapped|# React \+ Vite|bootstrapped with \[Create React App\]", re.I), "bootstrap README"),
    (re.compile(r"/vite\.svg|/next\.svg|/vercel\.svg|react\.svg", re.I), "scaffold logo"),
]
FAKE_PROOF = re.compile(
    r"\b\d{1,3}(?:[,.\s]\d{3})*\s?(?:\+|k\+)\s*(?:happy |satisfied )?(?:customers|clients|users|companies|businesses|"
    r"clients satisfaits|utilisateurs|entreprises)\b|★★★★★|⭐⭐⭐⭐⭐|trusted by (?:over |more than )?\d", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐]")
GRADIENT = re.compile(r"from-(?:purple|violet|indigo|fuchsia)-\d{3}[^\"'`]*to-(?:pink|purple|violet|blue|fuchsia|cyan)-\d{3}|"
                      r"linear-gradient\([^)]*(?:#(?:8b5cf6|7c3aed|6366f1|a855f7|ec4899)|purple|violet)", re.I)
GRADIENT_TEXT = re.compile(r"bg-clip-text[^\"'`]*text-transparent|text-transparent[^\"'`]*bg-clip-text|background-clip:\s*text")
DEAD_LINK = re.compile(r"href=[\"']#[\"']|href=\{?[\"']#[\"']\}?")
DEBUG = re.compile(r"\bconsole\.log\(|\bdebugger;|\balert\(")
TODO = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
COPYRIGHT = re.compile(r"(?:©|&copy;|copyright)\s*(20\d\d)(?!\s*[-–]\s*20\d\d)", re.I)


def wanted(path: str) -> bool:
    p = PurePosixPath(path)
    return (p.suffix.lower() in TEXT_EXT and not any(part in SKIP_PARTS for part in p.parts)
            and not SKIP_NAMES.search(p.name) and not p.name.startswith(".env"))


def pick(tree: Iterable[dict], limit: int = MAX_FILES) -> list[str]:
    """The files worth reading, the user-facing ones first (pages, components, content, README)."""
    rows = [t for t in tree if wanted(t["path"]) and int(t.get("size") or 0) <= MAX_BYTES]

    def rank(t: dict) -> tuple:
        path = t["path"].lower()
        top = any(path.startswith(x) for x in ("src/", "app/", "pages/", "components/", "content/", "public/", "index.html", "readme"))
        return (0 if PurePosixPath(path).suffix in UI_EXT else 1, 0 if top else 1, path.count("/"), path)
    return [t["path"] for t in sorted(rows, key=rank)[:limit]]


def finding(fid: str, level: str, title: str, fix: str, hits: dict[str, list[str]] | None = None) -> dict:
    out = {"id": fid, "level": level, "title": title, "fix": fix}
    if hits:
        out["count"] = sum(len(v) for v in hits.values())
        out["where"] = [f"{path}: {', '.join(sorted(set(v))[:4])}" for path, v in list(hits.items())[:8]]
    return out


def _scan(files: dict[str, str], ext: set[str], test) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for path, text in files.items():
        if PurePosixPath(path).suffix.lower() not in ext:
            continue
        found = test(text)
        if found:
            hits[path] = found
    return hits


def report(tree_paths: Iterable[str], files: dict[str, str], year: int | None = None) -> dict:
    """``tree_paths``: every path of the repository; ``files``: the text of the files read (see ``pick``)."""
    year = year or time.gmtime().tm_year
    paths = [p for p in tree_paths if not any(part in SKIP_PARTS for part in PurePosixPath(p).parts)]
    lower = [p.lower() for p in paths]
    names = {PurePosixPath(p).name for p in lower}
    out: list[dict] = []

    hits = _scan(files, UI_EXT | CODE_EXT, lambda t: [label for rx, label in PLACEHOLDERS if rx.search(t)])
    if hits:
        out.append(finding("placeholder", "error", "Placeholder content is still there",
                           "Replace it with the business's real data, or ask the person for it.", hits))
    hits = _scan(files, TEXT_EXT, lambda t: [label for rx, label in SCAFFOLD if rx.search(t)])
    if hits:
        out.append(finding("scaffold", "error", "Leftovers of the project template",
                           "Remove the demo text, logos and README of the framework; give the site its own title.", hits))
    hits = _scan(files, UI_EXT | CODE_EXT, lambda t: [w for w in BUZZWORDS if w in t.lower()])
    distinct = {w for v in hits.values() for w in v}
    if len(distinct) >= 2 or sum(len(v) for v in hits.values()) >= 3:
        out.append(finding("buzzwords", "warn", "Generic marketing words that read as AI-written",
                           "Rewrite with concrete facts of the business (what, for whom, where, how much).", hits))
    hits = _scan(files, UI_EXT | CODE_EXT, lambda t: FAKE_PROOF.findall(t))
    if hits:
        out.append(finding("fake-proof", "warn", "Numbers or ratings that look invented",
                           "Keep only real, verifiable figures and reviews; remove the rest.", hits))
    emoji = _scan(files, UI_EXT | CODE_EXT, lambda t: EMOJI.findall(t))
    if sum(len(v) for v in emoji.values()) >= 5:
        out.append(finding("emoji-icons", "warn", "Emoji used as icons",
                           "Use one icon set (Lucide, Phosphor, Heroicons) at one stroke width, or no icon.", emoji))
    grad = _scan(files, UI_EXT | CODE_EXT | {".css", ".scss"}, lambda t: GRADIENT.findall(t) + GRADIENT_TEXT.findall(t))
    if sum(len(v) for v in grad.values()) >= 3:
        out.append(finding("ai-gradients", "info", "The purple-gradient look of generated sites",
                           "Use the brand's colour with restraint on tinted neutrals; keep gradients only if the brand has them.",
                           {k: ["gradient"] * len(v) for k, v in grad.items()}))
    dead = _scan(files, UI_EXT | CODE_EXT, lambda t: DEAD_LINK.findall(t))
    if sum(len(v) for v in dead.values()) >= 2:
        out.append(finding("dead-links", "warn", "Links that go nowhere (href=\"#\")",
                           "Point each link to its page, or turn it into a button that does something, or remove it.",
                           {k: ["#"] * len(v) for k, v in dead.items()}))
    debug = _scan(files, CODE_EXT, lambda t: DEBUG.findall(t))
    if debug:
        out.append(finding("debug-code", "info", "Debug code left in (console.log, alert, debugger)",
                           "Remove it, or log errors through a real handler.", debug))
    todo = _scan(files, CODE_EXT | UI_EXT, lambda t: TODO.findall(t))
    if sum(len(v) for v in todo.values()) >= 3:
        out.append(finding("todo", "info", "Unfinished work marked TODO / FIXME", "Finish or remove each one.", todo))
    old = _scan(files, UI_EXT | CODE_EXT, lambda t: [y for y in COPYRIGHT.findall(t) if int(y) < year])
    if old:
        out.append(finding("copyright-year", "info", "The copyright year is out of date",
                           "Show the current year (computed at build time) or a range.", old))

    has = lambda *pats: any(re.search(p, x) for p in pats for x in lower)  # noqa: E731
    sitey = any(x.endswith((".html", ".tsx", ".jsx", ".vue", ".svelte", ".astro")) for x in lower)
    if sitey:
        if not has(r"(^|/)favicon\.(ico|svg|png)$", r"(^|/)icon\.(svg|png|ico|tsx?)$", r"apple-touch-icon"):
            out.append(finding("favicon-missing", "warn", "No favicon of its own",
                               "Add favicon.ico, icon.svg and apple-touch-icon.png made from the logo."))
        if not has(r"(^|/)404\.(html|tsx|jsx|astro|vue|svelte|md)$", r"not-found\.(tsx|jsx|js)$", r"\+error\.svelte$", r"error\.vue$"):
            out.append(finding("404-missing", "warn", "No custom 404 page",
                               "Add a 404 page that keeps the navigation and points to the main pages."))
        if not has(r"(og|opengraph|social|share)[-_]?(image)?[^/]*\.(png|jpe?g|webp|tsx)$", r"opengraph-image"):
            out.append(finding("og-image-missing", "info", "No social sharing image",
                               "Add a 1200×630 image with the brand for og:image."))
    if "readme.md" not in names:
        out.append(finding("readme-missing", "info", "No README", "Explain what the project is, how to run it and how it is deployed."))

    weight = {"error": 15, "warn": 7, "info": 2}
    return {"score": max(0, 100 - sum(weight[f["level"]] for f in out)), "files_read": len(files),
            "counts": {lv: sum(1 for f in out if f["level"] == lv) for lv in ("error", "warn", "info")},
            "findings": out,
            "note": "Mechanical signs only: also review the design and the wording with the not-vibecoded skill."}
