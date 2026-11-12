/** Texts of the compliance audit (written by mas/compliance.py in English) so the interface can translate them. */
import { msg, tr } from "@/lib/i18n";

export const FINDING_TEXTS: string[] = [
  msg("http:// does not redirect to https://"),
  msg("Visitors who type the address without https stay on an unencrypted connection."),
  msg("Mas sets up the redirect for apps it deploys; for another server, add a 301 to https."),
  msg("No HSTS header"),
  msg("Browsers are not told to always use https."),
  msg("Add Strict-Transport-Security: max-age=31536000 in the nginx server block."),
  msg("Article 32 asks for appropriate security measures; these headers are the usual baseline."),
  msg("Add them in the nginx server block (add_header …)."),
  msg("Tracking cookies are set before any consent"),
  msg("Load the tool only after consent (see the cookie banner) or remove it."),
  msg("Cookies set on the first visit"),
  msg("Check what they are for; if not strictly necessary, set them only after consent."),
  msg("Cookies without Secure and SameSite"),
  msg("Add Secure; SameSite=Lax (and HttpOnly for session cookies)."),
  msg("No consent banner found"),
  msg("The site uses services or cookies that need consent but no cookie banner (Mas or another) was detected."),
  msg("Add the Mas banner: <script defer src=\"https://…/cookies.js\"></script> (Cookie banner page)."),
  msg("A banner exists, yet services still load before consent"),
  msg("Check that every service above is blocked until the visitor agrees."),
  msg("No privacy policy link found"),
  msg("Articles 12-14 GDPR: visitors must be told who processes their data, why, for how long and what rights they have."),
  msg("Publish a privacy policy and link it in the footer of every page."),
  msg("No legal notice (mentions légales / Impressum) found"),
  msg("A professional site must name its publisher and host (LCEN art. 6 in France, Impressum in Germany)."),
  msg("Publish a legal notice and link it in the footer."),
  msg("Online shop without terms of sale"),
  msg("A shop must show its general terms of sale (CGV) before the order."),
  msg("Publish the CGV and link them."),
  msg("The page has no lang attribute"),
  msg("Accessibility and screen readers rely on it."),
  msg("Each visit sends the visitor's IP address to Google; European courts have sanctioned this without consent."),
  msg("Host the fonts on your own server (download and serve them)."),
  msg("The Mas cookie banner is installed but switched off"),
  msg("The script is on the site but the banner is disabled in the CMS."),
  msg("Enable it in the Cookie banner page."),
  msg("No third-party tracking detected"),
  msg("No analytics, advertising or embedded service was found loading before consent."),
  msg("The legal notice looks incomplete"),
  msg("Complete the notice with the missing identification."),
  msg("The legal notice names the publisher, host and contact"),
  msg("The privacy policy misses key information"),
  msg("Complete it (GDPR art. 13): controller, purposes, retention, rights, authority, recipients."),
  msg("Some audience tools are exempt from consent only if strictly configured (anonymous, first-party, limited retention)."),
  msg("Check the CNIL conditions for exemption, or put it behind the banner."),
  msg("The cookie banner has no link to a privacy policy"),
  msg("Visitors must be able to read how their data is used."),
  msg("Fill in the privacy policy link in the Cookie banner page."),
  msg("The privacy policy could say more"),
  msg("Add these points."),
  msg("The privacy policy covers the usual points"),
  msg("A form sends personal data over http://"),
  msg("Use https:// for the action."),
  msg("A form collects personal data without information"),
  msg("Visitors should be told, at the point of collection, who uses the data and link to the privacy policy."),
  msg("Add a short notice and a link to the privacy policy under the form (and a consent box when you send marketing)."),
  msg("A legal page link is broken"),
  msg("Publish the page or fix the link."),
  msg("The HTTPS certificate is not valid"),
  msg("Fix the certificate (Mas renews Let's Encrypt automatically)."),
  msg("The site could not be reached"),
  msg("Check the domain and that the app is running."),
];

/** Texts with a variable part: [pattern of the English text, translation key, names of the captured values]. */
const PATTERNS: [RegExp, string, string[]][] = [
  [new RegExp("^(.+) loads before consent$"), msg("{name} loads before consent"), ["name"]],
  [new RegExp("^(.+) is loaded from Google's servers$"), msg("{name} is loaded from Google's servers"), ["name"]],
  [new RegExp("^(.+) audience measurement detected$"), msg("{name} audience measurement detected"), ["name"]],
  [new RegExp("^Security headers missing: (.+)$"), msg("Security headers missing: {list}"), ["list"]],
  [new RegExp("^On the very first visit the server sends: (.+)\\.$"), msg("On the very first visit the server sends: {list}."), ["list"]],
  [new RegExp("^Not recognised as strictly necessary: (.+)\\. Cookies that are not needed for the service require consent\\.$"), msg("Not recognised as strictly necessary: {list}. Cookies that are not needed for the service require consent."), ["list"]],
  [new RegExp("^Not found in the text: (.+)\\.$"), msg("Not found in the text: {list}."), ["list"]],
  [new RegExp("^Not found: (.+)\\.$"), msg("Not found: {list}."), ["list"]],
  [new RegExp("^It is loaded as soon as the page opens \\((\\w+), servers: ([^)]+)\\)\\. Consent must come first\\.$"), msg("It is loaded as soon as the page opens ({category}, servers: {region}). Consent must come first."), ["category", "region"]],
  [new RegExp("^Mark it type=\"text/plain\" data-mas-consent=\"(\\w+)\" so the cookie banner starts it after consent\\.$"), msg("Mark it type=\"text/plain\" data-mas-consent=\"{category}\" so the cookie banner starts it after consent."), ["category"]],
  [new RegExp("^(\\S+) answers (.+)\\.$"), msg("{url} answers {status}."), ["url", "status"]],
]; 

/** The translation of one finding text; unknown texts stay as the audit wrote them. */
export const FINDING_ITEMS: string[] = [
  msg("publisher / company name"), msg("registration number (SIRET/RCS/VAT)"), msg("publication director"), msg("host"),
  msg("contact"), msg("data controller"), msg("purposes"), msg("retention period"), msg("rights (access, erasure…)"),
  msg("supervisory authority (CNIL…)"), msg("recipients / processors"),
];

/** "a, b, c" → each element translated when it is a known name (header names stay as they are). */
const translateList = (list: string) => list.split(", ").map((x) => tr(x)).join(", ");

export function translateFinding(text: string): string {
  if (!text) return text;
  for (const [rx, key, names] of PATTERNS) {
    const m = rx.exec(text);
    if (m) return tr(key, Object.fromEntries(names.map((n, i) => [n, n === "list" ? translateList(m[i + 1]) : m[i + 1]])));
  }
  return tr(text);
}
