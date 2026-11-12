#!/usr/bin/env python3
"""Sample data for the Semrush screens, so the layout can be reviewed before any credits are spent.

Nothing here calls Semrush and nothing is written to semrush_usage. Every row is flagged (sample = true), the brand
shows a "Sample data" banner while it is loaded, and clear() removes all of it. Site names in backlinks end in
.example (a reserved, made-up domain) so a sample can never be mistaken for a real link.

  python sample_semrush.py load  --client "Gargi"
  python sample_semrush.py clear --client "Gargi"
"""
import argparse, datetime as dt, json, os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "collectors"), os.path.join(HERE, "..", "seo")]

IDEAS = {
    "silver earrings": (["how to clean silver earrings at home", "are silver earrings good for sensitive ears", "how to tell if silver earrings are real",
                         "can you wear silver earrings in the shower", "which silver earrings are best for daily wear"],
                        ["silver jhumka earrings", "oxidised silver earrings", "silver stud earrings for women", "92.5 silver earrings", "silver hoop earrings"]),
    "silver anklets": (["how to wear anklets with saree", "is it good to wear silver anklets", "which leg should you wear an anklet on",
                        "how to stop silver anklets from turning black"],
                       ["silver payal for women", "evil eye anklet silver", "silver anklets with ghungroo", "light weight silver anklets"]),
    "silver mangalsutra": (["can you wear a silver mangalsutra", "how long should a mangalsutra be", "what is a modern mangalsutra"],
                           ["silver mangalsutra for daily wear", "short mangalsutra designs", "black bead silver chain"]),
}
SERP_SETS = ["52,21,5", "21,5,34", "5,34,16", "52,11,21", "5,6", "34,16,5", "21,36", "52,5,9", "11,21", "5"]
ISSUES = [(4, "Broken internal links", 38), (8, "Pages returned 4XX status code", 12), (21, "Duplicate title tags", 64),
          (106, "Duplicate meta descriptions", 140), (113, "Pages with low text-to-HTML ratio", 310), (124, "Images without alt attributes", 220),
          (205, "Orphaned pages in sitemaps", 55), (211, "Pages with only one internal link", 90)]

def load(conn, cid, today=None):
    import rank
    today = today or dt.date.today()
    rnd = random.Random(42)
    clear(conn, cid)
    me = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [cid]).fetchone()[0].replace("www.", "").strip("/")
    comps = [r[0] for r in conn.execute("""SELECT domain FROM competitors WHERE client_id=%s AND tracked AND NOT own_brand AND domain IS NOT NULL
                                           ORDER BY common_keywords DESC NULLS LAST LIMIT 4""", [cid])] or ["competitor-one.example", "competitor-two.example"]
    with conn.cursor() as cur:
        # content ideas
        rows = []
        for seed, (qs, rel) in IDEAS.items():
            for i, k in enumerate(qs):
                rows.append((cid, seed, "question", k, rnd.choice([170, 260, 390, 480, 720, 1300]), rnd.randint(8, 30), "informational", SERP_SETS[i % 4 + 0]))
            for i, k in enumerate(rel):
                rows.append((cid, seed, "related", k, rnd.choice([1000, 1900, 2900, 4400, 6600, 9900]), rnd.randint(25, 55), "commercial", SERP_SETS[(i + 4) % 10]))
        cur.executemany("""INSERT INTO keyword_ideas (client_id, seed, kind, keyword, search_volume, kd, intent, serp_features, pulled_on, sample)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""", [r + (today,) for r in rows])
        # SERP features on the keywords already pulled (only where none are stored)
        for i, (kw,) in enumerate(conn.execute("SELECT keyword FROM semrush_keywords WHERE client_id=%s AND serp_features IS NULL", [cid]).fetchall()):
            fk = SERP_SETS[i % len(SERP_SETS)]
            cur.execute("""UPDATE semrush_keywords SET serp_features=%s, serp_features_owned=%s, sample_features=true
                           WHERE client_id=%s AND keyword=%s AND serp_features IS NULL""", [fk, fk.split(",")[-1] if i % 3 == 0 else None, cid, kw])
        # keyword gap
        gap_kws = ["silver chain for men", "silver toe rings", "silver kada for men", "silver nose pin", "silver pendant for women",
                   "silver bracelet for men", "oxidised necklace set", "silver rings for women", "silver coin 10 gram", "silver baby bangles"]
        cur.executemany("""INSERT INTO keyword_gaps (client_id, database, pulled_on, keyword, search_volume, cpc, competition, positions, sample)
                           VALUES (%s,'in',%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""",
                        [(cid, today, k, rnd.choice([2400, 3600, 5400, 8100, 12100, 14800]), round(rnd.uniform(0.1, 0.6), 2), round(rnd.uniform(0.3, 1), 2),
                          json.dumps({**{c: rnd.randint(1, 9) if j == 0 else rnd.choice([None, rnd.randint(1, 30)]) for j, c in enumerate(comps)},
                                      me: None})) for k in gap_kws])
        # backlinks
        ov = [(me, 24, 5200, 310)] + [(c, rnd.randint(18, 42), rnd.randint(3000, 60000), rnd.randint(200, 2200)) for c in comps]
        cur.executemany("""INSERT INTO backlink_overview (client_id, domain, pulled_on, ascore, backlinks, refdomains, follows, nofollows, sample)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""",
                        [(cid, d, today, a, b, r, int(b * f), b - int(b * f)) for (d, a, b, r), f in zip(ov, [0.62, 0.71, 0.55, 0.78, 0.66])])
        names = ["jewellery-journal", "puneweddings", "style-notes", "silver-guide", "bridal-diaries", "gift-ideas-india", "festive-edit",
                 "fashion-weekly", "maharashtra-brides", "trend-tracker", "daily-style", "local-listings"]
        cur.executemany("""INSERT INTO backlink_refdomains (client_id, pulled_on, domain, ascore, backlinks, first_seen, last_seen, sample)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""",
                        [(cid, today, f"{n}.example", 60 - i * 4, rnd.randint(1, 40), today - dt.timedelta(days=rnd.randint(40, 900)), today) for i, n in enumerate(names)])
        prospects = ["vogue-style", "bride-guide", "festive-gifting", "pune-shopping", "jewellery-reviews", "wedding-planner-hub",
                     "silver-care-blog", "fashion-roundup", "gift-finder", "ethnic-wear-edit"]
        cur.executemany("""INSERT INTO backlink_gap (client_id, pulled_on, domain, ascore, links_to, sample) VALUES (%s,%s,%s,%s,%s,true)
                           ON CONFLICT DO NOTHING""",
                        [(cid, today, f"{n}.example", 58 - i * 4, json.dumps({c: rnd.randint(1, 12) for c in rnd.sample(comps, k=min(len(comps), rnd.randint(1, 3)))}))
                         for i, n in enumerate(prospects)])
        # Semrush Site Audit
        crawl = cur.execute("""INSERT INTO tech_crawls (client_id, source, finished_at, pages_crawled, health_score, errors, warnings, notices, sample)
                               VALUES (%s,'semrush',now(),1104,82,50,514,145,true) RETURNING crawl_id""", [cid]).fetchone()[0]
        urls = [r[0] for r in conn.execute("SELECT url FROM pages WHERE client_id=%s ORDER BY url LIMIT 40", [cid])] or [f"https://{me}/"]
        for iid, title, n in ISSUES:
            sev = "error" if iid < 100 else "warning" if iid < 200 else "notice"
            cur.execute("""INSERT INTO tech_issues (client_id, issue_code, source, title, severity, team, how_to_fix, pages, due_date, sample)
                           VALUES (%s,%s,'semrush',%s,%s,'tech','See the issue in Semrush Site Audit for the fix.',%s,%s,true) ON CONFLICT DO NOTHING""",
                        [cid, f"semrush:{iid}", title, sev, n, today + dt.timedelta(days={"error": 7, "warning": 14, "notice": 30}[sev])])
            if sev == "error":
                cur.executemany("INSERT INTO tech_issue_pages (crawl_id, client_id, issue_code, url, detail) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                [(crawl, cid, f"semrush:{iid}", u, "sample") for u in rnd.sample(urls, k=min(6, len(urls)))])
    # rankings: the Search Console part is real and free; Semrush tracked positions are sample
    rank.sync_client(conn, cid)
    tracked = conn.execute("SELECT keyword FROM rank_keywords WHERE client_id=%s ORDER BY keyword LIMIT 10", [cid]).fetchall()
    if not tracked:
        kws = [k for k in ("silver earrings", "silver anklets", "silver mangalsutra", "silver chain for women", "evil eye anklet silver")]
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO rank_keywords (client_id, keyword, tags, sample) VALUES (%s,%s,'{sample}',true) ON CONFLICT DO NOTHING", [(cid, k) for k in kws])
        tracked = [(k,) for k in kws]
    with conn.cursor() as cur:
        for (kw,) in tracked:
            base = rnd.randint(3, 25)
            cur.executemany("""INSERT INTO rank_positions (client_id, keyword, date, source, position, sample) VALUES (%s,%s,%s,'semrush',%s,true)
                               ON CONFLICT DO NOTHING""",
                            [(cid, kw, today - dt.timedelta(days=d), max(1, base + rnd.randint(-2, 2))) for d in range(14)])
    import sample_community
    sample_community.load(conn, cid, me, [c for c in comps if not c.endswith(".example")], today)
    conn.execute("UPDATE clients SET sample_semrush=true WHERE client_id=%s", [cid])
    return "sample data loaded"

SOURCES = {"semrush": ("keyword_ideas", "keyword_gaps", "backlink_overview", "backlink_refdomains", "backlink_gap", "tech_crawls", "tech_issues",
                        "rank_positions", "rank_keywords", "research_lookups"),
           "reddit": ("reddit_items", "reddit_communities"), "youtube": ("youtube_videos", "youtube_channels", "youtube_comments")}

def clear(conn, cid, only=None):
    """Removes sample rows: all of them, or only one source's (a real Reddit pull leaves the YouTube sample in place)."""
    for src, tables in SOURCES.items():
        if only and src != only: continue
        for t in tables:
            conn.execute(f"DELETE FROM {t} WHERE client_id=%s AND sample", [cid])
        if src == "semrush":
            conn.execute("""UPDATE semrush_keywords SET serp_features=NULL, serp_features_owned=NULL, sample_features=false
                            WHERE client_id=%s AND sample_features""", [cid])
    left = any(conn.execute(f"SELECT 1 FROM {t} WHERE client_id=%s AND sample LIMIT 1", [cid]).fetchone() for ts in SOURCES.values() for t in ts)
    conn.execute("UPDATE clients SET sample_semrush=%s WHERE client_id=%s", [left, cid])
    return "sample data removed"

if __name__ == "__main__":
    from common import connect
    ap = argparse.ArgumentParser(); ap.add_argument("what", choices=["load", "clear"]); ap.add_argument("--client", required=True)
    a = ap.parse_args()
    with connect() as conn:
        cid = conn.execute("SELECT client_id FROM clients WHERE client_id::text=%s OR lower(name)=lower(%s)", [a.client, a.client]).fetchone()[0]
        print(load(conn, cid) if a.what == "load" else clear(conn, cid))
