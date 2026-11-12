"""Turn a page's HTML into the plain fields the SOP checks need. Standard library only.

parse_html(url, html) -> dict with: title, description, og_title, og_description, canonical, robots,
headings [(level, text)], h1s, text, word_count, links [{href, text, rel, internal}], images [{src, alt}],
schema_types, faqs [question], product {has_price, has_availability, has_rating}, article_author, html_bytes, text_ratio.

draft_content(...) builds the same shape from fields typed in the tool, so drafts and live pages run the same checks.
"""
import json, re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

SKIP_TEXT = {"script", "style", "noscript", "template", "svg"}
CHROME = {"header", "nav", "footer"}          # site chrome: not counted as page copy
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

def host_of(url):
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h

class _P(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.stack = base, []
        self.title, self.meta, self.canonical = "", {}, None
        self.headings, self.links, self.images, self.ld = [], [], [], []
        self.text, self._head = [], None          # _head = [level, parts] while inside h1-h6
        self._a, self._ld, self._title = None, None, False

    def _skipping(self): return any(t in SKIP_TEXT for t in self.stack)
    def _in_chrome(self): return any(t in CHROME for t in self.stack)

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag not in VOID: self.stack.append(tag)
        if tag == "title": self._title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key: self.meta[key] = a.get("content", "").strip()
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = urljoin(self.base, a.get("href", ""))
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._ld = []
        elif re.fullmatch(r"h[1-6]", tag):
            self._head = [int(tag[1]), []]
        elif tag == "a" and a.get("href"):
            self._a = {"href": urljoin(self.base, a["href"].strip()), "rel": a.get("rel", "").lower(), "parts": [], "chrome": self._in_chrome()}
        elif tag == "img":
            self.images.append({"src": urljoin(self.base, a.get("src", "")), "alt": a.get("alt") if "alt" in a else None})

    def handle_endtag(self, tag):
        if tag == "title": self._title = False
        if tag == "script" and self._ld is not None:
            self.ld.append("".join(self._ld)); self._ld = None
        if re.fullmatch(r"h[1-6]", tag) and self._head:
            self.headings.append((self._head[0], " ".join("".join(self._head[1]).split()))); self._head = None
        if tag == "a" and self._a:
            a = self._a; self._a = None
            self.links.append({"href": a["href"], "rel": a["rel"], "text": " ".join("".join(a["parts"]).split()), "chrome": a["chrome"]})
        if tag in self.stack:                       # tolerate unclosed tags
            while self.stack and self.stack.pop() != tag: pass

    def handle_data(self, d):
        if self._title: self.title += d
        if self._ld is not None: self._ld.append(d); return
        if self._skipping(): return
        if self._head: self._head[1].append(d)
        if self._a: self._a["parts"].append(d)
        if not self._in_chrome(): self.text.append(d)

def _walk_ld(obj, out):
    if isinstance(obj, list):
        for o in obj: _walk_ld(o, out)
    elif isinstance(obj, dict):
        out.append(obj)
        for k in ("@graph", "mainEntity", "itemListElement"):
            if k in obj: _walk_ld(obj[k], out)

def _types(o):
    t = o.get("@type", [])
    return [t] if isinstance(t, str) else list(t)

def parse_html(url, html):
    p = _P(url); p.feed(html); p.close()
    objs = []
    for raw in p.ld:
        try: _walk_ld(json.loads(raw), objs)
        except ValueError: continue                 # broken JSON-LD is reported by the schema checks as missing
    types = sorted({t for o in objs for t in _types(o)})
    faqs = [o.get("name", "").strip() for o in objs if "Question" in _types(o) and o.get("name")]
    if not faqs:                                    # no FAQ schema: count question-style headings in the copy
        faqs = [t for lvl, t in p.headings if lvl >= 2 and (t.endswith("?") or re.match(r"^Q\d+[.:)]", t))]
    prod = next((o for o in objs if "Product" in _types(o)), None)
    product = None
    if prod:
        offers = prod.get("offers") or {}
        offers = offers[0] if isinstance(offers, list) and offers else offers
        offers = offers if isinstance(offers, dict) else {}
        product = {"has_price": bool(offers.get("price") or offers.get("lowPrice")),
                   "has_availability": bool(offers.get("availability")),
                   "has_rating": bool(prod.get("aggregateRating"))}
    art = next((o for o in objs if set(_types(o)) & {"Article", "BlogPosting", "NewsArticle"}), None)
    author = None
    if art and art.get("author"):
        au = art["author"]; au = au[0] if isinstance(au, list) and au else au
        author = au.get("name") if isinstance(au, dict) else str(au)
    text = " ".join(" ".join(p.text).split())
    site = host_of(url)
    links = [{**l, "internal": host_of(l["href"]) == site} for l in p.links if urlparse(l["href"]).scheme in ("http", "https")]
    return {
        "url": url, "title": " ".join(p.title.split()), "description": p.meta.get("description", ""),
        "og_title": p.meta.get("og:title", ""), "og_description": p.meta.get("og:description", ""),
        "canonical": p.canonical, "robots": p.meta.get("robots", ""),
        "headings": p.headings, "h1s": [t for lvl, t in p.headings if lvl == 1],
        "text": text, "word_count": len(re.findall(r"[\w'’-]+", text)),
        "links": links, "images": p.images, "schema_types": types, "faqs": faqs,
        "product": product, "article_author": author,
        "html_bytes": len(html.encode()), "text_ratio": round(len(text.encode()) / max(len(html.encode()), 1), 3),
    }

def draft_content(url, title="", description="", h1="", body="", faqs=(), intro="", read_more="",
                  og_title="", og_description="", links=(), schema_types=(), author=None):
    """A draft typed in the tool, in the same shape as parse_html. Body is plain text; ## lines are H2s."""
    headings = [(1, h1)] if h1 else []
    for line in (body + "\n" + read_more).splitlines():
        m = re.match(r"^(#{2,4})\s+(.*)", line.strip())
        if m: headings.append((len(m.group(1)), m.group(2).strip()))
    text = " ".join(" ".join([intro, re.sub(r"^#+\s*", "", body, flags=re.M), read_more, " ".join(faqs)]).split())
    site = host_of(url)
    return {
        "url": url, "title": title, "description": description, "og_title": og_title, "og_description": og_description,
        "canonical": url, "robots": "", "headings": headings, "h1s": [h1] if h1 else [], "text": text,
        "word_count": len(re.findall(r"[\w'’-]+", text)), "intro": intro, "read_more": read_more, "body": body,
        "links": [{**l, "internal": host_of(l["href"]) == site, "chrome": False} for l in links],
        "images": [], "schema_types": list(schema_types), "faqs": list(faqs), "product": None,
        "article_author": author, "html_bytes": None, "text_ratio": None,
    }
