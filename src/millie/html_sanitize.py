from __future__ import annotations

import html
from html.parser import HTMLParser
from urllib.parse import urlparse


ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "dd",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

VOID_TAGS = {"br", "hr"}
SKIP_CONTENT_TAGS = {
    "applet",
    "base",
    "button",
    "embed",
    "form",
    "iframe",
    "input",
    "link",
    "math",
    "meta",
    "object",
    "script",
    "select",
    "style",
    "svg",
    "textarea",
}
GLOBAL_ATTRS = {"dir", "lang", "title"}
TAG_ATTRS = {
    "a": {"href"},
    "blockquote": {"cite"},
    "ol": {"start", "type"},
    "td": {"align", "colspan", "rowspan"},
    "th": {"align", "colspan", "rowspan", "scope"},
}
SAFE_URL_SCHEMES = {"", "http", "https", "mailto"}


class MillieHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        clean_tag = tag.lower()
        if clean_tag in SKIP_CONTENT_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if clean_tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(f"<span>{html.escape(alt, quote=False)}</span>")
            return
        if clean_tag not in ALLOWED_TAGS:
            return
        rendered_attrs = self.safe_attrs(clean_tag, attrs)
        attr_text = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in rendered_attrs)
        self.parts.append(f"<{clean_tag}{attr_text}>")

    def handle_endtag(self, tag: str) -> None:
        clean_tag = tag.lower()
        if clean_tag in SKIP_CONTENT_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth or clean_tag not in ALLOWED_TAGS or clean_tag in VOID_TAGS:
            return
        self.parts.append(f"</{clean_tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self.skip_depth:
            self.parts.append(f"&{html.escape(name)};")

    def handle_charref(self, name: str) -> None:
        if not self.skip_depth:
            self.parts.append(f"&#{html.escape(name)};")

    def safe_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
        allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, set())
        safe: list[tuple[str, str]] = []
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            if name.startswith("on") or name not in allowed or raw_value is None:
                continue
            value = raw_value.strip()
            if name in {"href", "cite"} and not is_safe_url(value):
                continue
            safe.append((name, value))
        if tag == "a" and any(name == "href" for name, _value in safe):
            safe.append(("rel", "noreferrer noopener"))
            safe.append(("target", "_blank"))
        return safe

    def sanitized(self) -> str:
        return "".join(self.parts)


def is_safe_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme.lower() not in SAFE_URL_SCHEMES:
        return False
    return bool(parsed.scheme) or not parsed.netloc


def sanitize_html_fragment(value: str) -> str:
    parser = MillieHTMLSanitizer()
    parser.feed(value)
    parser.close()
    return parser.sanitized()


def sanitize_html_document(value: str) -> str:
    fragment = sanitize_html_fragment(value)
    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8"></head>'
        f'<body class="millie-message-html">{fragment}</body></html>'
    )
