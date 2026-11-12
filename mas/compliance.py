"""Automatic compliance audit of a published site (GDPR / ePrivacy / French LCEN), run after every change.

Mas reads what any visitor can see: the pages, their cookies, the third-party services they load,
the legal pages, the forms. It reports what it finds with a fix for each. It cannot see how data is
handled behind the site (register of processing, contracts with processors, retention, rights
requests): those stay on a short checklist for a human. A clean audit means "nothing visible is
wrong", never "legal"; the interface says so.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from mas.db import Database

log = logging.getLogger(__name__)

MAX_BYTES = 1_500_000
MAX_PAGES = 6
TIMEOUT = 12
REAUDIT_DAYS = 7  # third-party services change without a release: look again every week
SETTLE_SECONDS = 45  # let nginx and the certificate settle after a release
KEEP = 20
PENALTY = {"critical": 20, "warning": 6, "info": 0}

# domain fragment -> (name, category, note). "analytics" and "marketing" need consent in the EU
# (some audience tools are exempt under conditions: "exempt" is reported as information only).
TRACKERS: list[tuple[str, str, str, str]] = [
    ("google-analytics.com", "Google Analytics", "analytics", "US"),
    ("googletagmanager.com", "Google Tag Manager", "analytics", "US"),
    ("analytics.google.com", "Google Analytics", "analytics", "US"),
    ("hotjar.com", "Hotjar", "analytics", "EU/US"),
    ("clarity.ms", "Microsoft Clarity", "analytics", "US"),
    ("segment.com", "Segment", "analytics", "US"),
    ("mixpanel.com", "Mixpanel", "analytics", "US"),
    ("amplitude.com", "Amplitude", "analytics", "US"),
    ("heapanalytics.com", "Heap", "analytics", "US"),
    ("facebook.net", "Meta Pixel", "marketing", "US"),
    ("facebook.com/tr", "Meta Pixel", "marketing", "US"),
    ("doubleclick.net", "Google Ads (DoubleClick)", "marketing", "US"),
    ("googleadservices.com", "Google Ads", "marketing", "US"),
    ("googlesyndication.com", "Google AdSense", "marketing", "US"),
    ("analytics.tiktok.com", "TikTok Pixel", "marketing", "US/CN"),
    ("snap.licdn.com", "LinkedIn Insight", "marketing", "US"),
    ("ads-twitter.com", "X (Twitter) Ads", "marketing", "US"),
    ("static.ads-twitter.com", "X (Twitter) Ads", "marketing", "US"),
    ("s.pinimg.com", "Pinterest Tag", "marketing", "US"),
    ("criteo.com", "Criteo", "marketing", "EU"),
    ("js.hs-scripts.com", "HubSpot", "marketing", "US"),
    ("hs-analytics.net", "HubSpot", "marketing", "US"),
    ("platform.twitter.com", "X (Twitter) embed", "marketing", "US"),
    ("player.vimeo.com", "Vimeo player", "embed", "US"),
    ("youtube.com/embed", "YouTube player", "embed", "US"),
    ("youtube.com/iframe_api", "YouTube player", "embed", "US"),
    ("google.com/maps/embed", "Google Maps", "embed", "US"),
    ("maps.googleapis.com", "Google Maps", "embed", "US"),
    ("matomo.cloud", "Matomo", "exempt", "EU"),
    ("plausible.io", "Plausible", "exempt", "EU"),
    ("usefathom.com", "Fathom", "exempt", "US"),
    ("fonts.googleapis.com", "Google Fonts", "cdn", "US"),
    ("fonts.gstatic.com", "Google Fonts", "cdn", "US"),
]
INLINE_TRACKERS = [
    (re.compile(r"\bgtag\s*\(|google-analytics|GoogleAnalyticsObject|UA-\d{4,}-\d|G-[A-Z0-9]{6,}"), "Google Analytics", "analytics"),
    (re.compile(r"\bfbq\s*\("), "Meta Pixel", "marketing"),
    (re.compile(r"\b_paq\.push|\bmatomo", re.I), "Matomo", "exempt"),
    (re.compile(r"\bhj\s*\(|hotjar", re.I), "Hotjar", "analytics"),
    (re.compile(r"\bttq\.(load|page)|\blintrk|\bpintrk\s*\("), "Marketing pixel", "marketing"),
    (re.compile(r"\bdataLayer\b.*gtm\.start|googletagmanager"), "Google Tag Manager", "analytics"),
]
CMP_HINTS = ("cookiebot", "onetrust", "didomi", "axeptio", "tarteaucitron", "iubenda", "cookieyes", "osano",
             "usercentrics", "cookie-script", "klaro", "cookieconsent", "quantcast", "trustarc", "consentmanager",
             "complianz", "cookie-law-info", "borlabs")
TRACKING_COOKIES = re.compile(r"^(_ga|_gid|_gat|_gcl|_fbp|_fbc|_hj|_clck|_clsk|ajs_|mp_|_pin_|_ttp|_tt_|_uet|__hs|hubspotutk|"
                              r"IDE$|test_cookie|fr$|_ym_|_cc_|amplitude_)", re.I)
ESSENTIAL_COOKIES = re.compile(r"(sess|csrf|xsrf|^__host-|^__secure-|auth|token|login|cart|panier|basket|mas_consent|"
                               r"cookie_?consent|cookieconsent|euconsent|tarteaucitron|axeptio|didomi|lang|locale|"
                               r"^wordpress_test|^wp-settings|^lb|^aws|^cf_|^__cf|^_cfuvid|^ph_phc|^next-auth)", re.I)

PRIVACY_RE = re.compile(r"confidentialit|privacy|datenschutz|privacidad|rgpd|gdpr|donn[ée]es personnelles|protection des donn", re.I)
LEGAL_RE = re.compile(r"mentions?[ -]l[ée]gales?|legal notice|legal[ -]information|impressum|aviso legal|imprint", re.I)
COOKIE_POLICY_RE = re.compile(r"cookie", re.I)
TERMS_RE = re.compile(r"\bcgv\b|\bcgu\b|conditions g[ée]n[ée]rales|terms (and|&) conditions|terms of (use|service|sale)|agb\b", re.I)
SHOP_RE = re.compile(r"add[- ]to[- ]cart|ajouter au panier|checkout|panier|woocommerce|shopify|stripe|paypal", re.I)
EMAIL_FIELD_RE = re.compile(r"mail|phone|t[ée]l|nom|name|adresse|address", re.I)


# -- fetching ---------------------------------------------------------------------------------------


@dataclass
class Response:
    url: str
    status: int
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    error: str = ""  # tls | network when status is 0

    def header(self, name: str) -> str:
        return next((v for k, v in self.headers if k.lower() == name.lower()), "")

    def cookies(self) -> list[str]:
        return [v for k, v in self.headers if k.lower() == "set-cookie"]

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def http_fetch(url: str, follow: bool = True) -> Response:
    """GET a URL (TLS verified), following up to 4 redirects, body capped."""
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(5):
        req = urllib.request.Request(url, headers={"User-Agent": "MasComplianceAudit/1.0 (+https://github.com/SyanCode/Mas)",
                                                   "Accept": "text/html,application/xhtml+xml,*/*;q=0.5"})
        try:
            resp = opener.open(req, timeout=TIMEOUT)
            body = resp.read(MAX_BYTES)
            return Response(url, resp.status, list(resp.headers.items()), body)
        except urllib.error.HTTPError as exc:
            headers = list(exc.headers.items()) if exc.headers else []
            location = dict((k.lower(), v) for k, v in headers).get("location")
            if follow and exc.code in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                continue
            return Response(url, exc.code, headers, exc.read(MAX_BYTES) if exc.fp else b"")
        except urllib.error.URLError as exc:
            kind = "tls" if isinstance(exc.reason, ssl.SSLError) else "network"
            return Response(url, 0, error=kind)
        except (OSError, ValueError):
            return Response(url, 0, error="network")
    return Response(url, 0, error="network")


# -- reading a page ---------------------------------------------------------------------------------


class Page(HTMLParser):
    def __init__(self, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.lang = ""
        self.scripts: list[dict] = []
        self.iframes: list[dict] = []
        self.resources: list[str] = []
        self.links: list[dict] = []
        self.forms: list[dict] = []
        self.text: list[str] = []
        self._skip = 0
        self._script: dict | None = None
        self._link: dict | None = None
        self._form: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "script":
            self._script = {"src": a.get("src", ""), "type": a.get("type", "").lower(), "consent": "data-mas-consent" in a
                            or "data-cookiecategory" in a or "data-cmp" in a or "data-categories" in a, "text": ""}
            self.scripts.append(self._script)
            self._skip += 1
        elif tag == "style":
            self._skip += 1
        elif tag == "iframe":
            self.iframes.append({"src": a.get("src", ""), "data_src": a.get("data-src", ""), "consent": "data-mas-consent" in a})
        elif tag == "a" and a.get("href"):
            self._link = {"href": a["href"], "text": ""}
            self.links.append(self._link)
            if self._form is not None:
                self._form["links"].append(self._link)
        elif tag in ("img", "link", "source", "video", "audio"):
            ref = a.get("src") or a.get("href")
            if ref:
                self.resources.append(ref)
        elif tag == "form":
            self._form = {"action": a.get("action", ""), "inputs": [], "checkbox": False, "links": [], "text": []}
            self.forms.append(self._form)
        elif tag in ("input", "textarea", "select") and self._form is not None:
            kind = a.get("type", "text" if tag == "input" else tag).lower()
            self._form["inputs"].append({"type": kind, "name": a.get("name", "") + " " + a.get("placeholder", "")})
            if kind == "checkbox":
                self._form["checkbox"] = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            self._script = None
        elif tag == "a":
            self._link = None
        elif tag == "form":
            self._form = None

    def handle_data(self, data):
        if self._script is not None:
            self._script["text"] += data
        elif not self._skip:
            self.text.append(data)
            if self._link is not None:
                self._link["text"] += data
            if self._form is not None:
                self._form["text"].append(data)


def parse(url: str, html: str) -> Page:
    page = Page(url)
    try:
        page.feed(html)
        page.close()
    except Exception:  # noqa: BLE001 - broken HTML is audited as far as it reads
        pass
    return page


def _find_tracker(ref: str) -> tuple[str, str, str, str] | None:
    low = ref.lower()
    host_path = re.sub(r"^https?:?//", "", low)
    for frag, name, cat, region in TRACKERS:
        if frag in host_path:
            if frag == "youtube.com/embed" and "youtube-nocookie.com" in host_path:
                return None
            return frag, name, cat, region
    return None


# -- the audit --------------------------------------------------------------------------------------


def finding(fid: str, level: str, category: str, title: str, detail: str = "", fix: str = "", where: str = "") -> dict:
    return {"id": fid, "level": level, "category": category, "title": title, "detail": detail, "fix": fix, "where": where}


class Auditor:
    def __init__(self, fetch: Callable[..., Response] = http_fetch, mas_banner: Callable[[str], dict | None] = lambda host: None) -> None:
        self.fetch = fetch
        self.mas_banner = mas_banner  # host -> the banner settings Mas holds for that site, if any

    def audit(self, domain: str) -> dict:
        host = domain.lower().strip().rstrip(".")
        out: list[dict] = []
        home = self.fetch(f"https://{host}/")
        if home.status == 0:
            level_title = ("The HTTPS certificate is not valid", "Fix the certificate (Mas renews Let's Encrypt automatically).") \
                if home.error == "tls" else ("The site could not be reached", "Check the domain and that the app is running.")
            out.append(finding("site-unreachable" if home.error != "tls" else "https-invalid", "critical", "security",
                               level_title[0], fix=level_title[1], where=f"https://{host}/"))
            return self._result(host, out, [])
        pages = self._pages(host, home)
        out += self._https(host, home)
        out += self._cookies(pages, home)
        out += self._trackers(host, pages, home)
        out += self._legal(host, pages)
        out += self._forms(pages)
        return self._result(host, out, [p.url for p in pages])

    # crawl: the home page, the legal pages it links, a few pages of the sitemap or menu
    def _pages(self, host: str, home: Response) -> list[Page]:
        first = parse(home.url, home.text)
        pages = [first]
        seen = {home.url.rstrip("/")}
        want: list[str] = []
        for link in first.links:
            if PRIVACY_RE.search(link["text"] + link["href"]) or LEGAL_RE.search(link["text"] + link["href"]) \
                    or COOKIE_POLICY_RE.search(link["text"]) or TERMS_RE.search(link["text"] + link["href"]):
                want.append(urljoin(home.url, link["href"]))
        sitemap = self.fetch(f"https://{host}/sitemap.xml")
        if sitemap.status == 200:
            want += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap.text)[:4]
        else:
            want += [urljoin(home.url, l["href"]) for l in first.links[:12]]
        for url in want:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() != host or url.rstrip("/") in seen:
                continue
            if len(pages) >= MAX_PAGES + 3:
                break
            seen.add(url.rstrip("/"))
            resp = self.fetch(url.split("#")[0])
            if resp.status == 200 and "html" in resp.header("content-type").lower() + "html":
                pages.append(parse(resp.url, resp.text))
        return pages

    def _https(self, host: str, home: Response) -> list[dict]:
        out = []
        plain = self.fetch(f"http://{host}/", follow=False)
        if plain.status and not (plain.status in (301, 302, 307, 308) and plain.header("location").startswith("https://")):
            out.append(finding("https-redirect", "warning", "security", "http:// does not redirect to https://",
                               "Visitors who type the address without https stay on an unencrypted connection.",
                               "Mas sets up the redirect for apps it deploys; for another server, add a 301 to https.", f"http://{host}/"))
        if not home.header("strict-transport-security"):
            out.append(finding("hsts", "info", "security", "No HSTS header", "Browsers are not told to always use https.",
                               "Add Strict-Transport-Security: max-age=31536000 in the nginx server block.", f"https://{host}/"))
        missing = [h for h in ("content-security-policy", "x-content-type-options", "referrer-policy") if not home.header(h)]
        if missing:
            out.append(finding("security-headers", "info", "security", "Security headers missing: " + ", ".join(missing),
                               "Article 32 asks for appropriate security measures; these headers are the usual baseline.",
                               "Add them in the nginx server block (add_header …).", f"https://{host}/"))
        return out

    def _cookies(self, pages: list[Page], home: Response) -> list[dict]:
        out = []
        tracking, other, insecure = [], [], []
        for raw in home.cookies():
            name = raw.split("=", 1)[0].strip()
            if TRACKING_COOKIES.search(name):
                tracking.append(name)
            elif not ESSENTIAL_COOKIES.search(name):
                other.append(name)
            low = raw.lower()
            if "secure" not in low or "samesite" not in low:
                insecure.append(name)
        if tracking:
            out.append(finding("cookies-tracking", "critical", "cookies", "Tracking cookies are set before any consent",
                               "On the very first visit the server sends: " + ", ".join(tracking) + ".",
                               "Load the tool only after consent (see the cookie banner) or remove it.", f"https://{urlsplit(home.url).hostname}/"))
        if other:
            out.append(finding("cookies-unknown", "warning", "cookies", "Cookies set on the first visit",
                               "Not recognised as strictly necessary: " + ", ".join(other) + ". Cookies that are not needed for the service require consent.",
                               "Check what they are for; if not strictly necessary, set them only after consent."))
        if insecure:
            out.append(finding("cookies-flags", "info", "security", "Cookies without Secure and SameSite",
                               ", ".join(insecure), "Add Secure; SameSite=Lax (and HttpOnly for session cookies)."))
        return out

    def _trackers(self, host: str, pages: list[Page], home: Response) -> list[dict]:
        out: list[dict] = []
        loaded: dict[str, dict] = {}  # name -> {category, region, where}
        banner = False
        for page in pages:
            for s in page.scripts:
                src = s["src"]
                gated = s["type"] == "text/plain" or s["consent"] or "javascript/blocked" in s["type"]
                if re.search(r"/cookies\.js(\?|$)", src) or any(h in src.lower() for h in CMP_HINTS):
                    banner = True
                if src and (t := _find_tracker(src)) and not gated:
                    loaded.setdefault(t[1], {"category": t[2], "region": t[3], "where": page.url})
                if not src and not gated and s["text"]:
                    if any(h in s["text"].lower() for h in CMP_HINTS):
                        banner = True
                    for rx, name, cat in INLINE_TRACKERS:
                        if rx.search(s["text"]):
                            loaded.setdefault(name, {"category": cat, "region": "US", "where": page.url})
            for fr in page.iframes:
                t = _find_tracker(fr["src"]) if fr["src"] else None
                if t and not fr["consent"]:
                    loaded.setdefault(t[1], {"category": t[2], "region": t[3], "where": page.url})
            for ref in page.resources:
                if (t := _find_tracker(ref)) and t[2] in ("cdn", "marketing", "analytics"):
                    loaded.setdefault(t[1], {"category": t[2], "region": t[3], "where": page.url})
            if "cookieconsent" in " ".join(page.text).lower() or any(h in " ".join(page.text).lower() for h in ("tarteaucitron", "didomi")):
                banner = True
        needs_consent = {n: v for n, v in loaded.items() if v["category"] in ("analytics", "marketing", "embed")}
        for name, v in sorted(needs_consent.items()):
            out.append(finding(f"tracker-{re.sub(r'[^a-z0-9]+', '-', name.lower())}", "critical", "trackers",
                               f"{name} loads before consent",
                               f"It is loaded as soon as the page opens ({v['category']}, servers: {v['region']}). Consent must come first.",
                               'Mark it type="text/plain" data-mas-consent="' + ("marketing" if v["category"] != "analytics" else "analytics") +
                               '" so the cookie banner starts it after consent.', v["where"]))
        for name, v in sorted(loaded.items()):
            if v["category"] == "cdn":
                out.append(finding("font-google", "warning", "trackers", f"{name} is loaded from Google's servers",
                                   "Each visit sends the visitor's IP address to Google; European courts have sanctioned this without consent.",
                                   "Host the fonts on your own server (download and serve them).", v["where"]))
            elif v["category"] == "exempt":
                out.append(finding(f"tracker-{name.lower()}", "info", "trackers", f"{name} audience measurement detected",
                                   "Some audience tools are exempt from consent only if strictly configured (anonymous, first-party, limited retention).",
                                   "Check the CNIL conditions for exemption, or put it behind the banner.", v["where"]))
        mas = self.mas_banner(host)
        if mas is not None:
            banner = True
            if not mas.get("enabled"):
                out.append(finding("banner-off", "warning", "consent", "The Mas cookie banner is installed but switched off",
                                   "The script is on the site but the banner is disabled in the CMS.", "Enable it in the Cookie banner page."))
            elif not mas.get("policy_url"):
                out.append(finding("banner-policy", "warning", "consent", "The cookie banner has no link to a privacy policy",
                                   "Visitors must be able to read how their data is used.", "Fill in the privacy policy link in the Cookie banner page."))
        if (needs_consent or self._has_cookie_findings(home)) and not banner:
            out.append(finding("no-banner", "critical", "consent", "No consent banner found",
                               "The site uses services or cookies that need consent but no cookie banner (Mas or another) was detected.",
                               "Add the Mas banner: <script defer src=\"https://…/cookies.js\"></script> (Cookie banner page)."))
        elif not needs_consent and not self._has_cookie_findings(home):
            out.append(finding("no-tracking", "pass", "consent", "No third-party tracking detected",
                               "No analytics, advertising or embedded service was found loading before consent."))
        if banner and needs_consent:
            out.append(finding("banner-bypassed", "info", "consent", "A banner exists, yet services still load before consent",
                               "Check that every service above is blocked until the visitor agrees."))
        return out

    @staticmethod
    def _has_cookie_findings(home: Response) -> bool:
        for raw in home.cookies():
            name = raw.split("=", 1)[0].strip()
            if TRACKING_COOKIES.search(name) or not ESSENTIAL_COOKIES.search(name):
                return True
        return False

    def _legal(self, host: str, pages: list[Page]) -> list[dict]:
        out: list[dict] = []
        home = pages[0]
        found: dict[str, str] = {}
        for page in pages:
            for link in page.links:
                label = link["text"] + " " + link["href"]
                for key, rx in (("privacy", PRIVACY_RE), ("legal", LEGAL_RE), ("terms", TERMS_RE)):
                    if rx.search(label) and key not in found:
                        found[key] = urljoin(page.url, link["href"].split("#")[0])
        shop = bool(SHOP_RE.search(" ".join(home.text) + " ".join(s["src"] for s in home.scripts)))
        texts: dict[str, str] = {}
        for key in ("privacy", "legal", "terms"):
            if key in found:
                resp = self.fetch(found[key])
                if resp.status == 200:
                    texts[key] = re.sub(r"\s+", " ", " ".join(parse(resp.url, resp.text).text)).lower()
                elif key != "terms":
                    out.append(finding(f"{key}-broken", "critical", "legal", "A legal page link is broken",
                                       f"{found[key]} answers {resp.status or 'no answer'}.", "Publish the page or fix the link.", found[key]))
        if "privacy" not in found:
            out.append(finding("privacy-missing", "critical", "legal", "No privacy policy link found",
                               "Articles 12-14 GDPR: visitors must be told who processes their data, why, for how long and what rights they have.",
                               "Publish a privacy policy and link it in the footer of every page."))
        if "legal" not in found:
            out.append(finding("legal-missing", "critical", "legal", "No legal notice (mentions légales / Impressum) found",
                               "A professional site must name its publisher and host (LCEN art. 6 in France, Impressum in Germany).",
                               "Publish a legal notice and link it in the footer."))
        if shop and "terms" not in found:
            out.append(finding("terms-missing", "warning", "legal", "Online shop without terms of sale",
                               "A shop must show its general terms of sale (CGV) before the order.", "Publish the CGV and link them."))
        legal = texts.get("legal", "")
        if legal:
            checks = [("publisher / company name", r"soci[ée]t[ée]|raison sociale|[ée]diteur|publisher|company|entreprise|gmbh|sarl|sas\b|eurl|siret|siren"),
                      ("registration number (SIRET/RCS/VAT)", r"siret|siren|rcs|tva|vat|handelsregister|\bcif\b"),
                      ("publication director", r"directeur de la publication|responsable de la publication|publication director|verantwortlich"),
                      ("host", r"h[ée]bergeur|h[ée]berg[ée] par|hosting|hosted by|host\b|hosting provider"),
                      ("contact", r"@|contact|t[ée]l|phone")]
            missing = [name for name, rx in checks if not re.search(rx, legal)]
            if missing:
                out.append(finding("legal-incomplete", "warning", "legal", "The legal notice looks incomplete",
                                   "Not found in the text: " + ", ".join(missing) + ".", "Complete the notice with the missing identification.", found["legal"]))
            else:
                out.append(finding("legal-ok", "pass", "legal", "The legal notice names the publisher, host and contact", where=found["legal"]))
        policy = texts.get("privacy", "")
        if policy:
            checks = [("data controller", r"responsable (du|de) traitement|data controller|controller|verantwortliche"),
                      ("purposes", r"finalit[ée]s?|purposes?|zweck|objectifs?"),
                      ("retention period", r"dur[ée]e de conservation|conserv[ée]es?|retention|retain|speicherdauer|aufbewahr"),
                      ("rights (access, erasure…)", r"droit d.acc[èe]s|droits?|right of access|your rights|betroffenenrechte|rectification|effacement|erasure"),
                      ("supervisory authority (CNIL…)", r"cnil|autorit[ée] de contr[oô]le|supervisory authority|aufsichtsbeh[öo]rde"),
                      ("recipients / processors", r"destinataires?|sous-traitants?|recipients?|processors?|empf[äa]nger")]
            missing = [name for name, rx in checks if not re.search(rx, policy)]
            if len(missing) >= 3:
                out.append(finding("privacy-incomplete", "warning", "legal", "The privacy policy misses key information",
                                   "Not found in the text: " + ", ".join(missing) + ".",
                                   "Complete it (GDPR art. 13): controller, purposes, retention, rights, authority, recipients.", found["privacy"]))
            elif missing:
                out.append(finding("privacy-partial", "info", "legal", "The privacy policy could say more",
                                   "Not found: " + ", ".join(missing) + ".", "Add these points.", found["privacy"]))
            else:
                out.append(finding("privacy-ok", "pass", "legal", "The privacy policy covers the usual points", where=found["privacy"]))
        if not home.lang:
            out.append(finding("html-lang", "info", "legal", "The page has no lang attribute", "Accessibility and screen readers rely on it.",
                               '<html lang="fr">'))
        return out

    def _forms(self, pages: list[Page]) -> list[dict]:
        out = []
        for page in pages:
            for f in page.forms:
                personal = [i for i in f["inputs"] if i["type"] in ("email", "tel") or EMAIL_FIELD_RE.search(i["name"])]
                if not personal or all(i["type"] in ("hidden", "search") for i in personal):
                    continue
                if any(i["type"] == "password" for i in f["inputs"]) and not any(i["type"] in ("email", "tel") for i in f["inputs"]):
                    continue
                if f["action"].startswith("http://"):
                    out.append(finding("form-insecure", "critical", "forms", "A form sends personal data over http://", where=page.url,
                                       fix="Use https:// for the action."))
                text = " ".join(f["text"]).lower()
                informed = any(PRIVACY_RE.search(l["text"] + l["href"]) for l in f["links"]) or \
                    re.search(r"donn[ée]es|privacy|datenschutz|rgpd|gdpr|consent|accept", text) or f["checkbox"]
                if not informed:
                    out.append(finding("form-info", "warning", "forms", "A form collects personal data without information",
                                       "Visitors should be told, at the point of collection, who uses the data and link to the privacy policy.",
                                       "Add a short notice and a link to the privacy policy under the form (and a consent box when you send marketing).",
                                       page.url))
                    break
        return out

    def _result(self, host: str, findings: list[dict], pages: list[str]) -> dict:
        seen, unique = set(), []
        for f in findings:
            key = (f["id"], f["where"])
            if key not in seen:
                seen.add(key)
                unique.append(f)
        order = {"critical": 0, "warning": 1, "info": 2, "pass": 3}
        unique.sort(key=lambda f: (order[f["level"]], f["category"]))
        counts = {lvl: sum(1 for f in unique if f["level"] == lvl) for lvl in order}
        score = max(0, 100 - sum(PENALTY.get(f["level"], 0) for f in unique))
        verdict = "blocking" if counts["critical"] else "attention" if counts["warning"] else "ok"
        return {"host": host, "score": score, "verdict": verdict, "counts": counts, "findings": unique, "pages": pages}


# -- storage and scheduling ---------------------------------------------------------------------------


class Compliance:
    def __init__(self, db: Database, auditor: Auditor | None = None) -> None:
        self.db = db
        self.auditor = auditor or Auditor(mas_banner=self._banner)
        self._running: set[str] = set()

    def _banner(self, host: str) -> dict | None:
        from mas import cookies
        from mas.goals import app_for_origin
        app = app_for_origin(self.db, f"https://{host}")
        if app is None:
            return None
        return cookies.load(self.db, app["id"]) if self.db.get_setting(f"cookies:{app['id']}") else None

    def run(self, app: dict, release_id: int | None = None, trigger: str = "manual") -> dict:
        """Audit the site now and keep the result; compares with the previous audit for what is new."""
        if not app.get("domain"):
            raise ValueError("this app has no domain")
        if app["id"] in self._running:
            raise RuntimeError("an audit is already running for this site")
        self._running.add(app["id"])
        try:
            result = self.auditor.audit(app["domain"])
        finally:
            self._running.discard(app["id"])
        previous = self.latest(app["id"])
        old = {(f["id"], f["where"]) for f in (previous or {}).get("findings", []) if f["level"] in ("critical", "warning")}
        for f in result["findings"]:
            f["new"] = bool(previous) and f["level"] in ("critical", "warning") and (f["id"], f["where"]) not in old
        result.update(release_id=release_id, trigger=trigger, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.db.save_compliance(app["id"], release_id, result["score"], result["verdict"], trigger, json.dumps(result))
        crit, warn = result["counts"]["critical"], result["counts"]["warning"]
        self.db.audit("compliance", "cms-compliance", result["verdict"] != "blocking", service_id=app["id"],
                      detail=f"{result['score']}/100: {crit} critical, {warn} warning(s) ({trigger})")
        return result

    def latest(self, app_id: str) -> dict | None:
        row = self.db.latest_compliance(app_id)
        return json.loads(row["result"]) | {"id": row["id"]} if row else None

    def history(self, app_id: str) -> list[dict]:
        return [{"id": r["id"], "at": r["at"], "score": r["score"], "verdict": r["verdict"], "release_id": r["release_id"],
                 "trigger": r["trigger"]} for r in self.db.compliance_history(app_id, KEEP)]

    def tick(self, now: float | None = None) -> list[str]:
        """Audit the sites whose latest release is not audited yet, and the ones not seen for a week."""
        now = now or time.time()
        done = []
        for app in self.db.list_apps():
            if not app.get("domain") or app["id"] in self._running:
                continue
            last = self.db.latest_compliance(app["id"])
            release = next((r for r in self.db.list_releases(app["id"], 5) if r["status"] == "live"), None)
            reason = None
            if release and (not last or last["release_id"] != release["id"]):
                finished = release.get("finished_at") or release["created_at"]
                try:
                    age = now - datetime.fromisoformat(finished).timestamp()
                except ValueError:
                    age = SETTLE_SECONDS
                if age >= SETTLE_SECONDS:
                    reason = "release"
            elif last and now - datetime.fromisoformat(last["at"]).timestamp() > REAUDIT_DAYS * 86400:
                reason = "weekly"
            elif not last and not release and app.get("mode") == "adopted":  # a managed app is audited once deployed
                reason = "first"
            if reason:
                try:
                    self.run(app, release["id"] if release else None, reason)
                    done.append(app["id"])
                except Exception:  # noqa: BLE001 - one site must not stop the others
                    log.exception("compliance audit of %s failed", app["id"])
                break  # one audit per tick: each one makes many requests
        return done
