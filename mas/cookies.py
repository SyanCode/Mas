"""Cookie consent banner a site embeds with one script line.

The banner is configured per site in the CMS and served by Mas (``/cookies.js``). The visitor's
choice lives in a first-party cookie on the site itself; Mas only counts the decisions (accept,
reject, custom) per day, with no identifier. Scripts and iframes that set cookies are written
``type="text/plain" data-mas-consent="analytics"`` and are started by the banner once allowed.
"""

from __future__ import annotations

import json
import re
from datetime import date

from mas.db import Database
from mas.goals import app_for_origin

CATEGORIES = ("analytics", "marketing", "preferences")
POSITIONS = ("bottom", "bottom-left", "center")
THEMES = ("auto", "light", "dark")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
URL_RE = re.compile(r"^(https?://[^\s<>\"']+|/[^\s<>\"']*)$")
DECISIONS = ("accept", "reject", "custom")
MAX_EXPIRY_MONTHS = 13  # the CNIL asks to ask again within 13 months at most, 6 is the usual choice

DEFAULTS = {
    "enabled": False, "position": "bottom-left", "theme": "auto", "accent": "#2563eb", "policy_url": "",
    "title": "", "message": "", "categories": ["analytics"], "expiry_months": 6, "reopen_button": True,
}


def normalize(raw: dict) -> dict:
    """A clean configuration from what the editor sent; raises ValueError with a readable reason."""
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        raise ValueError("invalid configuration")
    out["enabled"] = bool(raw.get("enabled", out["enabled"]))
    out["reopen_button"] = bool(raw.get("reopen_button", out["reopen_button"]))
    for key, allowed in (("position", POSITIONS), ("theme", THEMES)):
        value = raw.get(key, out[key])
        if value not in allowed:
            raise ValueError(f"{key}: one of {', '.join(allowed)}")
        out[key] = value
    accent = str(raw.get("accent", out["accent"]))
    if not COLOR_RE.fullmatch(accent):
        raise ValueError("accent: a colour like #2563eb")
    out["accent"] = accent
    policy = str(raw.get("policy_url", "")).strip()
    if policy and not URL_RE.fullmatch(policy):
        raise ValueError("policy link: https://… or /page")
    out["policy_url"] = policy
    for key, limit in (("title", 80), ("message", 500)):
        text = " ".join(str(raw.get(key, "")).split())
        if len(text) > limit:
            raise ValueError(f"{key}: at most {limit} characters")
        out[key] = text
    cats = raw.get("categories", out["categories"])
    if not isinstance(cats, list) or any(c not in CATEGORIES for c in cats):
        raise ValueError(f"categories: {', '.join(CATEGORIES)}")
    out["categories"] = [c for c in CATEGORIES if c in cats]
    months = raw.get("expiry_months", out["expiry_months"])
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= MAX_EXPIRY_MONTHS:
        raise ValueError(f"validity: 1 to {MAX_EXPIRY_MONTHS} months")
    out["expiry_months"] = months
    return out


def load(db: Database, app_id: str) -> dict:
    try:
        stored = json.loads(db.get_setting(f"cookies:{app_id}") or "null")
        return normalize(stored) if stored else dict(DEFAULTS)
    except ValueError:
        return dict(DEFAULTS)


def save(db: Database, app_id: str, raw: dict) -> dict:
    config = normalize(raw)
    db.set_setting(f"cookies:{app_id}", json.dumps(config))
    return config


def public_config(db: Database, host: str) -> dict:
    """What the script needs for the site served at ``host`` (nothing when it is off or unknown)."""
    app = app_for_origin(db, f"https://{host}")
    if app is None:
        return {"enabled": False}
    config = load(db, app["id"])
    return config if config["enabled"] else {"enabled": False}


def record(db: Database, host: str, decision: str, today: date | None = None) -> bool:
    app = app_for_origin(db, f"https://{host}")
    if app is None or decision not in DECISIONS:
        return False
    db.add_consent(app["id"], (today or date.today()).isoformat(), decision)
    return True


def report(db: Database, app_id: str, days: int = 30, today: date | None = None) -> dict:
    from datetime import timedelta
    since = ((today or date.today()) - timedelta(days=days - 1)).isoformat()
    totals = {d: 0 for d in DECISIONS}
    for row in db.consent_stats(app_id, since):
        totals[row["decision"]] += row["n"]
    shown = sum(totals.values())
    return {**totals, "total": shown, "accept_rate": round(100 * totals["accept"] / shown, 1) if shown else 0.0}


SCRIPT = r"""(function () {
  "use strict";
  var s = document.currentScript;
  if (!s || window.masConsent) return;
  var base = new URL(s.src).origin;
  var COOKIE = "mas_consent", CATS = ["analytics", "marketing", "preferences"];
  var T = {
    en: { title: "We respect your privacy", msg: "We use cookies to measure audience and improve the site. You choose.", accept: "Accept all", reject: "Reject all", custom: "Customize", save: "Save my choices", policy: "Learn more", settings: "Cookies", necessary: "Necessary", necessaryD: "Required for the site to work. Always on.", analytics: "Audience measurement", analyticsD: "Helps us understand how the site is used.", marketing: "Marketing", marketingD: "Lets us show relevant content and measure campaigns.", preferences: "Preferences", preferencesD: "Remembers your settings, such as language." },
    fr: { title: "Nous respectons votre vie privée", msg: "Nous utilisons des cookies pour mesurer l'audience et améliorer le site. C'est vous qui choisissez.", accept: "Tout accepter", reject: "Tout refuser", custom: "Personnaliser", save: "Enregistrer mes choix", policy: "En savoir plus", settings: "Cookies", necessary: "Nécessaires", necessaryD: "Indispensables au fonctionnement du site. Toujours actifs.", analytics: "Mesure d'audience", analyticsD: "Nous aide à comprendre comment le site est utilisé.", marketing: "Marketing", marketingD: "Permet d'afficher des contenus pertinents et de mesurer les campagnes.", preferences: "Préférences", preferencesD: "Mémorise vos réglages, comme la langue." },
    es: { title: "Respetamos su privacidad", msg: "Usamos cookies para medir la audiencia y mejorar el sitio. Usted decide.", accept: "Aceptar todo", reject: "Rechazar todo", custom: "Personalizar", save: "Guardar mis opciones", policy: "Más información", settings: "Cookies", necessary: "Necesarias", necessaryD: "Imprescindibles para el funcionamiento del sitio. Siempre activas.", analytics: "Medición de audiencia", analyticsD: "Nos ayuda a entender cómo se usa el sitio.", marketing: "Marketing", marketingD: "Permite mostrar contenido relevante y medir campañas.", preferences: "Preferencias", preferencesD: "Recuerda sus ajustes, como el idioma." },
    de: { title: "Wir respektieren Ihre Privatsphäre", msg: "Wir verwenden Cookies, um die Reichweite zu messen und die Website zu verbessern. Sie entscheiden.", accept: "Alle akzeptieren", reject: "Alle ablehnen", custom: "Anpassen", save: "Auswahl speichern", policy: "Mehr erfahren", settings: "Cookies", necessary: "Notwendig", necessaryD: "Für den Betrieb der Website erforderlich. Immer aktiv.", analytics: "Reichweitenmessung", analyticsD: "Hilft uns zu verstehen, wie die Website genutzt wird.", marketing: "Marketing", marketingD: "Ermöglicht passende Inhalte und die Messung von Kampagnen.", preferences: "Einstellungen", preferencesD: "Merkt sich Ihre Einstellungen, etwa die Sprache." }
  };
  var cfg = null, state = null, listeners = [], root = null, host = null;

  function lang() {
    var l = (document.documentElement.lang || navigator.language || "en").slice(0, 2).toLowerCase();
    return T[l] ? l : "en";
  }
  function read() {
    var m = document.cookie.match(/(?:^|; )mas_consent=([^;]*)/);
    if (!m) return null;
    try { var v = JSON.parse(decodeURIComponent(m[1])); return v && v.c && v.e > Date.now() ? v : null; } catch (e) { return null; }
  }
  function write(choice) {
    var months = (cfg && cfg.expiry_months) || 6;
    var v = { c: choice, e: Date.now() + months * 30 * 864e5 };
    document.cookie = COOKIE + "=" + encodeURIComponent(JSON.stringify(v)) + "; max-age=" + months * 30 * 86400 +
      "; path=/; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
    state = v;
  }
  function has(cat) { return !!(state && state.c && state.c[cat]); }

  function activate() {
    var nodes = document.querySelectorAll("script[type='text/plain'][data-mas-consent], iframe[data-mas-consent][data-src]");
    Array.prototype.forEach.call(nodes, function (n) {
      if (n.getAttribute("data-mas-done") || !has(n.getAttribute("data-mas-consent"))) return;
      n.setAttribute("data-mas-done", "1");
      if (n.tagName === "IFRAME") { n.src = n.getAttribute("data-src"); return; }
      var c = document.createElement("script");
      Array.prototype.forEach.call(n.attributes, function (a) {
        if (a.name !== "type" && a.name !== "data-mas-consent" && a.name !== "data-mas-done") c.setAttribute(a.name, a.value);
      });
      c.text = n.text;
      n.parentNode.insertBefore(c, n.nextSibling);
    });
    if (typeof window.gtag === "function") {
      var g = function (v) { return v ? "granted" : "denied"; };
      window.gtag("consent", "update", { analytics_storage: g(has("analytics")), ad_storage: g(has("marketing")),
        ad_user_data: g(has("marketing")), ad_personalization: g(has("marketing")), functionality_storage: g(has("preferences")) });
    }
    listeners.forEach(function (fn) { try { fn(state && state.c); } catch (e) {} });
    window.dispatchEvent(new CustomEvent("mas:consent", { detail: state && state.c }));
  }

  function beacon(decision) {
    if (cfg && cfg.preview) return;
    try {
      fetch(base + "/t/k", { method: "POST", body: JSON.stringify({ d: decision }), keepalive: true,
        mode: "no-cors", credentials: "omit", headers: { "Content-Type": "text/plain" } });
    } catch (e) {}
  }
  function decide(choice, decision) {
    if (!cfg.preview) write(choice);
    close();
    beacon(decision);
    if (!cfg.preview) activate();
    showReopen();
  }

  function css() {
    var a = cfg.accent || "#2563eb";
    var dark = cfg.theme === "dark" || (cfg.theme !== "light" && window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    var bg = dark ? "#18181b" : "#ffffff", fg = dark ? "#f4f4f5" : "#18181b", mut = dark ? "#a1a1aa" : "#52525b", bd = dark ? "#3f3f46" : "#d4d4d8";
    var pos = cfg.position === "bottom" ? "left:0;right:0;bottom:0;border-radius:12px 12px 0 0;" :
      cfg.position === "center" ? "left:50%;top:50%;transform:translate(-50%,-50%);width:min(520px,calc(100vw - 32px));border-radius:12px;" :
      "left:16px;bottom:16px;width:min(400px,calc(100vw - 32px));border-radius:12px;";
    return ":host{all:initial}*{box-sizing:border-box;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}" +
      ".ov{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:2147483646;display:" + (cfg.position === "center" ? "block" : "none") + "}" +
      ".box{position:fixed;z-index:2147483647;background:" + bg + ";color:" + fg + ";border:1px solid " + bd + ";box-shadow:0 10px 40px rgba(0,0,0,.25);padding:20px;" + pos + "}" +
      (cfg.position === "bottom" ? ".in{max-width:1100px;margin:0 auto}" : "") +
      "h2{margin:0 0 6px;font-size:16px;font-weight:600}p{margin:0 0 14px;font-size:13.5px;line-height:1.5;color:" + mut + "}a{color:" + a + "}" +
      ".row{display:flex;flex-wrap:wrap;gap:8px}button{flex:1 1 auto;min-height:38px;padding:8px 14px;border-radius:8px;font-size:13.5px;font-weight:600;cursor:pointer;border:1px solid " + bd + ";background:transparent;color:" + fg + "}" +
      "button:focus-visible{outline:2px solid " + a + ";outline-offset:2px}button.p{background:" + a + ";border-color:" + a + ";color:#fff}" +
      "button.l{border:0;flex:0 0 auto;text-decoration:underline;font-weight:500;color:" + mut + "}" +
      ".cat{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;padding:10px 0;border-top:1px solid " + bd + "}" +
      ".cat b{display:block;font-size:13.5px}.cat span{font-size:12.5px;color:" + mut + "}" +
      "input[type=checkbox]{width:40px;height:22px;accent-color:" + a + ";flex:0 0 auto;margin-top:2px}" +
      ".re{position:fixed;left:16px;bottom:16px;z-index:2147483645;width:40px;height:40px;min-height:0;padding:0;border-radius:50%;background:" + bg + ";box-shadow:0 2px 10px rgba(0,0,0,.25);font-size:18px;flex:none}";
  }

  function build(panel) {
    var t = T[lang()], el = document.createElement("div");
    var title = cfg.title || t.title, msg = cfg.message || t.msg;
    var policy = cfg.policy_url ? ' <a href="' + cfg.policy_url.replace(/"/g, "&quot;") + '" target="_blank" rel="noopener">' + t.policy + "</a>" : "";
    var h = '<div class="ov"></div><div class="box" role="dialog" aria-modal="' + (cfg.position === "center") + '" aria-labelledby="mt"><div class="in">';
    h += '<h2 id="mt"></h2><p class="m"></p>';
    if (panel) {
      h += '<div class="cat"><div><b>' + t.necessary + "</b><span>" + t.necessaryD + '</span></div><input type="checkbox" checked disabled aria-label="' + t.necessary + '"></div>';
      cfg.categories.forEach(function (c) {
        h += '<div class="cat"><div><b>' + t[c] + "</b><span>" + t[c + "D"] + '</span></div><input type="checkbox" data-c="' + c + '"' + (has(c) ? " checked" : "") + ' aria-label="' + t[c] + '"></div>';
      });
      h += '<div class="row" style="margin-top:14px"><button class="p" data-a="save">' + t.save + '</button><button data-a="reject">' + t.reject + "</button></div>";
    } else {
      h += '<div class="row"><button data-a="reject">' + t.reject + '</button><button class="p" data-a="accept">' + t.accept + "</button>" +
        (cfg.categories.length ? '<button data-a="custom">' + t.custom + "</button>" : "") + "</div>";
    }
    el.innerHTML = h + "</div></div>";
    el.querySelector("h2").textContent = title;
    el.querySelector(".m").innerHTML = "";
    el.querySelector(".m").appendChild(document.createTextNode(msg));
    el.querySelector(".m").insertAdjacentHTML("beforeend", policy);
    return el;
  }

  function mount(panel) {
    close();
    if (!host) { host = document.createElement("div"); host.setAttribute("data-mas-cookies", ""); document.body.appendChild(host); root = host.attachShadow({ mode: "open" }); }
    var style = document.createElement("style");
    style.textContent = css();
    root.innerHTML = "";
    root.appendChild(style);
    var el = build(panel);
    root.appendChild(el);
    el.addEventListener("click", function (e) {
      var a = e.target && e.target.getAttribute && e.target.getAttribute("data-a");
      if (!a) return;
      var all = function (v) { var o = {}; cfg.categories.forEach(function (c) { o[c] = v; }); return o; };
      if (a === "accept") decide(all(true), "accept");
      else if (a === "reject") decide(all(false), "reject");
      else if (a === "custom") mount(true);
      else if (a === "save") {
        var o = all(false);
        Array.prototype.forEach.call(el.querySelectorAll("input[data-c]"), function (i) { o[i.getAttribute("data-c")] = i.checked; });
        var n = cfg.categories.filter(function (c) { return o[c]; }).length;
        decide(o, n === cfg.categories.length ? "accept" : n === 0 ? "reject" : "custom");
      }
    });
    var first = el.querySelector("button");
    if (first && !cfg.preview) first.focus({ preventScroll: true });
  }
  function close() { if (root) root.innerHTML = ""; }
  function showReopen() {
    if (!cfg.reopen_button || !root) return;
    var style = document.createElement("style");
    style.textContent = css();
    var b = document.createElement("button");
    b.className = "re"; b.textContent = "🍪"; b.setAttribute("aria-label", T[lang()].settings);
    b.addEventListener("click", function () { mount(true); });
    root.appendChild(style); root.appendChild(b);
  }

  window.masConsent = {
    get: function () { return state && state.c ? Object.assign({ necessary: true }, state.c) : null; },
    has: has,
    open: function () { if (cfg && cfg.enabled) mount(true); },
    on: function (fn) { listeners.push(fn); if (state) fn(state.c); }
  };
  document.addEventListener("click", function (e) {
    var el = e.target && e.target.closest && e.target.closest("[data-mas-cookie-settings]");
    if (el) { e.preventDefault(); window.masConsent.open(); }
  });

  function start(c) {
    cfg = c;
    if (!cfg || !cfg.enabled) return;
    cfg.categories = (cfg.categories || []).filter(function (x) { return CATS.indexOf(x) >= 0; });
    state = cfg.preview ? null : read();
    function go() { if (state) { activate(); showReopen(); } else mount(false); }
    if (state) { if (!host) { host = document.createElement("div"); host.setAttribute("data-mas-cookies", ""); document.body.appendChild(host); root = host.attachShadow({ mode: "open" }); } }
    go();
  }
  function init() {
    if (window.__MAS_COOKIES_PREVIEW__) return start(Object.assign({ preview: true }, window.__MAS_COOKIES_PREVIEW__));
    fetch(base + "/t/c?h=" + encodeURIComponent(location.hostname), { credentials: "omit" })
      .then(function (r) { return r.json(); }).then(start).catch(function () {});
  }
  if (document.body) init(); else document.addEventListener("DOMContentLoaded", init);
})();
"""


# The script holds accented texts: write them as \uXXXX so any page encoding reads them right.
SCRIPT = "".join(c if ord(c) < 128 else (f"\\u{ord(c):04x}" if ord(c) < 0x10000 else
                 "".join(f"\\u{u:04x}" for u in __import__("struct").unpack("<2H", c.encode("utf-16-le"))))
                 for c in SCRIPT)
