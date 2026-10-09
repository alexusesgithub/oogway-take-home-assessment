"""Sanitisation of model-generated HTML artifacts.

Generated HTML is untrusted: a prompt-injected transcript or a confused model
could emit scripts, tracking pixels or phishing forms. The viewer uses three
independent layers so that a gap in one is covered by the others:

1. **Server-side allowlist sanitiser (this module, `nh3`/ammonia)**: only
   presentational tags survive; scripts, event handlers, iframes, objects,
   forms, SVG, meta refresh and `javascript:` URLs are removed; CSS is
   scrubbed of `@import`, `url(...)` (except `data:` images), `expression()`
   and legacy binding hooks; remote images are dropped.
2. **Content-Security-Policy** injected into the rebuilt document:
   `default-src 'none'` so even if something slipped through, the browser
   refuses scripts, network requests, fonts and frames.
3. **Sandboxed iframe** in the UI (`sandbox=""`): no script execution, an
   opaque origin (no access to the app's cookies, storage or DOM), no
   top-level navigation, no popups, no forms.

The report returned alongside lets the UI tell the user exactly what was blocked.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field

import nh3

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "base-uri 'none'; form-action 'none'; frame-src 'none'"
)

ALLOWED_TAGS = set(nh3.ALLOWED_TAGS) | {"main", "section", "label", "progress", "meter"}
ALLOWED_TAGS -= {"area", "map"}
GLOBAL_ATTRS = {"class", "id", "style", "title", "lang", "dir", "role"}
ATTRIBUTES = deepcopy(nh3.ALLOWED_ATTRIBUTES)
ATTRIBUTES["*"] = set(ATTRIBUTES.get("*", set())) | GLOBAL_ATTRS
ATTRIBUTES.setdefault("img", set()).update({"src", "alt", "width", "height"})
ATTRIBUTES.setdefault("progress", set()).update({"value", "max"})
ATTRIBUTES.setdefault("meter", set()).update({"value", "min", "max"})
ATTRIBUTES = {tag: attrs for tag, attrs in ATTRIBUTES.items() if tag == "*" or tag in ALLOWED_TAGS}

# Base styles for readable output even when the model writes minimal CSS.
BASE_CSS = """
:root { color-scheme: light; }
*, *::before, *::after { box-sizing: border-box; }
html { background: #faf7f2; }
body { max-width: 880px; margin: 0 auto; padding: 40px 32px 56px;
       font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
       color: #221d17; background: #ffffff; min-height: 100vh; overflow-wrap: anywhere;
       box-shadow: 0 0 0 1px #e4dacb; }
h1, h2, h3 { line-height: 1.2; letter-spacing: -0.01em; color: #1a1612; }
h1 { font-size: 2em; margin: 0 0 0.4em; padding-bottom: 0.35em; border-bottom: 3px solid #c2410c; }
h2 { font-size: 1.35em; margin: 1.6em 0 0.5em; }
h3 { font-size: 1.1em; margin: 1.3em 0 0.4em; }
p, ul, ol { margin: 0 0 0.9em; }
li { margin: 0.25em 0; }
section, article { margin: 0 0 1.25em; }
blockquote { margin: 1em 0; padding: 0.5em 1em; border-left: 3px solid #c2410c; background: #fdf3ea; }
strong { color: #1a1612; }
img { max-width: 100%; height: auto; }
table { width: 100%; border-collapse: collapse; margin: 1em 0; font-size: 0.95em; }
th, td { padding: 8px 10px; border: 1px solid #e4dacb; text-align: left; vertical-align: top; }
th { background: #f3eee6; }
code { font-family: ui-monospace, Menlo, monospace; font-size: 0.9em; background: #f3eee6; padding: 0.1em 0.3em; border-radius: 4px; }
a { color: #9a3412; }
sup.cite { font: 600 0.62em/1 system-ui, sans-serif; color: #9a3412; background: #fdebd9;
           padding: 2px 4px; border-radius: 4px; margin-left: 2px; vertical-align: 0.5em; }
@media (max-width: 600px) { body { padding: 24px 18px 40px; } h1 { font-size: 1.6em; } }
"""

STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
BODY_RE = re.compile(r"<body\b[^>]*>(.*?)(?:</body>|$)", re.I | re.S)
HEAD_RE = re.compile(r"<head\b[^>]*>.*?</head>", re.I | re.S)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
CSS_DANGEROUS_RE = re.compile(r"expression\s*\(|behavior\s*:|-moz-binding|javascript:", re.I)
CSS_IMPORT_RE = re.compile(r"@import[^;]*;?", re.I)
CITATION_IN_TEXT_RE = re.compile(r"\[S(\d{1,3})\](?![^<]*>)")

COUNTERS = {
    "scripts": re.compile(r"<script\b", re.I),
    "event_handlers": re.compile(r"\son[a-z]+\s*=", re.I),
    "embedded_frames": re.compile(r"<(iframe|frame|object|embed|applet)\b", re.I),
    "forms": re.compile(r"<(form|input|button|select|textarea)\b", re.I),
    "svg": re.compile(r"<svg\b", re.I),
    "meta_refresh_or_base": re.compile(r"<meta[^>]+http-equiv|<base\b", re.I),
    "unsafe_urls": re.compile(r"javascript:", re.I),
    "external_stylesheets": re.compile(r"<link\b[^>]*stylesheet|@import", re.I),
}


@dataclass
class SanitizedHtml:
    html: str
    report: dict[str, int] = field(default_factory=dict)

    @property
    def blocked_total(self) -> int:
        return sum(self.report.values())


CSS_ESCAPE_RE = re.compile(r"\\([0-9a-fA-F]{1,6})\s?")
CSS_IMAGE_FN_RE = re.compile(r"(-webkit-)?\b(image-set|image|src|cross-fade|element)\s*\([^)]*\)?", re.I)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
URL_NOISE_RE = re.compile(r"[\x00-\x20]+")


def _decode_css_escapes(css: str) -> str:
    r"""Undo CSS escapes (\75rl( -> url() so obfuscated URLs cannot dodge the filters."""

    def _char(match: re.Match[str]) -> str:
        code = int(match.group(1), 16)
        return chr(code) if 0 < code < 0x110000 else ""

    return CSS_ESCAPE_RE.sub(_char, css).replace("\\", "")


def sanitize_css(css: str, report: dict[str, int]) -> str:
    css = _decode_css_escapes(css)
    imports = len(CSS_IMPORT_RE.findall(css))
    css = CSS_IMPORT_RE.sub("", css)

    def _url(match: re.Match[str]) -> str:
        target = URL_NOISE_RE.sub("", match.group(2)).lower()
        if target.startswith("data:image/") and not target.startswith("data:image/svg"):
            return match.group(0)
        report["external_resources"] = report.get("external_resources", 0) + 1
        return "none"

    css = CSS_URL_RE.sub(_url, css)
    image_fns = len(CSS_IMAGE_FN_RE.findall(css))
    if image_fns:  # image-set('https://...') etc. load resources without url()
        css = CSS_IMAGE_FN_RE.sub("none", css)
        report["external_resources"] = report.get("external_resources", 0) + image_fns
    dangerous = len(CSS_DANGEROUS_RE.findall(css))
    css = CSS_DANGEROUS_RE.sub("blocked:", css)
    if dangerous:
        report["css_expressions"] = report.get("css_expressions", 0) + dangerous
    if imports:
        report["external_stylesheets"] = max(report.get("external_stylesheets", 0), imports)
    return css.replace("</", r"<\/")


def sanitize_html(document: str, title: str = "Artifact") -> SanitizedHtml:
    report: dict[str, int] = {}
    for name, pattern in COUNTERS.items():
        count = len(pattern.findall(document))
        if count:
            report[name] = count

    document = HTML_COMMENT_RE.sub("", document)  # a "<body>" inside a comment must not fool BODY_RE
    styles = "\n".join(sanitize_css(css, report) for css in STYLE_BLOCK_RE.findall(document))
    body_match = BODY_RE.search(document)
    body = body_match.group(1) if body_match else HEAD_RE.sub("", document)
    body = STYLE_BLOCK_RE.sub("", body)

    def attribute_filter(tag: str, attr: str, value: str) -> str | None:
        if attr == "style":
            return sanitize_css(value, report)
        compact = URL_NOISE_RE.sub("", value).lower()  # defeats "d&#9;ata:" / " javascript:" tricks
        if tag == "img" and attr == "src":
            if compact.startswith("data:image/") and not compact.startswith("data:image/svg"):
                return value
            report["external_resources"] = report.get("external_resources", 0) + 1
            return None
        if compact.startswith(("data:", "javascript:", "vbscript:")):  # data: only for <img>
            report["unsafe_urls"] = report.get("unsafe_urls", 0) + 1
            return None
        return value

    clean_body = nh3.clean(
        body,
        tags=ALLOWED_TAGS,
        clean_content_tags={"script", "style", "noscript", "template", "svg", "math", "iframe", "object"},
        attributes=ATTRIBUTES,
        attribute_filter=attribute_filter,
        url_schemes={"http", "https", "mailto", "data"},
        strip_comments=True,
        link_rel="noopener noreferrer nofollow",
    )
    # Render [S1] citation markers as small badges, in text only (never inside a tag).
    clean_body = CITATION_IN_TEXT_RE.sub(r'<sup class="cite">S\1</sup>', clean_body)
    safe_title = nh3.clean(title, tags=set())
    html = (
        '<!DOCTYPE html>\n<html lang="en"><head>'
        '<meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>{safe_title}</title>"
        f"<style>{BASE_CSS}</style>"
        f"<style>{styles}</style>"
        f"</head><body>{clean_body}</body></html>"
    )
    return SanitizedHtml(html=html, report=report)
