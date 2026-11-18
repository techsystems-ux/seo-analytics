#!/usr/bin/env python3
"""PageSpeed Insights collector. Free API; PSI_API_KEY is optional but raises the quota.
Reads active client_urls, runs mobile + desktop, stores one row per URL/strategy.
Usage: python pagespeed.py [--client-id UUID] [--limit N]
Env:   DATABASE_URL (aeo_app role), PSI_API_KEY (optional)"""
import argparse, json, os, sys, time, urllib.parse, urllib.request, urllib.error
import psycopg

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

def fetch(url, strategy, key=None, retries=3, timeout=90):
    q = {"url": url, "strategy": strategy, "category": "performance"}
    if key: q["key"] = key
    req = f"{API}?{urllib.parse.urlencode(q)}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 5); continue
            raise
    raise RuntimeError("unreachable")

def parse(resp):
    """Pure function: PSI JSON -> flat row. Missing metrics stay None."""
    lh = resp.get("lighthouseResult", {})
    aud = lh.get("audits", {})
    num = lambda k: (aud.get(k) or {}).get("numericValue")
    score = (lh.get("categories", {}).get("performance") or {}).get("score")
    fx = resp.get("loadingExperience", {}) or {}
    m = fx.get("metrics", {}) or {}
    pct = lambda k: (m.get(k) or {}).get("percentile")
    cls_f = pct("CUMULATIVE_LAYOUT_SHIFT_SCORE")       # CrUX reports CLS x100
    return {
        "perf_score": round(score * 100) if score is not None else None,
        "lcp_ms": num("largest-contentful-paint"), "cls": num("cumulative-layout-shift"),
        "tbt_ms": num("total-blocking-time"), "fcp_ms": num("first-contentful-paint"),
        "ttfb_ms": num("server-response-time"),
        "field_lcp_ms": pct("LARGEST_CONTENTFUL_PAINT_MS"),
        "field_cls": cls_f / 100 if cls_f is not None else None,
        "field_inp_ms": pct("INTERACTION_TO_NEXT_PAINT"),
        "field_category": fx.get("overall_category"),
    }

COLS = ["perf_score","lcp_ms","cls","tbt_ms","fcp_ms","ttfb_ms","field_lcp_ms","field_cls","field_inp_ms","field_category"]

def save(cur, client_id, url, strategy, row=None, error=None):
    row = row or {c: None for c in COLS}
    cur.execute(
        f"INSERT INTO pagespeed_results (client_id,url,strategy,error,{','.join(COLS)}) VALUES (%s,%s,%s,%s,{','.join(['%s']*len(COLS))})",
        [client_id, url, strategy, error] + [row[c] for c in COLS])

def sync_top_pages(conn, client_id, limit=10, cap=60):
    """Core Web Vitals for the brand's top pages by Search Console clicks (90 days), mobile and desktop, each re-run
    after 7 days. ponytail: stops starting new pages after `cap` seconds (a PSI call takes 10-30 s); the rest waits for
    the next run. Returns pages measured."""
    key, stop, done = os.environ.get("PSI_API_KEY"), time.time() + cap, 0
    urls = [r[0] for r in conn.execute("""
        SELECT g.page FROM (SELECT page, sum(clicks) c FROM gsc_search_daily WHERE client_id=%(c)s AND date > current_date - 90
                            GROUP BY page ORDER BY c DESC LIMIT %(n)s) g
        WHERE NOT EXISTS (SELECT 1 FROM pagespeed_results p WHERE p.client_id=%(c)s AND p.url=g.page AND p.error IS NULL
                          AND p.fetched_at > now() - interval '7 days')
        ORDER BY g.c DESC""", {"c": client_id, "n": limit})]
    for url in urls:
        for strategy in ("mobile", "desktop"):
            if time.time() > stop: return done
            try: save(conn, client_id, url, strategy, parse(fetch(url, strategy, key, retries=1, timeout=45)))
            except Exception as e: save(conn, client_id, url, strategy, error=str(e)[:300])
        done += 1
    return done

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id"); ap.add_argument("--limit", type=int)
    a = ap.parse_args(); key = os.environ.get("PSI_API_KEY")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET app.is_staff = 'true'")
        q = "SELECT client_id,url FROM client_urls WHERE active" + (" AND client_id=%s" if a.client_id else "") + " ORDER BY client_id,url" + (f" LIMIT {int(a.limit)}" if a.limit else "")
        urls = conn.execute(q, [a.client_id] if a.client_id else []).fetchall()
        ok = bad = 0
        for client_id, url in urls:
            for strategy in ("mobile", "desktop"):
                try:
                    save(conn, client_id, url, strategy, parse(fetch(url, strategy, key))); ok += 1
                except Exception as e:             # one bad URL must not stop the batch
                    save(conn, client_id, url, strategy, error=str(e)[:300]); bad += 1
                time.sleep(1)
        print(f"done: {ok} ok, {bad} failed, {len(urls)} urls")
        return 1 if bad and not ok else 0

if __name__ == "__main__":
    sys.exit(main())
