"""Page discovery from a Shopify-style sitemap (fake HTTP), keyword assignment from Search Console and Semrush,
priority order, and the page check limit. Needs DATABASE_URL (aeo_app) on a migrated database."""
import os, sys, datetime as dt
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo")]
from common import connect
import sitemap, run_checks

assert sitemap.classify("https://gargi.shop/") == "home"
assert sitemap.classify("https://gargi.shop/collections/silver-earrings") == "collection"
assert sitemap.classify("https://gargi.shop/collections/rings/products/x") == "product"
assert sitemap.classify("https://gargi.shop/products/evil-eye-anklet") == "product"
assert sitemap.classify("https://gargi.shop/blogs/new/how-to-clean-silver") == "blog"
assert sitemap.classify("https://gargi.shop/blogs/new") == "static"
assert sitemap.classify("https://gargi.shop/pages/store-locator") == "static"
assert sitemap.normalise("https://WWW.Gargi.shop/collections/x/?utm=1") == "https://www.gargi.shop/collections/x"

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
SITE = {
  "https://gargi.shop/robots.txt": (200, "User-agent: *\nSitemap: https://www.gargi.shop/sitemap.xml\n"),
  "https://www.gargi.shop/sitemap.xml": (200, f'<?xml version="1.0"?><sitemapindex {NS}>' + "".join(
      f"<sitemap><loc>https://www.gargi.shop/{n}.xml</loc></sitemap>" for n in ("sitemap_products_1", "sitemap_collections_1", "sitemap_pages_1", "sitemap_blogs_1", "broken")) + "</sitemapindex>"),
  "https://www.gargi.shop/sitemap_products_1.xml": (200, f'<urlset {NS}><url><loc>https://www.gargi.shop/</loc></url>'
      + "".join(f"<url><loc>https://www.gargi.shop/products/item-{i}</loc></url>" for i in range(5))
      + '<url><loc>https://cdn.shopify.com/s/files/x.jpg</loc></url></urlset>'),
  "https://www.gargi.shop/sitemap_collections_1.xml": (200, f'<urlset {NS}><url><loc>https://www.gargi.shop/collections/silver-earrings</loc></url>'
      '<url><loc>https://www.gargi.shop/collections/silver-mangalsutra/</loc></url></urlset>'),
  "https://www.gargi.shop/sitemap_pages_1.xml": (200, f'<urlset {NS}><url><loc>https://www.gargi.shop/pages/store-locator</loc></url></urlset>'),
  "https://www.gargi.shop/sitemap_blogs_1.xml": (200, f'<urlset {NS}><url><loc>https://www.gargi.shop/blogs/new/how-to-clean-silver</loc></url></urlset>'),
  "https://www.gargi.shop/broken.xml": (200, "<not xml"),
}
class R:
    def __init__(s, c, t): s.status_code, s.text = c, t
class Fake:
    def __init__(s, pages): s.pages, s.calls = pages, []
    def get(s, url, headers=None, timeout=None, allow_redirects=True):
        s.calls.append(url); return R(*s.pages.get(url, (404, "")))

with connect() as conn:
    conn.execute("DELETE FROM clients WHERE domain='gargi-sitemap-test.shop'")
    cid = conn.execute("INSERT INTO clients (name, domain) VALUES ('Gargi','gargi-sitemap-test.shop') RETURNING client_id").fetchone()[0]
    fake = Fake({k.replace("gargi.shop", "gargi-sitemap-test.shop"): (c, t.replace("gargi.shop", "gargi-sitemap-test.shop")) for k, (c, t) in SITE.items()})
    day = dt.date.today() - dt.timedelta(days=5)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO gsc_search_daily VALUES
            (%(c)s,%(d)s,'silver earrings','https://gargi-sitemap-test.shop/collections/silver-earrings/','ind','MOBILE',40,900,0.04,4.2,false),
            (%(c)s,%(d)s,'gargi earrings','https://gargi-sitemap-test.shop/collections/silver-earrings/','ind','MOBILE',90,200,0.4,1.0,true),
            (%(c)s,%(d)s,'oxidised anklet','https://www.gargi-sitemap-test.shop/products/item-3','ind','MOBILE',25,400,0.06,6.0,false)""",
                    {"c": cid, "d": day})
        cur.execute("""INSERT INTO semrush_keywords (client_id, domain, database, pulled_on, keyword, position, url, traffic_pct)
                       VALUES (%s,'gargi-sitemap-test.shop','in',%s,'silver mangalsutra',5,'https://www.gargi-sitemap-test.shop/collections/silver-mangalsutra',0.7)""",
                    [cid, dt.date.today()])
    res = sitemap.sync_pages(conn, cid, session=fake)
    assert res == {"found": 10, "added": 10, "keywords": 3}, res          # cdn image and broken child sitemap skipped
    kinds = dict(conn.execute("SELECT page_type::text, count(*) FROM pages WHERE client_id=%s GROUP BY 1", [cid]).fetchall())
    assert kinds == {"home": 1, "product": 5, "collection": 2, "static": 1, "blog": 1}, kinds
    kw = dict(conn.execute("SELECT url, primary_keyword FROM pages WHERE client_id=%s AND primary_keyword IS NOT NULL", [cid]).fetchall())
    assert kw == {"https://www.gargi-sitemap-test.shop/collections/silver-earrings": "silver earrings",      # non-branded beats branded clicks
                  "https://www.gargi-sitemap-test.shop/products/item-3": "oxidised anklet",
                  "https://www.gargi-sitemap-test.shop/collections/silver-mangalsutra": "silver mangalsutra"}, kw   # from Semrush
    conn.execute("UPDATE pages SET primary_keyword='silver jhumka' WHERE client_id=%s AND url LIKE '%%silver-earrings'", [cid])
    again = sitemap.sync_pages(conn, cid, session=fake)
    assert again["added"] == 0 and conn.execute("SELECT primary_keyword FROM pages WHERE client_id=%s AND url LIKE '%%silver-earrings'", [cid]).fetchone()[0] == "silver jhumka"
    order = [conn.execute("SELECT url FROM pages WHERE page_id=%s", [i]).fetchone()[0].rsplit("/", 1)[-1] for i in sitemap.priority(conn, cid)]
    assert order[0] == "" and set(order[1:3]) == {"silver-earrings", "silver-mangalsutra"} and order[3] == "item-3", order
    html = {u: (200, f"<html><head><title>{u}</title></head><body><h1>x</h1></body></html>") for u in
            [r[0] for r in conn.execute("SELECT url FROM pages WHERE client_id=%s", [cid])]}
    checked = run_checks.check_live(conn, cid, session=Fake(html), pause=0, limit=4)
    assert len(checked) == 4 and checked[0][0].endswith("gargi-sitemap-test.shop/"), checked
    conn.execute("DELETE FROM clients WHERE client_id=%s", [cid])
print("sitemap: 10 pages found and typed, keywords from Search Console then Semrush, people's keywords kept, top-priority pages checked first")
