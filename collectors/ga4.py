#!/usr/bin/env python3
"""GA4 Data API collector (free). Two reports per client:
  1) daily sessions / engagement / key events / purchases / revenue by channel group
  2) daily sessions from AI-engine sources (chatgpt, perplexity, gemini, copilot, claude ...)
Usage: python ga4.py [--client-id UUID] [--days 7] [--lag 1]"""
import argparse, datetime as dt, sys
from common import connect, window
from google_auth import SessionPool

SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
ENDPOINT = "https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
METRICS = ["sessions", "engagedSessions", "keyEvents", "transactions", "purchaseRevenue", "newUsers"]
AI_REGEX = r"chatgpt|openai|perplexity|gemini|copilot|claude|deepseek|meta\.ai|you\.com"

def run_report(session, property_id, start, end, dimension, dim_filter=None, limit=10000):
    url, rows, offset = ENDPOINT.format(pid=property_id), [], 0
    while True:
        body = {"dateRanges": [{"startDate": str(start), "endDate": str(end)}],
                "dimensions": [{"name": "date"}, {"name": dimension}], "metrics": [{"name": m} for m in METRICS],
                "limit": limit, "offset": offset}
        if dim_filter: body["dimensionFilter"] = dim_filter
        r = session.post(url, json=body, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"GA4 {r.status_code}: {r.text[:200]}")
        j = r.json(); batch = j.get("rows", []); rows += batch
        if len(rows) >= int(j.get("rowCount", len(rows))) or not batch:
            return rows
        offset += limit

def parse(rows, client_id):
    out = []
    for r in rows:
        d, key = (v["value"] for v in r["dimensionValues"])
        m = [float(v["value"]) for v in r["metricValues"]]
        out.append((client_id, dt.datetime.strptime(d, "%Y%m%d").date(), key, int(m[0]), int(m[1]), m[2], int(m[3]), m[4],
                    int(m[5]) if len(m) > 5 else None))
    return out

SQL = """INSERT INTO {t} (client_id,date,{k},sessions,engaged_sessions,key_events,transactions,revenue,new_users) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (client_id,date,{k}) DO UPDATE SET sessions=EXCLUDED.sessions, engaged_sessions=EXCLUDED.engaged_sessions,
  key_events=EXCLUDED.key_events, transactions=EXCLUDED.transactions, revenue=EXCLUDED.revenue, new_users=EXCLUDED.new_users"""

AI_FILTER = {"filter": {"fieldName": "sessionSource",
                        "stringFilter": {"matchType": "PARTIAL_REGEXP", "value": AI_REGEX, "caseSensitive": False}}}

ORGANIC_FILTER = {"filter": {"fieldName": "sessionDefaultChannelGroup",
                             "stringFilter": {"matchType": "EXACT", "value": "Organic Search"}}}

def sync_client(conn, session, client_id, property_id, days=7, lag=1):
    start, end = window(days, lag)
    main_rows = parse(run_report(session, property_id, start, end, "sessionDefaultChannelGroup"), client_id)
    ai_rows = parse(run_report(session, property_id, start, end, "sessionSource", AI_FILTER), client_id)
    landing = parse(run_report(session, property_id, start, end, "landingPagePlusQueryString", ORGANIC_FILTER), client_id)
    items = sync_items(conn, session, client_id, property_id, start, end)
    with conn.cursor() as cur:
        cur.executemany(SQL.format(t="ga4_daily", k="channel_group"), main_rows)
        cur.executemany(SQL.format(t="ga4_ai_referrals", k="source"), ai_rows)
        cur.executemany(SQL.format(t="ga4_organic_landing", k="landing_page"), [r[:2] + (r[2][:2000],) + r[3:] for r in landing])
    return len(main_rows) + len(ai_rows) + len(landing) + items

def sync_items(conn, session, client_id, property_id, start, end):
    """Products bought in sessions from Google organic search, one row per product per calendar month in the window.
    GA4 may refuse this mix of item and session fields on some properties; the monthly report then lists landing pages instead."""
    months, d = [], start.replace(day=1)
    while d <= end:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        months.append((d, max(d, start), min(nxt - dt.timedelta(days=1), end))); d = nxt
    n = 0
    for month, a, b in months[-14:]:
        body = {"dateRanges": [{"startDate": str(a), "endDate": str(b)}], "dimensions": [{"name": "itemName"}],
                "metrics": [{"name": "itemsPurchased"}, {"name": "itemRevenue"}], "dimensionFilter": ORGANIC_FILTER,
                "orderBys": [{"metric": {"metricName": "itemRevenue"}, "desc": True}], "limit": 50}
        try:
            r = session.post(ENDPOINT.format(pid=property_id), json=body, timeout=120)
            if r.status_code != 200: return n
            rows = r.json().get("rows", [])
        except Exception:
            return n
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO ga4_organic_items (client_id, month, item_name, items_purchased, item_revenue) VALUES (%s,%s,%s,%s,%s)
                               ON CONFLICT (client_id, month, item_name) DO UPDATE SET items_purchased=EXCLUDED.items_purchased,
                               item_revenue=EXCLUDED.item_revenue""",
                            [(client_id, month, x["dimensionValues"][0]["value"][:300], int(float(x["metricValues"][0]["value"])),
                              float(x["metricValues"][1]["value"])) for x in rows if x["dimensionValues"][0]["value"] not in ("(not set)", "")])
        n += len(rows)
    return n

def clients_to_sync(conn, client_id=None):
    q = "SELECT c.client_id, i.ga4_property_id, c.name, i.google_connection_id FROM clients c JOIN client_integrations i USING (client_id) WHERE i.ga4_property_id IS NOT NULL AND c.status='active'"
    return conn.execute(q + (" AND c.client_id=%s" if client_id else ""), [client_id] if client_id else []).fetchall()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id"); ap.add_argument("--days", type=int, default=7); ap.add_argument("--lag", type=int, default=1)
    a = ap.parse_args()
    with connect() as conn:
        pool, failed = SessionPool(conn, SCOPE), 0
        for cid, pid, name, gconn in clients_to_sync(conn, a.client_id):
            try:
                print(f"{name}: {sync_client(conn, pool.get(gconn), cid, pid, a.days, a.lag)} rows")
            except Exception as e:
                failed += 1; print(f"{name}: FAILED {e}", file=sys.stderr)
        return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
