#!/usr/bin/env python3
"""PageSpeed Insights collector. Free API; PSI_API_KEY is optional but raises the quota.
Reads active client_urls, runs mobile + desktop, stores one row per URL/strategy.
Usage: python pagespeed.py [--client-id UUID] [--limit N]
Env:   DATABASE_URL (aeo_app role), PSI_API_KEY (optional)"""
import argparse, json, os, sys, time, urllib.parse, urllib.request, urllib.error
import psycopg

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

def fetch(url, strategy, key=None, retries=3):
    q = {"url": url, "strategy": strategy, "category": "performance"}
    if key: q["key"] = key
    req = f"{API}?{urllib.parse.urlencode(q)}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
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
