#!/usr/bin/env python3
"""Search Console collector (free API). Pulls query x page x country x device per day, plus daily totals.
Re-pulls overlap on purpose: rows are upserted, so late-settling data self-corrects.
Usage: python gsc.py [--client-id UUID] [--days 7] [--lag 3]      (backfill: --days 480)"""
import argparse, sys
from urllib.parse import quote
from common import connect, window
from google_auth import SessionPool

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
ENDPOINT = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
DIMS = ["date", "query", "page", "country", "device"]

def fetch_all(session, site_url, start, end, dims, row_limit=25000):
    url = ENDPOINT.format(site=quote(site_url, safe=""))
    rows, start_row = [], 0
    while True:
        r = session.post(url, json={"startDate": str(start), "endDate": str(end), "dimensions": dims,
                                    "rowLimit": row_limit, "startRow": start_row, "dataState": "final", "type": "web"}, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"GSC {r.status_code}: {r.text[:200]}")
        batch = r.json().get("rows", [])
        rows += batch
        if len(batch) < row_limit:
            return rows
        start_row += row_limit

def brand_terms(name, domain, aliases):
    stem = (domain or "").lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0].split(".")[0]
    terms = {t.lower().strip() for t in [name, stem, *(aliases or [])] if t and len(t.strip()) >= 3}
    return {t.replace(" ", "") for t in terms}          # compare with spaces removed: 'right kind' == 'rightkind'

def is_branded(query, terms):
    q = query.lower().replace(" ", "")
    return any(t in q for t in terms)

def parse_rows(rows, client_id, terms):
    out = []
    for r in rows:
        d, q, p, c, dev = r["keys"]
        out.append((client_id, d, q, p[:2000], c, dev, int(r["clicks"]), int(r["impressions"]),
                    r.get("ctr"), r.get("position"), is_branded(q, terms)))
    return out

def parse_totals(rows, client_id):
    return [(client_id, r["keys"][0], int(r["clicks"]), int(r["impressions"]), r.get("ctr"), r.get("position")) for r in rows]

UPSERT = """INSERT INTO gsc_search_daily (client_id,date,query,page,country,device,clicks,impressions,ctr,position,is_branded)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (client_id,date,query,page,country,device) DO UPDATE SET
  clicks=EXCLUDED.clicks, impressions=EXCLUDED.impressions, ctr=EXCLUDED.ctr, position=EXCLUDED.position, is_branded=EXCLUDED.is_branded"""
UPSERT_TOT = """INSERT INTO gsc_totals_daily (client_id,date,clicks,impressions,ctr,position) VALUES (%s,%s,%s,%s,%s,%s)
ON CONFLICT (client_id,date) DO UPDATE SET clicks=EXCLUDED.clicks, impressions=EXCLUDED.impressions, ctr=EXCLUDED.ctr, position=EXCLUDED.position"""

def sync_client(conn, session, client_id, site_url, name, domain, aliases, days=7, lag=3, row_limit=25000):
    start, end = window(days, lag)
    terms = brand_terms(name, domain, aliases)
    detail = parse_rows(fetch_all(session, site_url, start, end, DIMS, row_limit), client_id, terms)
    totals = parse_totals(fetch_all(session, site_url, start, end, ["date"], row_limit), client_id)
    with conn.cursor() as cur:
        cur.executemany(UPSERT, detail)
        cur.executemany(UPSERT_TOT, totals)
    return len(detail) + len(totals)

def clients_to_sync(conn, client_id=None):
    q = """SELECT c.client_id, i.gsc_site_url, c.name, c.domain, c.aliases, i.google_connection_id FROM clients c
           JOIN client_integrations i USING (client_id) WHERE i.gsc_site_url IS NOT NULL AND c.status='active'"""
    return conn.execute(q + (" AND c.client_id=%s" if client_id else ""), [client_id] if client_id else []).fetchall()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id"); ap.add_argument("--days", type=int, default=7); ap.add_argument("--lag", type=int, default=3)
    a = ap.parse_args()
    with connect() as conn:
        pool, failed = SessionPool(conn, SCOPE), 0
        for cid, site, name, domain, aliases, gconn in clients_to_sync(conn, a.client_id):
            try:
                print(f"{name}: {sync_client(conn, pool.get(gconn), cid, site, name, domain, aliases, a.days, a.lag)} rows")
            except Exception as e:
                failed += 1; print(f"{name}: FAILED {e}", file=sys.stderr)
        return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
