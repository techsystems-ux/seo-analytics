#!/usr/bin/env python3
"""Daily rankings from Search Console (free). For each tracked keyword: the impression-weighted average position
across pages, devices and India/other countries that day, its clicks and impressions, and the page that got most clicks.

When a brand has no tracked keywords yet, it starts with its collection pages' keywords and its top non-branded
searches (tagged 'auto'); people add or remove keywords on the Rankings screen.
"""
import datetime as dt

def seed_keywords(conn, client_id, n=40):
    if conn.execute("SELECT 1 FROM rank_keywords WHERE client_id=%s LIMIT 1", [client_id]).fetchone(): return 0
    kws = conn.execute("""SELECT primary_keyword, url FROM pages WHERE client_id=%s AND page_type IN ('collection','home')
                          AND primary_keyword IS NOT NULL ORDER BY search_volume DESC NULLS LAST LIMIT %s""", [client_id, n // 2]).fetchall()
    have = {k.lower() for k, _ in kws}
    for (q,) in conn.execute("""SELECT query FROM gsc_search_daily WHERE client_id=%s AND NOT is_branded AND date >= current_date - 90
                                GROUP BY query ORDER BY sum(clicks) DESC, sum(impressions) DESC LIMIT %s""", [client_id, n]):
        if len(kws) >= n: break
        if q.lower() not in have: kws.append((q, None)); have.add(q.lower())
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO rank_keywords (client_id, keyword, target_url, tags) VALUES (%s,%s,%s,'{auto}') ON CONFLICT DO NOTHING",
                        [(client_id, k, u) for k, u in kws])
    return len(kws)

def sync_client(conn, client_id, days=None):
    """Fills rank_positions (source 'gsc') from data already pulled into gsc_search_daily: no extra Google calls."""
    seeded = seed_keywords(conn, client_id)
    if days is None:
        has = conn.execute("SELECT 1 FROM rank_positions WHERE client_id=%s AND source='gsc' LIMIT 1", [client_id]).fetchone()
        days = 10 if has else 90
    n = conn.execute("""
        INSERT INTO rank_positions (client_id, keyword, date, source, position, url, clicks, impressions)
        SELECT g.client_id, k.keyword, g.date, 'gsc',
               round(sum(g.position * g.impressions) / nullif(sum(g.impressions), 0), 1),
               (array_agg(g.page ORDER BY g.clicks DESC, g.impressions DESC))[1], sum(g.clicks), sum(g.impressions)
        FROM rank_keywords k JOIN gsc_search_daily g ON g.client_id = k.client_id AND lower(g.query) = lower(k.keyword)
        WHERE k.client_id = %s AND g.date >= current_date - %s
        GROUP BY g.client_id, k.keyword, g.date
        ON CONFLICT (client_id, keyword, date, source) DO UPDATE SET position=EXCLUDED.position, url=EXCLUDED.url,
          clicks=EXCLUDED.clicks, impressions=EXCLUDED.impressions""", [client_id, days]).rowcount
    return f"{n} daily positions" + (f", started tracking {seeded} keywords" if seeded else "")
