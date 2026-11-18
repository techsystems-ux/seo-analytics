"""Search Console beyond search data (free API, same sign-in and scope as gsc.py):
  sitemaps     every sitemap submitted in Search Console, with its errors, warnings and URL counts
  inspection   Google's index status for each page (URL Inspection API: 2,000 URLs a day per site, one call per URL).
               Pages with Search Console clicks come first, then the sitemap's pages; each is re-checked after 7 days.
sync(conn, session, client_id, site_url) -> "n sitemaps, n URLs inspected"
"""
import json, time
from urllib.parse import quote

SITEMAPS = "https://www.googleapis.com/webmasters/v3/sites/{site}/sitemaps"
INSPECT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
RECHECK_DAYS = 7
TIME_CAP = 90           # ponytail: seconds per run, so a worker task ends well inside Vercel's 300; the rest waits for the next night

def sync_sitemaps(conn, session, client_id, site_url):
    r = session.get(SITEMAPS.format(site=quote(site_url, safe="")), timeout=60)
    if r.status_code != 200: raise RuntimeError(f"Sitemaps {r.status_code}: {r.text[:200]}")
    maps = r.json().get("sitemap", [])
    with conn.transaction():                     # replace: a sitemap removed in Search Console disappears here too
        conn.execute("DELETE FROM gsc_sitemaps WHERE client_id=%s", [client_id])
        for m in maps:
            conn.execute("""INSERT INTO gsc_sitemaps (client_id, path, type, is_index, is_pending, last_submitted, last_downloaded,
                                                      errors, warnings, contents) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                         [client_id, m["path"], m.get("type"), m.get("isSitemapsIndex", False), m.get("isPending", False),
                          m.get("lastSubmitted"), m.get("lastDownloaded"), int(m.get("errors") or 0), int(m.get("warnings") or 0),
                          json.dumps(m.get("contents", []))])
    return len(maps)

def urls_to_inspect(conn, client_id, limit):
    """Never-inspected first, then the oldest; within each, the pages with the most clicks in the last 90 days."""
    return [r[0] for r in conn.execute("""
        WITH u AS (SELECT page AS url, sum(clicks) w FROM gsc_search_daily WHERE client_id=%(c)s AND date > current_date - 90 GROUP BY page
                   UNION ALL SELECT url, -1 FROM pages WHERE client_id=%(c)s),
             best AS (SELECT url, max(w) w FROM u GROUP BY url)
        SELECT best.url FROM best LEFT JOIN gsc_url_inspection i ON i.client_id=%(c)s AND i.url=best.url
        WHERE i.url IS NULL OR i.inspected_at < now() - make_interval(days => %(d)s)
        ORDER BY i.inspected_at NULLS FIRST, best.w DESC LIMIT %(n)s""", {"c": client_id, "d": RECHECK_DAYS, "n": limit})]

def parse_inspection(res):
    """Pure function: URL Inspection response -> the columns we keep."""
    ix = res.get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    rich = res.get("inspectionResult", {}).get("richResultsResult", {}) or {}
    types = sorted({i.get("richResultType") for i in rich.get("detectedItems", []) if i.get("richResultType")})
    return {"verdict": ix.get("verdict"), "coverage_state": ix.get("coverageState"), "indexing_state": ix.get("indexingState"),
            "robots_state": ix.get("robotsTxtState"), "fetch_state": ix.get("pageFetchState"), "last_crawl": ix.get("lastCrawlTime"),
            "crawled_as": ix.get("crawledAs"), "google_canonical": ix.get("googleCanonical"), "user_canonical": ix.get("userCanonical"),
            "rich_verdict": rich.get("verdict"), "rich_types": ", ".join(types) or None}

COLS = ["verdict", "coverage_state", "indexing_state", "robots_state", "fetch_state", "last_crawl", "crawled_as",
        "google_canonical", "user_canonical", "rich_verdict", "rich_types"]
UPSERT = f"""INSERT INTO gsc_url_inspection (client_id, url, inspected_at, {', '.join(COLS)}) VALUES (%s, %s, now(), {', '.join(['%s'] * len(COLS))})
ON CONFLICT (client_id, url) DO UPDATE SET inspected_at=now(), {', '.join(f'{c}=EXCLUDED.{c}' for c in COLS)}"""

def inspect_urls(conn, session, client_id, site_url, limit=200, cap=TIME_CAP):
    stop, done = time.time() + cap, 0
    for url in urls_to_inspect(conn, client_id, limit):
        if time.time() > stop: break
        r = session.post(INSPECT, json={"inspectionUrl": url, "siteUrl": site_url, "languageCode": "en-US"}, timeout=60)
        if r.status_code == 429: break                               # the day's quota is used up: carry on tomorrow
        if r.status_code == 400: continue                             # a URL outside the property (e.g. old sitemap entry)
        if r.status_code != 200: raise RuntimeError(f"URL Inspection {r.status_code}: {r.text[:200]}")
        row = parse_inspection(r.json())
        conn.execute(UPSERT, [client_id, url] + [row[c] for c in COLS]); done += 1
    return done

def sync(conn, session, client_id, site_url):
    return f"{sync_sitemaps(conn, session, client_id, site_url)} sitemaps, {inspect_urls(conn, session, client_id, site_url)} URLs inspected"

if __name__ == "__main__":
    sample = {"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed",
              "lastCrawlTime": "2026-11-10T04:00:00Z", "googleCanonical": "https://a.shop/x"},
              "richResultsResult": {"verdict": "PASS", "detectedItems": [{"richResultType": "Product snippets"}, {"richResultType": "Breadcrumbs"}]}}}
    p = parse_inspection(sample)
    assert p["verdict"] == "PASS" and p["coverage_state"] == "Submitted and indexed" and p["rich_types"] == "Breadcrumbs, Product snippets"
    assert parse_inspection({})["verdict"] is None and parse_inspection({})["rich_types"] is None
    print("gsc_index: parse ok")
