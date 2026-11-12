"""Run the SOP page checks for a brand and store the results.

  python run_checks.py --client "Easeliving"            fetch every page of the brand, check it, store results
  python run_checks.py --client "Easeliving" --url URL  one page only
  python run_checks.py --client "Easeliving" --import pages.csv   add or update pages first
      CSV columns: url, page_type, primary_keyword, secondary_keywords (separated by ;), search_volume, intent

Drafts typed in the tool go through check_draft(), so a draft and the live page are judged by the same rules.
"""
import argparse, csv, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "collectors"))
import requests
from page_parser import parse_html, draft_content
from checks import run_page_checks, run_site_checks, score

UA = "HoversSiteCheck/1.0 (+https://hovers.in)"
BRAND_COLS = ["brand_spellings", "product_names", "term_fixes", "usps", "usp_markers", "banned_words", "title_banned_words",
              "claims_to_avoid", "region_words", "online_only", "correct_domain", "wrong_domains", "sister_domains",
              "theme_appends_brand", "tone", "reviewer", "review_days"]

def find_client(conn, name_or_id):
    row = conn.execute("SELECT client_id, name FROM clients WHERE client_id::text=%s OR lower(name)=lower(%s)", [name_or_id, name_or_id]).fetchone()
    if not row: sys.exit(f"No client called {name_or_id}")
    return row

def load_brand(conn, client_id):
    row = conn.execute(f"SELECT {', '.join(BRAND_COLS)} FROM brand_rules WHERE client_id=%s", [client_id]).fetchone()
    return dict(zip(BRAND_COLS, row)) if row else {}

def save_brand(conn, client_id, rules):
    cols = [c for c in BRAND_COLS if c in rules]
    vals = [json.dumps(rules[c]) if c == "term_fixes" else rules[c] for c in cols]
    conn.execute(f"""INSERT INTO brand_rules (client_id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))})
                     ON CONFLICT (client_id) DO UPDATE SET {', '.join(f'{c}=EXCLUDED.{c}' for c in cols)}, updated_at=now()""",
                 [client_id] + vals)

def import_pages(conn, client_id, rows):
    n = 0
    for r in rows:
        conn.execute("""INSERT INTO pages (client_id, url, page_type, primary_keyword, secondary_keywords, search_volume, intent)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (client_id, url) DO UPDATE SET page_type=EXCLUDED.page_type, primary_keyword=EXCLUDED.primary_keyword,
                          secondary_keywords=EXCLUDED.secondary_keywords, search_volume=EXCLUDED.search_volume, intent=EXCLUDED.intent""",
                     [client_id, r["url"].strip(), r["page_type"].strip(), (r.get("primary_keyword") or "").strip() or None,
                      [s.strip() for s in (r.get("secondary_keywords") or "").split(";") if s.strip()],
                      int(float(r["search_volume"])) if (r.get("search_volume") or "").strip() else None, (r.get("intent") or "").strip() or None])
        n += 1
    return n

def fetch(url, session=None):
    s = session or requests
    resp = s.get(url, headers={"User-Agent": UA}, timeout=30, allow_redirects=True)
    return resp.status_code, resp.text

def store(conn, client_id, page_id, source, status, content, results):
    sid = conn.execute("""INSERT INTO page_snapshots (client_id, page_id, source, http_status, content)
                          VALUES (%s,%s,%s,%s,%s) RETURNING snapshot_id""",
                       [client_id, page_id, source, status, json.dumps(content)]).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO page_checks (snapshot_id, client_id, page_id, check_id, result, found, message)
                           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (snapshot_id, check_id) DO UPDATE
                           SET result=EXCLUDED.result, found=EXCLUDED.found, message=EXCLUDED.message""",
                        [(sid, client_id, page_id, r["check_id"], r["result"], r["found"], r["message"]) for r in results])
    return sid

def site_pass(conn, client_id):
    """Rules that need every page at once. Results are added to each page's latest snapshot."""
    rows = conn.execute("""SELECT p.page_id, p.url, p.primary_keyword, s.snapshot_id, s.content
                           FROM pages p LEFT JOIN LATERAL (SELECT snapshot_id, content FROM page_snapshots
                             WHERE page_id=p.page_id ORDER BY taken_at DESC LIMIT 1) s ON true
                           WHERE p.client_id=%s""", [client_id]).fetchall()
    pages = [{"page_id": r[0], "url": r[1], "primary_keyword": r[2], "snapshot_id": r[3], "content": r[4]} for r in rows]
    res = run_site_checks(pages)
    with conn.cursor() as cur:
        for p in pages:
            if p["snapshot_id"] is None: continue
            cur.executemany("""INSERT INTO page_checks (snapshot_id, client_id, page_id, check_id, result, found, message)
                               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (snapshot_id, check_id) DO UPDATE
                               SET result=EXCLUDED.result, found=EXCLUDED.found, message=EXCLUDED.message""",
                            [(p["snapshot_id"], client_id, p["page_id"], r["check_id"], r["result"], r["found"], r["message"]) for r in res[p["page_id"]]])
    return res

def check_live(conn, client_id, only_url=None, session=None, pause=1.0, limit=None):
    """Checks one URL, or the brand's pages in priority order (home, collections, then by Search Console clicks), up to `limit`."""
    brand = load_brand(conn, client_id)
    q = "SELECT page_id, url, page_type, primary_keyword, secondary_keywords FROM pages WHERE client_id=%s"
    if only_url:
        rows = conn.execute(q + " AND url=%s", [client_id, only_url]).fetchall()
    else:
        import sitemap
        order = sitemap.priority(conn, client_id)[:limit] if limit else sitemap.priority(conn, client_id)
        by_id = {r[0]: r for r in conn.execute(q, [client_id]).fetchall()}
        rows = [by_id[i] for i in order if i in by_id]
    summary = []
    for page_id, url, ptype, kw, sec in rows:
        page = {"page_type": ptype, "primary_keyword": kw, "secondary_keywords": sec}
        try:
            status, html = fetch(url, session)
        except Exception as e:                      # unreachable page: record it, keep going
            summary.append((url, None, f"could not fetch: {e}")); continue
        if status >= 400:
            summary.append((url, None, f"HTTP {status}")); continue
        content = parse_html(url, html)
        results = run_page_checks(content, page, brand)
        store(conn, client_id, page_id, "live", status, content, results)
        summary.append((url, score(results), f"{sum(r['result'] == 'fail' for r in results)} to fix"))
        time.sleep(pause)
    site_pass(conn, client_id)
    return summary

def check_draft(conn, client_id, page_id, **fields):
    brand = load_brand(conn, client_id)
    url, ptype, kw, sec = conn.execute("SELECT url, page_type, primary_keyword, secondary_keywords FROM pages WHERE page_id=%s", [page_id]).fetchone()
    content = draft_content(url, **fields)
    results = run_page_checks(content, {"page_type": ptype, "primary_keyword": kw, "secondary_keywords": sec}, brand)
    store(conn, client_id, page_id, "draft", None, content, results)
    return results

if __name__ == "__main__":
    from common import connect
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True); ap.add_argument("--url"); ap.add_argument("--import", dest="csv")
    a = ap.parse_args()
    with connect() as conn:
        cid, name = find_client(conn, a.client)
        if a.csv:
            with open(a.csv, newline="", encoding="utf-8-sig") as f: print(f"Imported {import_pages(conn, cid, csv.DictReader(f))} pages")
        for url, sc, note in check_live(conn, cid, a.url):
            print(f"{'—' if sc is None else sc:>4}  {url}  ({note})")
