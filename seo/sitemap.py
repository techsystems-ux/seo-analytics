"""Find every page of a brand's site from its sitemap, sort it into home / collection / product / blog / static,
and give each page a primary keyword from real data. No file upload needed.

sync_pages(conn, client_id) -> {"found": n, "added": n, "keywords": n}
  1. Read robots.txt for Sitemap: lines, else /sitemap.xml. Follow sitemap indexes (Shopify splits products,
     collections, pages and blogs into child sitemaps).
  2. Add new URLs to `pages` (existing pages, and keywords a person set, are never changed).
  3. Pages without a primary keyword get the non-branded Search Console query that sent them the most clicks in the
     last 90 days, else the Semrush keyword that page ranks for with the most traffic. Marked so a person can change it.
"""
import re, xml.etree.ElementTree as ET
from urllib.parse import urlparse, urlunparse
import requests

UA = "HoversSiteCheck/1.0 (+https://hovers.in)"
MAX_URLS = 20000                       # safety stop for very large sites
NS = re.compile(r"^\{[^}]+\}")

def tag(e): return NS.sub("", e.tag)

def normalise(url):
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    path = re.sub(r"/{2,}", "/", u.path or "/")
    if len(path) > 1: path = path.rstrip("/")
    return urlunparse(("https", host, path, "", "", ""))

def page_key(url):
    """Host-free path, for matching sitemap URLs to Search Console and GA4 pages (www or not, slash or not)."""
    p = urlparse(url).path or "/"
    return p.rstrip("/") or "/"

def classify(url):
    p = page_key(url).lower()
    if p == "/": return "home"
    if p.startswith("/products/") or re.match(r"^/collections/[^/]+/products/", p): return "product"
    if p.startswith("/collections/"): return "collection"
    if re.match(r"^/blogs/[^/]+/[^/]+", p) or re.match(r"^/(blog|news|articles)/[^/]+", p): return "blog"
    return "static"

def fetch(session, url):
    r = session.get(url, headers={"User-Agent": UA}, timeout=30)
    return r.status_code, r.text

def sitemap_roots(session, domain):
    base = f"https://{domain}"
    roots = []
    try:
        code, text = fetch(session, base + "/robots.txt")
        if code == 200: roots = [l.split(":", 1)[1].strip() for l in text.splitlines() if l.lower().startswith("sitemap:")]
    except Exception:
        pass
    return roots or [base + "/sitemap.xml"]

def discover(session, domain):
    """-> list of page URLs from every sitemap reachable from the site's root sitemaps."""
    todo, seen, pages = sitemap_roots(session, domain), set(), []
    site = domain.lower().replace("www.", "")
    while todo and len(pages) < MAX_URLS:
        sm = todo.pop(0)
        if sm in seen: continue
        seen.add(sm)
        try:
            code, text = fetch(session, sm)
            if code != 200: continue
            root = ET.fromstring(text.encode("utf-8"))
        except Exception:
            continue                                  # one broken child sitemap never stops the rest
        locs = [c.text.strip() for e in root for c in e if tag(c) == "loc" and c.text]
        if tag(root) == "sitemapindex": todo += locs
        else: pages += [u for u in locs if (urlparse(u).hostname or "").lower().replace("www.", "") == site]
    return list(dict.fromkeys(normalise(u) for u in pages))

def fill_keywords(conn, client_id):
    """Primary keyword for pages that have none, from Search Console (non-branded, most clicks, 90 days), else Semrush."""
    missing = conn.execute("""SELECT page_id, url FROM pages WHERE client_id=%s AND (primary_keyword IS NULL OR intent='auto')""",
                           [client_id]).fetchall()                  # keywords picked automatically are refreshed; a person's are kept
    if not missing: return 0
    best = {}
    for page, query, clicks in conn.execute("""SELECT page, query, sum(clicks) c FROM gsc_search_daily
                                               WHERE client_id=%s AND NOT is_branded AND date > current_date - 90
                                               GROUP BY page, query HAVING sum(clicks) > 0""", [client_id]):
        k = page_key(page)
        if k not in best or clicks > best[k][1]: best[k] = (query, clicks)
    for url, kw, traffic in conn.execute("""SELECT url, keyword, coalesce(traffic_pct,0) FROM semrush_keywords
                                            WHERE client_id=%s AND url IS NOT NULL ORDER BY traffic_pct DESC NULLS LAST""", [client_id]):
        k = page_key(url)
        best.setdefault(k, (kw, -1))                 # Semrush only where Search Console has nothing
    n = 0
    for pid, url in missing:
        hit = best.get(page_key(url))
        if hit:
            conn.execute("UPDATE pages SET primary_keyword=%s, intent='auto' WHERE page_id=%s AND primary_keyword IS DISTINCT FROM %s",
                         [hit[0], pid, hit[0]]); n += 1
    return n

def sync_pages(conn, client_id, session=None):
    session = session or requests.Session()
    domain = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    domain = re.sub(r"^https?://", "", domain.lower()).strip("/").split("/")[0]
    urls = discover(session, domain) or discover(session, "www." + domain if not domain.startswith("www.") else domain[4:])
    added = 0
    with conn.cursor() as cur:
        for u in urls:
            cur.execute("""INSERT INTO pages (client_id, url, page_type) VALUES (%s,%s,%s) ON CONFLICT (client_id, url) DO NOTHING""",
                        [client_id, u, classify(u)])
            added += cur.rowcount
    try:                                                  # keep branded/non-branded labels current before picking keywords
        import gsc
        name, dom, aliases = conn.execute("SELECT name, domain, aliases FROM clients WHERE client_id=%s", [client_id]).fetchone()
        gsc.relabel_branded(conn, client_id, gsc.brand_terms(name, dom, aliases))
    except ImportError:
        pass
    return {"found": len(urls), "added": added, "keywords": fill_keywords(conn, client_id)}

def priority(conn, client_id):
    """Pages in checking order: home, collections, then everything else by Search Console clicks (90 days)."""
    clicks = {}
    for page, c in conn.execute("""SELECT page, sum(clicks) FROM gsc_search_daily WHERE client_id=%s AND date > current_date - 90
                                   GROUP BY page""", [client_id]):
        clicks[page_key(page)] = clicks.get(page_key(page), 0) + c
    rows = conn.execute("SELECT page_id, url, page_type FROM pages WHERE client_id=%s", [client_id]).fetchall()
    rank = {"home": 0, "collection": 1, "blog": 2, "static": 3, "product": 3}
    return [r[0] for r in sorted(rows, key=lambda r: (rank[r[2]] if rank[r[2]] < 2 else 2, -clicks.get(page_key(r[1]), 0), r[1]))]
