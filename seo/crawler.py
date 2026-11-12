#!/usr/bin/env python3
"""Hovers technical crawl (free, no Semrush units). Fetches the brand's pages (sitemap list first, then internal
links it finds), follows redirects by hand so chains are visible, and records technical issues.

Results feed the issue tracker the team works from (tech_issues): one row per issue type with severity, team,
owner, due date and status. Re-crawling verifies fixes: an issue marked fixed that no longer appears becomes
"verified"; one marked fixed or verified that appears again is reopened.

  python crawler.py --client "Gargi" [--limit 500] [--pause 0.5]
"""
import argparse, datetime as dt, os, re, sys, time
from collections import defaultdict
from urllib.parse import urljoin, urlparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "collectors"))
import requests
from page_parser import parse_html
import sitemap

UA = "HoversSiteCheck/1.0 (+https://hovers.in)"
SLOW_SECONDS = 3.0
DUE_DAYS = {"error": 7, "warning": 14, "notice": 30}

# code: (severity, team, title, how to fix)
ISSUES = {
    "page_4xx":              ("error", "tech", "Page returns a 4xx error", "Restore the page, or 301-redirect it to the closest live page and update links to it."),
    "page_5xx":              ("error", "tech", "Page returns a server error (5xx)", "Check the server or app logs for this URL; fix the error and re-crawl."),
    "broken_internal_link":  ("error", "tech", "Internal link to a broken page", "Change the link to a live page, or redirect the broken target."),
    "missing_title":         ("error", "seo",  "Title tag missing", "Write a unique title with the page's main keyword (SOP: 50–60 characters)."),
    "canonical_broken":      ("error", "tech", "Canonical points to a page that does not load", "Point the canonical at the live version of this page."),
    "redirect_chain":        ("warning", "tech", "Redirect chain (more than one hop)", "Redirect straight to the final URL in one hop."),
    "duplicate_title":       ("warning", "seo",  "Same title on several pages", "Give each page its own title; the pages are listed with the shared title."),
    "missing_description":   ("warning", "seo",  "Meta description missing", "Write a description with the keyword and a reason to click (SOP: 140–160 characters)."),
    "missing_h1":            ("warning", "seo",  "No H1 heading", "Add one H1 that states what the page is about."),
    "noindex_in_sitemap":    ("warning", "tech", "Page in the sitemap is set to noindex", "Either remove noindex or take the page out of the sitemap."),
    "mixed_content":         ("warning", "tech", "Secure page loads http:// files", "Load images, scripts and styles over https."),
    "hreflang_broken":       ("warning", "tech", "hreflang points to a page that does not load", "Fix or remove the hreflang link."),
    "duplicate_description": ("notice", "seo",  "Same meta description on several pages", "Write a description for each page."),
    "multiple_h1":           ("notice", "seo",  "More than one H1", "Keep one H1; make the others H2."),
    "canonical_other":       ("notice", "tech", "Canonical points to a different page", "Check this is intended (filters, variants). If not, make the canonical self-referencing."),
    "link_to_redirect":      ("notice", "tech", "Internal link goes through a redirect", "Link straight to the final URL."),
    "img_missing_alt":       ("notice", "seo",  "Images without alt text", "Describe each product image in its alt text."),
    "slow_page":             ("notice", "tech", f"Server took over {SLOW_SECONDS:.0f}s to respond", "Check caching, apps and heavy sections on this template."),
    "orphan_page":           ("notice", "seo",  "Page in the sitemap that no crawled page links to", "Link to it from a related collection, blog or menu."),
}

def host_key(url):
    return (urlparse(url).hostname or "").lower().removeprefix("www.")

def fetch_chain(session, url, max_hops=5):
    """Follows redirects one at a time. Returns (final_url, status, html, hops, seconds)."""
    hops, t0, cur = [], time.monotonic(), url
    for _ in range(max_hops + 1):
        r = session.get(cur, headers={"User-Agent": UA}, timeout=30, allow_redirects=False)
        loc = (getattr(r, "headers", None) or {}).get("Location") or (getattr(r, "headers", None) or {}).get("location")
        if r.status_code in (301, 302, 303, 307, 308) and loc:
            hops.append((cur, r.status_code)); cur = urljoin(cur, loc); continue
        return cur, r.status_code, (r.text if r.status_code == 200 else ""), hops, time.monotonic() - t0
    return cur, 310, "", hops, time.monotonic() - t0          # too many redirects

def crawl(start_urls, session=None, limit=500, pause=0.5, sitemap_urls=()):
    """Returns {url: record}. Only URLs on the site's own host are fetched."""
    s = session or requests.Session()
    site = host_key(start_urls[0])
    queue, seen, pages = [], set(), {}
    for u in start_urls:
        n = sitemap.normalise(u)
        if n not in seen: seen.add(n); queue.append(n)
    while queue and len(pages) < limit:
        url = queue.pop(0)
        try:
            final, status, html, hops, secs = fetch_chain(s, url)
        except Exception as e:
            pages[url] = {"status": None, "error": str(e)[:200], "hops": [], "final": url, "secs": None, "content": None}; continue
        rec = {"status": status, "final": sitemap.normalise(final), "hops": hops, "secs": secs, "content": None, "raw": None}
        if status == 200 and html:
            rec["content"] = parse_html(final, html)
            rec["hreflang"] = [urljoin(final, h) for h in re.findall(r'<link[^>]+hreflang=["\'][^"\']+["\'][^>]*href=["\']([^"\']+)', html, re.I)]
            rec["mixed"] = final.startswith("https://") and bool(re.search(r'(?:src|href)=["\']http://[^"\']+\.(?:js|css|jpe?g|png|gif|webp|svg)', html, re.I))
            for l in rec["content"]["links"]:
                if not l["internal"] and host_key(l["href"]) != site: continue
                n = sitemap.normalise(l["href"])
                if host_key(n) == site and n not in seen and not re.search(r"\.(jpe?g|png|gif|webp|svg|pdf|zip)$", n, re.I):
                    seen.add(n); queue.append(n)
        pages[url] = rec
        if pause: time.sleep(pause)
    return pages

def find_issues(pages, sitemap_urls=()):
    """{code: [(url, detail), ...]} from crawl records."""
    out = defaultdict(list)
    final_status = {u: r["status"] for u, r in pages.items()}
    titles, descs, linked = defaultdict(list), defaultdict(list), set()
    in_sitemap = {sitemap.normalise(u) for u in sitemap_urls}
    for u, r in pages.items():
        st = r["status"]
        if st is None: out["page_5xx"].append((u, f"could not fetch: {r.get('error')}")); continue
        if 400 <= st < 500: out["page_4xx"].append((u, f"HTTP {st}"))
        elif st >= 500: out["page_5xx"].append((u, f"HTTP {st}"))
        if len(r["hops"]) > 1: out["redirect_chain"].append((u, " → ".join([h[0] for h in r["hops"]] + [r["final"]])))
        if r["secs"] and r["secs"] > SLOW_SECONDS: out["slow_page"].append((u, f"{r['secs']:.1f}s"))
        c = r["content"]
        if not c: continue
        if not c["title"]: out["missing_title"].append((u, None))
        else: titles[c["title"].strip().lower()].append(u)
        if not c["description"]: out["missing_description"].append((u, None))
        else: descs[c["description"].strip().lower()].append(u)
        if not c["h1s"]: out["missing_h1"].append((u, None))
        elif len(c["h1s"]) > 1: out["multiple_h1"].append((u, f"{len(c['h1s'])} H1s"))
        if "noindex" in (c["robots"] or "").lower() and u in in_sitemap: out["noindex_in_sitemap"].append((u, c["robots"]))
        if r.get("mixed"): out["mixed_content"].append((u, None))
        no_alt = sum(1 for i in c["images"] if not (i["alt"] or "").strip())
        if no_alt: out["img_missing_alt"].append((u, f"{no_alt} images"))
        if c["canonical"]:
            can = sitemap.normalise(c["canonical"])
            if can != r["final"]:
                cs = final_status.get(can)
                if cs is not None and cs != 200: out["canonical_broken"].append((u, f"{can} (HTTP {cs})"))
                elif not (re.search(r"/collections/[^/]+/products/", u) and "/products/" in can):   # Shopify's own variant URLs: expected
                    out["canonical_other"].append((u, can))
        for h in r.get("hreflang", []):
            hs = final_status.get(sitemap.normalise(h))
            if hs is not None and hs != 200: out["hreflang_broken"].append((u, h))
        reported = set()
        for l in c["links"]:
            if not l["internal"]: continue
            t = sitemap.normalise(l["href"]); linked.add(t)
            if t == u or t in reported: continue
            tr = pages.get(t)
            if tr is None: continue
            if tr["status"] is not None and tr["status"] >= 400:
                out["broken_internal_link"].append((u, f"{t} (HTTP {tr['status']})")); reported.add(t)
            elif tr["hops"]:
                out["link_to_redirect"].append((u, f"{t} → {tr['final']}")); reported.add(t)
    for group, code in ((titles, "duplicate_title"), (descs, "duplicate_description")):
        for text, urls in group.items():
            if len(urls) > 1: out[code] += [(u, f"shared by {len(urls)} pages: {text[:80]}") for u in urls]
    for u in in_sitemap:
        r = pages.get(u)
        if r and r["status"] == 200 and u not in linked and sitemap.page_key(u) != "/": out["orphan_page"].append((u, None))
    return out

def save(conn, client_id, pages, issues, started_at, today=None):
    today = today or dt.date.today()
    ok = [u for u, r in pages.items() if r["status"] == 200]
    err_urls = {u for code, rows in issues.items() if ISSUES[code][0] == "error" for u, _ in rows}
    counts = {sev: sum(len(rows) for code, rows in issues.items() if ISSUES[code][0] == sev) for sev in ("error", "warning", "notice")}
    health = round(100 * (1 - len(err_urls & set(pages)) / max(len(pages), 1)), 1)
    crawl_id = conn.execute("""INSERT INTO tech_crawls (client_id, source, started_at, finished_at, pages_crawled, health_score, errors, warnings, notices)
                               VALUES (%s,'hovers',%s,now(),%s,%s,%s,%s,%s) RETURNING crawl_id""",
                            [client_id, started_at, len(pages), health, counts["error"], counts["warning"], counts["notice"]]).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO tech_issue_pages (crawl_id, client_id, issue_code, url, detail) VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        [(crawl_id, client_id, code, u, d) for code, rows in issues.items() for u, d in rows])
    for code, rows in issues.items():
        sev, team, title, fix = ISSUES[code]
        conn.execute("""INSERT INTO tech_issues (client_id, issue_code, source, title, severity, team, how_to_fix, pages, due_date)
                        VALUES (%s,%s,'hovers',%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (client_id, issue_code) DO UPDATE SET pages=EXCLUDED.pages, last_seen=now(), title=EXCLUDED.title,
                          how_to_fix=EXCLUDED.how_to_fix,
                          status=CASE WHEN tech_issues.status IN ('fixed','verified') THEN 'open' ELSE tech_issues.status END,
                          updated_at=CASE WHEN tech_issues.status IN ('fixed','verified') THEN now() ELSE tech_issues.updated_at END,
                          due_date=CASE WHEN tech_issues.status IN ('fixed','verified') THEN EXCLUDED.due_date ELSE tech_issues.due_date END""",
                     [client_id, code, title, sev, team, fix, len({u for u, _ in rows}), today + dt.timedelta(days=DUE_DAYS[sev])])
    gone = conn.execute("""UPDATE tech_issues SET pages=0, status=CASE WHEN status='fixed' THEN 'verified' ELSE status END,
                             updated_at=CASE WHEN status='fixed' THEN now() ELSE updated_at END
                           WHERE client_id=%s AND source='hovers' AND NOT (issue_code = ANY(%s)) AND pages > 0 RETURNING issue_code""",
                        [client_id, list(issues)]).fetchall()
    return {"crawl_id": crawl_id, "pages": len(pages), "ok": len(ok), "health": health, **counts, "cleared": len(gone)}

def run(conn, client_id, session=None, limit=500, pause=0.5):
    started = dt.datetime.now(dt.timezone.utc)
    domain = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    sm_urls = [r[0] for r in conn.execute("SELECT url FROM pages WHERE client_id=%s", [client_id])]
    order = sitemap.priority(conn, client_id) if sm_urls else []
    by_id = dict(conn.execute("SELECT page_id, url FROM pages WHERE client_id=%s", [client_id]).fetchall())
    home = sm_urls and next((u for u in sm_urls if sitemap.page_key(u) == "/"), None)
    start = [home or f"https://{domain.replace('https://', '').replace('http://', '').strip('/')}/"] + [by_id[i] for i in order if i in by_id]
    pages = crawl(start, session, limit, pause)
    issues = find_issues(pages, sm_urls)
    return save(conn, client_id, pages, issues, started)

if __name__ == "__main__":
    from common import connect
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True); ap.add_argument("--limit", type=int, default=500); ap.add_argument("--pause", type=float, default=0.5)
    a = ap.parse_args()
    with connect() as conn:
        cid = conn.execute("SELECT client_id FROM clients WHERE client_id::text=%s OR lower(name)=lower(%s)", [a.client, a.client]).fetchone()[0]
        print(run(conn, cid, limit=a.limit, pause=a.pause))
