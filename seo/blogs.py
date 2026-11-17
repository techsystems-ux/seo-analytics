"""Blog drafts: clean the HTML, keep links honest, add the FAQ block and schema, and score the draft with the same
on-page checks Orbit runs on live pages. Used by the writer (collectors/blog_writer.py), the review screen and publishing.
Standard library only, so it runs on Vercel and on the Mac."""
import html as H, json, re, secrets
from html.parser import HTMLParser
from urllib.parse import urlparse

ALLOWED = {"h2", "h3", "h4", "p", "ul", "ol", "li", "strong", "b", "em", "i", "a", "blockquote", "table", "thead", "tbody",
           "tr", "th", "td", "br", "hr", "img"}
VOID = {"br", "hr", "img"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "form", "noscript", "svg", "template"}

def host(u):
    h = (urlparse(u or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h

def bare(domain):
    d = (domain or "").lower().replace("https://", "").replace("http://", "").strip("/")
    return d[4:] if d.startswith("www.") else d

def norm_url(u):
    return (u or "").split("#")[0].split("?")[0].rstrip("/").lower().replace("://www.", "://")

class _Clean(HTMLParser):
    """Keeps a small set of tags; internal links only to known pages (others become plain text); outside links get nofollow."""
    def __init__(self, domain, allowed_internal):
        super().__init__(convert_charrefs=False)
        self.out, self.drop, self.domain = [], 0, bare(domain)
        self.allowed = None if allowed_internal is None else {norm_url(u) for u in allowed_internal}   # None: any page of the site
        self.a_stack, self.internal, self.external = [], [], []

    def _is_internal(self, href):
        h = host(href)
        return href.startswith("/") or (h and (h == self.domain or h.endswith("." + self.domain)))

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT: self.drop += 1; return
        if self.drop or tag not in ALLOWED: return
        a = dict(attrs)
        if tag == "a":
            href = (a.get("href") or "").strip()
            if href.startswith("/") and self.domain: href = f"https://{self.domain}{href}"
            if not href.startswith(("http://", "https://")): self.a_stack.append(False); return
            if self._is_internal(href):
                if (self.allowed is not None and norm_url(href) not in self.allowed) or norm_url(href) in {norm_url(x) for x in self.internal}:
                    self.a_stack.append(False); return          # unknown or repeated internal link: keep the words, drop the link
                self.internal.append(href); self.a_stack.append(True)
                self.out.append(f'<a href="{H.escape(href, quote=True)}">'); return
            self.external.append(href); self.a_stack.append(True)
            self.out.append(f'<a href="{H.escape(href, quote=True)}" rel="nofollow noopener" target="_blank">'); return
        if tag == "img":
            src = a.get("src") or ""
            if src.startswith("https://"):
                self.out.append(f'<img src="{H.escape(src, quote=True)}" alt="{H.escape(a.get("alt") or "", quote=True)}" loading="lazy">')
            return
        self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT: self.drop = max(0, self.drop - 1); return
        if self.drop or tag not in ALLOWED or tag in VOID: return
        if tag == "a":
            if self.a_stack and self.a_stack.pop(): self.out.append("</a>")
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, d):
        if not self.drop: self.out.append(H.escape(d, quote=False))
    def handle_entityref(self, name):
        if not self.drop: self.out.append(f"&{name};")
    def handle_charref(self, name):
        if not self.drop: self.out.append(f"&#{name};")

def dashes(s):
    """House rule WRITE-01: no em dashes (a comma reads the same)."""
    s = re.sub(r"\s*—\s*", ", ", s or "")
    return s.replace(" ,", ",")

def clean_body(body_html, domain, allowed_internal):
    """-> (clean html, internal links kept, outside links). allowed_internal=None keeps any link to the brand's own site
    (a person edited it); a list keeps only those pages (the writer, so it cannot invent URLs)."""
    body = re.sub(r"<h1[^>]*>.*?</h1>", "", body_html or "", flags=re.S | re.I)   # the title is the only H1
    p = _Clean(domain, allowed_internal); p.feed(dashes(body)); p.close()
    out = re.sub(r"<p>\s*</p>", "", "".join(p.out))
    return out.strip(), p.internal, p.external

def faq_html(faqs):
    if not faqs: return ""
    items = "".join(f"<h3>{H.escape(dashes(f['q']))}</h3><p>{H.escape(dashes(f['a']))}</p>" for f in faqs if f.get("q") and f.get("a"))
    return f'<h2>Frequently asked questions</h2>{items}'

def faq_schema(faqs):
    data = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": dashes(f["q"]), "acceptedAnswer": {"@type": "Answer", "text": dashes(f["a"])}}
        for f in faqs if f.get("q") and f.get("a")]}
    return f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>' if data["mainEntity"] else ""

def article_body(d, with_schema=True):
    """What goes into the store: the body, the FAQ block, and FAQPage schema (themes already add Article schema)."""
    return (d.get("body_html") or "") + faq_html(d.get("faqs") or []) + (faq_schema(d.get("faqs") or []) if with_schema else "")

def slugify(s, limit=60):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    if len(s) > limit: s = s[:limit].rsplit("-", 1)[0]
    return s or "post"

def words(html_text):
    return len(re.findall(r"\b\w[\w'-]*\b", re.sub(r"<[^>]+>", " ", html_text or "")))

def predicted_url(domain, blog_handle, slug):
    return f"https://{bare(domain)}/blogs/{blog_handle or 'news'}/{slug or 'post'}"

def page_html(d, url, author, brand_name):
    """The draft as a full page, so the live-page checks (title, description, headings, links, schema) apply unchanged."""
    art = {"@context": "https://schema.org", "@type": "BlogPosting", "headline": d.get("title"),
           "author": {"@type": "Person" if author and author != brand_name else "Organization", "name": author or brand_name}}
    return (f"<html><head><title>{H.escape(d.get('meta_title') or d.get('title') or '')}</title>"
            f"<meta name=\"description\" content=\"{H.escape(d.get('meta_description') or '', quote=True)}\">"
            f"<meta property=\"og:title\" content=\"{H.escape(d.get('meta_title') or '', quote=True)}\">"
            f"<meta property=\"og:description\" content=\"{H.escape(d.get('meta_description') or '', quote=True)}\">"
            f"<script type=\"application/ld+json\">{json.dumps(art)}</script></head><body><article>"
            f"<h1>{H.escape(d.get('title') or '')}</h1><p class=\"byline\">By {H.escape(author or brand_name or '')}</p>"
            f"{article_body(d)}</article></body></html>")

def check(d, domain, brand_rules=None, blog_handle=None, author=None, brand_name=None):
    """-> (results, score): the SOP blog checks on this draft."""
    from page_parser import parse_html
    from checks import run_page_checks, score
    url = predicted_url(domain, blog_handle, d.get("slug"))
    content = parse_html(url, page_html(d, url, author, brand_name))
    results = run_page_checks(content, {"page_type": "blog", "primary_keyword": d.get("target_keyword"),
                                        "secondary_keywords": d.get("secondary_keywords") or []}, brand_rules or {})
    return results, score(results)

def new_token():
    return secrets.token_urlsafe(18)
