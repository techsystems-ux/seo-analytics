"""Sample rows for the Research, Reddit and YouTube screens (part of sample_semrush.load). Everything is flagged
sample = true. Links point to .example addresses; brand, tone, question and pain-point labels come from the same
code real pulls use, so the screens behave as they will with live data."""
import datetime as dt, json, random, zlib
import community as cm
from ai_visibility import tracked_brands

def _now(days=0): return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

def research(conn, cid, me, comps, kws, pages, rnd):
    comp = comps[0] if comps else "competitor.example"
    feats = ["5,34,16,52,21", "21,5,34", "52,11,21", "5,34", "34,16,5,6", "21,36"]
    def kw_rows(words, base, kd):
        return [{"keyword": w, "volume": rnd.choice(base), "kd": rnd.randint(*kd), "intent": rnd.choice(["commercial", "informational", "commercial,transactional"]),
                 "cpc": round(rnd.uniform(0.05, 0.4), 2), "fk": rnd.choice(feats)} for w in words]
    keyword = {
        "overview": {"keyword": "silver anklets", "volume": 27100, "kd": 38, "intent": "commercial,transactional", "competition": 1, "cpc": 0.15,
                     "results": 12400000, "trend": [0.55, 0.54, 0.6, 0.67, 0.67, 0.81, 0.81, 0.67, 0.81, 1.0, 0.81, 0.67], "fk": "5,34,16,52,21,36"},
        "related": kw_rows(["silver payal", "anklets for women", "silver anklets for women", "payal design", "silver payal design", "oxidised anklets",
                            "black thread anklet", "evil eye anklet", "silver kada", "toe rings silver", "chain anklet", "pearl anklet"], [1300, 2900, 6600, 9900, 14800], (20, 45)),
        "broad": kw_rows(["silver anklets for women", "silver anklets online", "light weight silver anklets", "silver anklets with bells", "silver anklets for baby girl",
                          "92.5 silver anklets", "silver anklets price", "silver anklets for bride", "daily wear silver anklets", "silver anklets set"], [480, 880, 1600, 2400, 3600], (15, 40)),
        "questions": kw_rows(["how to clean silver anklets", "which leg to wear anklet", "can we wear silver anklets daily", "why do women wear anklets",
                              "how to check silver anklet purity", "how to wear anklet with jeans", "are silver anklets good for health"], [170, 320, 590, 880, 1300], (8, 28)),
        "serp": [{"position": i + 1, "domain": d, "url": f"https://{d}/collections/anklets", "fp": rnd.choice(["", "5", "34", "21"])}
                 for i, d in enumerate([comp, "marketplace-one.example", me, "jewellery-blog.example", *(comps[1:3] or []), "fashion-mag.example",
                                        "marketplace-two.example", "silver-guide.example", "video-site.example"][:10])],
    }
    hist = [{"date": (dt.date.today().replace(day=1) - dt.timedelta(days=30 * i)).isoformat(), "rank": 120000 - i * 1500,
             "org_keywords": 3200 - i * 90 + rnd.randint(-60, 60), "org_traffic": 9800 - i * 300 + rnd.randint(-400, 400), "org_cost": 900 - i * 25}
            for i in range(12)]
    kwrows = [{"keyword": k, "position": p or rnd.randint(1, 20), "prev": (p or 10) + rnd.randint(-3, 4), "volume": v or rnd.choice([1000, 2400, 5400]),
               "kd": rnd.randint(10, 50), "traffic_pct": round(rnd.uniform(0.3, 9), 2), "url": u or f"https://{me}/", "fk": rnd.choice(feats)} for k, p, v, u in kws[:25]]
    domain = {
        "overview": {"domain": me, "rank": 118500, "org_keywords": 3240, "org_traffic": 9850, "org_cost": 905, "ad_keywords": 14, "ad_traffic": 220, "ad_cost": 40},
        "history": hist,
        "keywords": kwrows,
        "pages": [{"url": u, "keywords": rnd.randint(4, 240), "traffic": rnd.randint(40, 2400), "traffic_pct": round(rnd.uniform(0.4, 18), 2)} for u in pages[:20]],
        "rising": [dict(r, prev=r["position"] + rnd.randint(2, 9)) for r in kwrows[:8]],
        "falling": [dict(r, prev=max(1, r["position"] - rnd.randint(2, 7))) for r in kwrows[8:15]],
        "competitors": [{"domain": d, "relevance": round(rnd.uniform(0.12, 0.45), 2), "common": rnd.randint(150, 900), "org_keywords": rnd.randint(2000, 40000),
                         "org_traffic": rnd.randint(5000, 120000)} for d in comps + ["marketplace-one.example"]],
    }
    anchors = ["gargi", "gargi by png", "silver jewellery", f"https://{me}", "click here", "silver earrings", "png gargi", "shop now", "oxidised jewellery", "read more"]
    backlinks = {
        "overview": {"ascore": 24, "total": 5200, "domains_num": 310, "urls_num": 4100, "ips_num": 280, "follows_num": 3230, "nofollows_num": 1970,
                     "texts_num": 4400, "images_num": 800},
        "anchors": [{"anchor": a, "domains_num": max(1, 90 - i * 9), "backlinks_num": max(2, 1200 - i * 110), "first_seen": "2023-0%d-1%d" % (1 + i % 9, i % 9)}
                    for i, a in enumerate(anchors)],
        "refdomains": [{"domain": f"{n}.example", "domain_ascore": 62 - i * 4, "backlinks_num": rnd.randint(1, 60), "country": "in",
                        "first_seen": (dt.date.today() - dt.timedelta(days=rnd.randint(30, 900))).isoformat()}
                       for i, n in enumerate(["city-news", "style-weekly", "wedding-guide", "jewellery-journal", "gift-ideas", "fashion-roundup",
                                              "local-directory", "festive-edit", "shopping-list", "trend-notes"])],
        "new": [{"source_title": t, "source_url": f"https://{n}.example/{s}", "target_url": f"https://{me}/collections/{c}", "anchor": a,
                 "page_ascore": rnd.randint(5, 40), "first_seen": (dt.date.today() - dt.timedelta(days=rnd.randint(1, 25))).isoformat()}
                for t, n, s, c, a in [("Festive gifting guide", "gift-ideas", "festive-gifts", "silver-earrings", "silver earrings"),
                                      ("Best anklets this season", "style-weekly", "anklets", "anklets", "gargi"),
                                      ("Bridal jewellery checklist", "wedding-guide", "checklist", "silver-mangalsutra", "silver mangalsutra")]],
        "lost": [{"source_title": "Old store listing", "source_url": "https://local-directory.example/listing", "target_url": f"https://{me}/",
                  "anchor": "gargi", "page_ascore": 12, "last_seen": (dt.date.today() - dt.timedelta(days=12)).isoformat()}],
    }
    paid = {
        "overview": {"domain": comp, "ad_keywords": 840, "ad_traffic": 23000, "ad_cost": 3100},
        "ads": [dict(r, position=rnd.randint(1, 4), traffic_pct=round(rnd.uniform(0.5, 12), 2), url=f"https://{comp}/collections/{r['keyword'].replace(' ', '-')}")
                for r in kw_rows(["silver jewellery", "silver earrings", "silver anklets", "silver rings for women", "silver chain", "925 silver jewellery",
                                  "silver bracelet", "silver pendant", "silver gifts", "silver nose pin"], [6600, 9900, 14800, 22200], (20, 50))],
        "copies": [{"title": t, "description": d, "visible_url": comp, "url": f"https://{comp}/", "keywords": rnd.randint(20, 300)}
                   for t, d in [("Sample ad · 925 silver jewellery", "Sample ad copy: hallmarked silver, free shipping, easy returns."),
                                ("Sample ad · Silver anklets", "Sample ad copy: light, daily-wear anklets in pure silver."),
                                ("Sample ad · Gifts in silver", "Sample ad copy: gift-ready boxes for every occasion.")]],
        "shopping": [{"title": f"Sample product · {p}", "price": f"₹{rnd.choice([799, 1299, 1899, 2499, 3499])}", "keyword": k, "position": rnd.randint(1, 8),
                      "volume": rnd.choice([1600, 4400, 9900]), "shop": comp, "url": f"https://{comp}/products/{p.lower().replace(' ', '-')}"}
                     for p, k in [("Oxidised jhumka", "silver jhumka"), ("Evil eye anklet", "evil eye anklet silver"), ("Plain toe rings", "silver toe rings"),
                                  ("Mangalsutra chain", "silver mangalsutra"), ("Stud earrings", "silver studs")]],
    }
    with conn.cursor() as cur:
        for tool, q, res in [("keyword", "silver anklets", keyword), ("domain", me, domain), ("backlinks", me, backlinks), ("paid", comp, paid)]:
            cur.execute("INSERT INTO research_lookups (client_id, tool, query, result, sample) VALUES (%s,%s,%s,%s,true)", [cid, tool, q, json.dumps(res)])

REDDIT = [  # (subreddit, title, body, kind) ; {me} {c0} {c1} {c2} are replaced with brand names
    ("IndianFashionAddicts", "Where to buy real silver jewellery online in India?", "Looking at {me} and {c0}. Is {me} genuine 92.5 silver? Any reviews?", "post"),
    ("IndianFashionAddicts", None, "Bought earrings from {me} last month, lovely quality and the finish is great. Would recommend.", "comment"),
    ("IndianFashionAddicts", None, "{c0} pieces are pretty but one of my rings tarnished in two weeks, disappointed.", "comment"),
    ("IndianFashionAddicts", "Silver anklets that don't turn black?", "My payal turned black within a month. How do you clean silver anklets at home?", "post"),
    ("jewelry", "How to tell if silver is real 925?", "Is the hallmark enough or should I test it? Got a gift and not sure it's pure silver.", "post"),
    ("jewelry", None, "Tarnish is normal for real silver. A polishing cloth works, keep it in a zip bag.", "comment"),
    ("pune", "Good silver jewellery stores in Pune?", "Need a gift for my sister. Is {me} on FC Road worth it or should I go to {c1}?", "post"),
    ("pune", None, "{me} FC Road store staff were helpful and prices were fair. Good collection of mangalsutras.", "comment"),
    ("pune", None, "{c1} has more designs but making charges felt expensive.", "comment"),
    ("india", "Is buying silver jewellery a good gift for weddings?", "What do people prefer these days, gold plated or silver?", "post"),
    ("TwoXIndia", "Allergic reaction from cheap anklets", "Got a rash and itchy skin from a cheap anklet, does pure silver help with sensitive skin?", "post"),
    ("TwoXIndia", None, "Switched to {c0} 925 silver and no allergy since. Best decision.", "comment"),
    ("weddingsIndia", "Modern mangalsutra designs that aren't heavy?", "Want something short for daily wear. Which brands have good silver mangalsutra?", "post"),
    ("weddingsIndia", None, "{me} has short silver mangalsutras, I loved mine. {c2} is also good.", "comment"),
    ("weddingsIndia", None, "Ordered from {c2}, delivery was late and the clasp broke. Refund took weeks.", "comment"),
    ("IndianFashionAddicts", "{c0} vs {c1} for daily wear?", "Which one has better quality for everyday earrings?", "post"),
    ("IndianFashionAddicts", None, "{c0} for trendy designs, {c1} for value for money.", "comment"),
    ("jewelry", "Does oxidised silver fade?", "How long does the oxidised finish last on jhumkas?", "post"),
    ("pune", None, "Bought a rakhi from {me}, good packaging and on-time delivery.", "comment"),
    ("india", None, "Fake silver everywhere online, always ask for the hallmark certificate.", "comment"),
]
YT = [  # (topic or brand query, title, channel, views, mentions)
    ("silver anklets", "10 silver anklet designs for daily wear | try-on", "Style With Asha (sample)", 182000, "{c0}"),
    ("silver anklets", "How I clean my silver payal at home in 5 minutes", "Home Hacks India (sample)", 640000, ""),
    ("silver anklets", "{me} anklet haul and honest review", "Pune Shopping Diaries (sample)", 23000, "{me}"),
    ("silver anklets", "Budget silver anklets under 1000 | {c1} vs {c0}", "Jewellery Talk (sample)", 98000, "{c1} {c0}"),
    ("silver earrings", "Silver jhumka collection 2026", "Style With Asha (sample)", 210000, "{c0}"),
    ("silver earrings", "Real vs fake silver: 3 tests at home", "Jewellery Talk (sample)", 450000, ""),
    ("silver earrings", "{me} FC Road store tour", "{me} (sample)", 12000, "{me}"),
    ("silver mangalsutra", "Modern short mangalsutra designs | {c2}", "Bride Notes (sample)", 76000, "{c2}"),
    ("silver mangalsutra", "Silver mangalsutra for daily wear ft. {me}", "Bride Notes (sample)", 41000, "{me}"),
    ("silver mangalsutra", "Mangalsutra buying guide", "Wedding Planner Desk (sample)", 133000, ""),
    ("Gargi", "{me} new collection unboxing", "Pune Shopping Diaries (sample)", 18000, "{me}"),
    ("Gargi", "Diwali gifting in silver | {me}", "{me} (sample)", 9000, "{me}"),
]
YT_COMMENTS = ["Does this turn black after a few weeks?", "Which store in Pune has these?", "Is this pure 92.5 silver?", "Love this, ordered one!",
               "The clasp broke on mine after a month", "How do you clean oxidised silver?", "Price please?", "Is it good for sensitive skin? I get a rash from cheap ones",
               "Delivery took two weeks, but quality is good", "Can men wear these anklets?"]

def reddit(conn, cid, labels, rnd, today):
    brands = tracked_brands(conn, cid)
    me, cs = labels[0], (labels[1:] + ["Competitor A", "Competitor B", "Competitor C"])[:3]
    rows = []
    for i, (sub, title, body, kind) in enumerate(REDDIT):
        f = lambda t: t.format(me=me, c0=cs[0], c1=cs[1], c2=cs[2]) if t else t
        title, body = f(title), f(body)
        text = f"{title or ''}\n{body or ''}"
        found_by = "cited" if i in (0, 4, 12) else "brand" if me in text else "competitor" if any(c in text for c in cs) else "topic"
        rows.append((cid, f"s{i:03d}", kind, sub, title, body, f"https://reddit.example/r/{sub}/comments/s{i // 3:03d}/sample/", f"user_{rnd.randint(100, 999)}",
                     rnd.randint(2, 480), rnd.randint(0, 90) if kind == "post" else None, _now(rnd.randint(5, 300)), found_by,
                     None, cm.brands_in(text, brands), cm.tone(text), cm.is_question(title, body), cm.pain_point(text),
                     rnd.choice(["silver anklets", "silver earrings", "silver mangalsutra"]) if cm.is_question(title, body) else None, today))
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO reddit_items (client_id, item_id, kind, subreddit, title, body, url, author, score, comments, created_at, found_by, query,
                             brands, sentiment, is_question, pain_point, topic, pulled_on, sample)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""", rows)

def youtube(conn, cid, labels, rnd, today):
    brands = tracked_brands(conn, cid)
    me, cs = labels[0], (labels[1:] + ["Competitor A", "Competitor B", "Competitor C"])[:3]
    chans = {}
    with conn.cursor() as cur:
        rank = {}
        for i, (q, title, ch, views, ment) in enumerate(YT):
            f = lambda t: t.format(me=me, c0=cs[0], c1=cs[1], c2=cs[2])
            title, ch, ment = f(title), f(ch), f(ment)
            vid = f"sample{i:05d}"[:11]
            chid = "UCsample" + str(zlib.crc32(ch.encode()) % 10**6)
            chans.setdefault(chid, ch)
            rank[q] = rank.get(q, 0) + 1
            found_by = "brand" if q == "Gargi" else "topic"
            cur.execute("""INSERT INTO youtube_videos (client_id, video_id, title, description, channel_id, channel, published_at, views, likes, comments, duration_s,
                             found_by, query, search_rank, brands, own_channel, pulled_on, sample)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""",
                        [cid, vid, title, f"Sample video. Featuring {ment}." if ment else "Sample video.", chid, ch, _now(rnd.randint(10, 500)), views,
                         views // 30, views // 400, rnd.randint(120, 900), found_by, q if q != "Gargi" else me, rank[q] * 2 - 1,
                         cm.brands_in(f"{title} {ment}", brands), ch.startswith(me), today])
        for chid, ch in chans.items():
            cur.execute("""INSERT INTO youtube_channels (client_id, channel_id, title, subscribers, videos, views, pulled_on, sample)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""",
                        [cid, chid, ch, rnd.choice([4200, 18000, 56000, 210000, 640000]), rnd.randint(40, 600), rnd.randint(10**6, 9 * 10**7), today])
        for i, t in enumerate(YT_COMMENTS * 2):
            cur.execute("""INSERT INTO youtube_comments (client_id, comment_id, video_id, text, likes, published_at, is_question, pain_point, pulled_on, sample)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING""",
                        [cid, f"sc{i:04d}", f"sample{i % len(YT):05d}"[:11], t, rnd.randint(0, 300), _now(rnd.randint(1, 200)), cm.is_question(t), cm.pain_point(t), today])
        # one cited video, marked the way real pulls mark it
        cur.execute("UPDATE youtube_videos SET found_by='cited' WHERE client_id=%s AND video_id=%s AND sample", [cid, "sample00005"[:11]])

def load(conn, cid, me_domain, comps, today=None):
    today = today or dt.date.today()
    rnd = random.Random(7)
    brands = tracked_brands(conn, cid)
    labels = [cm.display_name(b) for b in brands]
    kws = [tuple(r) for r in conn.execute("SELECT keyword, position, search_volume, url FROM semrush_keywords WHERE client_id=%s ORDER BY traffic_pct DESC NULLS LAST", [cid])]
    seen = set(); kws = [k for k in kws if not (k[0] in seen or seen.add(k[0]))]            # one row per keyword
    kws = kws or [(k, None, None, None) for k in ("silver earrings", "silver anklets", "silver mangalsutra", "silver chain for women", "oxidised jhumka")]
    pages = [r[0] for r in conn.execute("SELECT url FROM pages WHERE client_id=%s ORDER BY page_type, url LIMIT 20", [cid])] or [f"https://{me_domain}/"]
    research(conn, cid, me_domain, comps, kws, pages, rnd)
    reddit(conn, cid, labels, rnd, today)
    youtube(conn, cid, labels, rnd, today)

def clear(conn, cid):
    for t in ("research_lookups", "reddit_items", "youtube_videos", "youtube_channels", "youtube_comments"):
        conn.execute(f"DELETE FROM {t} WHERE client_id=%s AND sample", [cid])
