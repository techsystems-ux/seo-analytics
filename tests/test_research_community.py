"""Semrush research lookups, Reddit through Apify and YouTube through the Data API, all with fakes (no credits, no
quota). Checks cost guards, stored results, brand matching (PNG Brothers is not the client), tone, questions, pain
points, cited threads and videos, and the Apify and YouTube limits. Needs DATABASE_URL (aeo_app) on a migrated database."""
import os, sys, json, datetime as dt
os.environ["SEMRUSH_TOTAL_CAP"] = "10000000"; os.environ["APIFY_TOTAL_ITEMS"] = "60"; os.environ["APIFY_MAX_ITEMS"] = "40"
os.environ["YOUTUBE_DAILY_QUOTA"] = "1000"
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "scripts")]
from common import connect
import semrush, semrush_research as sr, reddit, youtube, community as cm

class R:
    def __init__(s, code, text="", js=None): s.status_code, s.text, s._js = code, (json.dumps(js) if js is not None else text), js
    def json(s): return s._js

# ---------------------------------------------------------------- Semrush research
class FakeSemrush:
    def __init__(s): s.urls = []
    def get(s, url, timeout=None):
        s.urls.append(url)
        if "type=phrase_this" in url: return R(200, "Keyword;Search Volume;CPC;Competition;Number of Results;Trends;Intent;Keyword Difficulty;SERP Features\n"
                                                    "silver anklets;27100;0.15;1;12400000;0.55,0.67,1.00;0,3;38;5,34,52\n")
        if "type=phrase_related" in url or "type=phrase_fullsearch" in url or "type=phrase_questions" in url:
            return R(200, "Keyword;Search Volume;Keyword Difficulty;Intent;SERP Features\nsilver payal;9900;30;0;5,34\nhow to clean silver anklets;880;12;1;21\n")
        if "type=phrase_organic" in url: return R(200, "Domain;Url;SERP Features by Position\nrival.in;https://rival.in/anklets;5\n")
        return R(200, "ERROR 50 :: NOTHING FOUND")

with connect() as conn:
    conn.execute("DELETE FROM clients WHERE domain='community-test.shop'")
    cid = conn.execute("""INSERT INTO clients (name, domain, aliases, community_topics) VALUES ('Gargi','community-test.shop','{Gargi by PNG,PNG}','{silver anklets}')
                          RETURNING client_id""").fetchone()[0]
    conn.execute("INSERT INTO competitors (client_id, name, domain, tracked, aliases) VALUES (%s,'pngbrothers.com','pngbrothers.com',true,'{PNG Brothers}')", [cid])
    conn.execute("INSERT INTO competitors (client_id, name, domain, tracked) VALUES (%s,'Zavya','zavya.co',true)", [cid])
    assert sr.estimate("keyword") == 10 + 40 * 20 + 20 * 20 + 40 * 20 + 10 * 10
    f = FakeSemrush()
    lid, spent = sr.run(conn, cid, "KEY", "keyword", "Silver Anklets", session=f)
    res = conn.execute("SELECT result, units, query FROM research_lookups WHERE lookup_id=%s", [lid]).fetchone()
    o = res[0]["overview"]
    assert o["volume"] == 27100 and o["kd"] == 38 and o["intent"] == "commercial,transactional" and o["trend"] == [0.55, 0.67, 1.0] and o["fk"] == "5,34,52", o
    assert res[0]["related"][0] == {"keyword": "silver payal", "volume": 9900, "kd": 30, "intent": "commercial", "fk": "5,34"}, res[0]["related"]
    assert res[0]["serp"][0]["domain"] == "rival.in" and res[0]["serp"][0]["fp"] == "5"
    assert res[2] == "silver anklets" and spent == res[1] == 10 + 40 * 2 + 20 * 2 + 40 * 2 + 10 * 1, res[1]       # billed per line returned
    assert semrush.feature_units(conn, "research_keyword") == spent
    os.environ["SEMRUSH_CAP_RESEARCH_DOMAIN"] = "5"                                     # feature cap below one request: nothing is called
    n = len(f.urls); lid2, sp2 = sr.run(conn, cid, "KEY", "domain", "https://www.rival.in/x", session=f)
    assert sp2 == 0 and len(f.urls) == n and "stopped" in conn.execute("SELECT result FROM research_lookups WHERE lookup_id=%s", [lid2]).fetchone()[0]
    assert conn.execute("SELECT query FROM research_lookups WHERE lookup_id=%s", [lid2]).fetchone()[0] == "rival.in"
    del os.environ["SEMRUSH_CAP_RESEARCH_DOMAIN"]
# ---------------------------------------------------------------- topics found on their own
import community as cm2
with connect() as conn:
    conn.execute("DELETE FROM clients WHERE domain='autotopic-test.shop'")
    t = conn.execute("INSERT INTO clients (name, domain, aliases) VALUES ('Gargi','autotopic-test.shop','{PNG}') RETURNING client_id").fetchone()[0]
    conn.execute("INSERT INTO competitors (client_id, name, domain, tracked, aliases) VALUES (%s,'pngbrothers.com','pngbrothers.com',true,'{PNG Brothers}')", [t])
    for slug in ("silver-anklets", "silver-earrings", "all", "new-arrivals", "under-999", "gargi-specials", "silver-mangalsutra", "toe-rings"):
        conn.execute("INSERT INTO pages (client_id, url, page_type) VALUES (%s,%s,'collection')", [t, f"https://autotopic-test.shop/collections/{slug}"])
    day = dt.date.today() - dt.timedelta(days=3)
    for q, imp in (("silver anklets for women", 900), ("silver anklet", 400), ("silver earrings", 700), ("toe rings silver", 300),
                   ("png brothers anklets", 5000), ("gargi earrings", 9000)):
        conn.execute("INSERT INTO gsc_search_daily VALUES (%s,%s,%s,'https://autotopic-test.shop/','ind','MOBILE',1,%s,0.1,5,%s)", [t, day, q, imp, q.startswith("gargi")])
    auto = cm2.auto_topics(conn, t)
    assert auto[:3] == ["silver anklets", "silver earrings", "toe rings"] and "silver mangalsutra" in auto, auto     # ranked by searches; no brand, sale or price pages
    assert not any("gargi" in a or "png" in a or a in ("all", "new arrivals") for a in auto), auto
    assert cm2.topics(conn, t) == (auto, True)
    conn.execute("UPDATE clients SET community_topics='{anklets}' WHERE client_id=%s", [t])
    assert cm2.topics(conn, t) == (["anklets"], False)
    conn.execute("DELETE FROM clients WHERE client_id=%s", [t])
print("topics: found from collections ranked by non-branded searches, without brand, competitor, sale or price pages; a person can override")

print("research: keyword lookup stored with every section, columns cleaned, billed per line, feature cap stops before any call")

# ---------------------------------------------------------------- Reddit through Apify
assert cm.is_question("How do you clean silver anklets at home?") and not cm.is_question("Bought earrings, lovely")
assert cm.tone("lovely quality, would recommend") == "positive" and cm.tone("it tarnished, disappointed") == "negative"
assert cm.pain_point("my payal turned black in a month") == "Tarnish and care" and cm.pain_point("great gift") is None

class FakeApify:
    def __init__(s): s.calls = []
    def post(s, url, params=None, json=None, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer TOKEN" and "TOKEN" not in url
        s.calls.append(json)
        if json.get("searchCommunities"):
            return R(200, js=[{"dataType": "community", "name": "r/IndianFashionAddicts", "title": "Indian fashion", "numberOfMembers": 520000,
                               "description": "silver and gold jewellery, sarees"}, {"dataType": "community", "name": "r/nsfwthing", "over18": True},
                              {"dataType": "community", "name": "r/pune", "numberOfMembers": 240000, "description": "city"}][:json["maxItems"]])
        if json.get("startUrls") and "/search/" in json["startUrls"][0]["url"]:
            assert "restrict_sr=1" in json["startUrls"][0]["url"] and "silver%20anklets" in json["startUrls"][0]["url"], json
            return R(200, js=[{"dataType": "post", "id": f"in{i}", "url": f"https://www.reddit.com/r/IndianFashionAddicts/comments/in{i}/x/",
                               "parsedCommunityName": "IndianFashionAddicts", "title": "Do silver anklets turn black?", "upVotes": 5} for i in range(json["maxItems"])])
        if json.get("startUrls"):
            return R(200, js=[{"dataType": "post", "parsedId": "t3_abc12", "url": "https://www.reddit.com/r/IndianFashionAddicts/comments/abc12/where_to_buy/",
                               "parsedCommunityName": "IndianFashionAddicts", "title": "Where to buy real silver?", "body": "PNG Brothers or Zavya?",
                               "upVotes": 120, "numberOfComments": 40, "createdAt": "2026-08-01T10:00:00Z"},
                              {"dataType": "comment", "id": "c1", "url": "https://www.reddit.com/r/IndianFashionAddicts/comments/abc12/where_to_buy/c1/",
                               "communityName": "r/IndianFashionAddicts", "body": "Zavya rings tarnished fast, disappointed", "upVotes": 30}])
        q = json["searches"][0]
        return R(200, js=[{"dataType": "post", "id": f"{q[:3]}{len(q)}x{i}", "url": f"https://www.reddit.com/r/pune/comments/{q[:3].replace(' ', '')}{len(q)}x{i}/x/",
                           "communityName": "r/pune", "title": f"Is Gargi on FC Road good? {i}" if q == "Gargi" else f"{q} question {i}?",
                           "body": "Gargi earrings were lovely, would recommend" if q == "Gargi" else "", "upVotes": i} for i in range(json["maxItems"])])

with connect() as conn:
    # a cited Reddit thread for this brand (through a real answer row)
    pid = conn.execute("INSERT INTO prompts (client_id, text, approved) VALUES (%s,'best silver jewellery brands',true) RETURNING prompt_id", [cid]).fetchone()[0]
    rid = conn.execute("""INSERT INTO runs (client_id, prompt_id, engine, region, sample_no, cycle, status) VALUES (%s,%s,'chatgpt','IN',1,'2026-11-01','ok')
                          RETURNING run_id""", [cid, pid]).fetchone()[0]
    aid = conn.execute("INSERT INTO answers (client_id, run_id, full_text) VALUES (%s,%s,'Try Zavya.') RETURNING answer_id", [cid, rid]).fetchone()[0]
    conn.execute("""INSERT INTO citations (client_id, answer_id, url, domain) VALUES
                    (%s,%s,'https://www.reddit.com/r/IndianFashionAddicts/comments/abc12/where_to_buy/','reddit.com'),
                    (%s,%s,'https://www.youtube.com/watch?v=vidCITED001','youtube.com')""", [cid, aid, cid, aid])
    base = reddit.items_used(conn); reddit.TOTAL_ITEMS = base + 60     # usage logs cannot be deleted by the app: test against what is there
    fa = FakeApify()
    out = reddit.run(conn, cid, "TOKEN", max_items=40, session=fa)
    assert fa.calls[0]["searchCommunities"] and fa.calls[0]["searches"] == ["silver anklets"], fa.calls[0]
    assert fa.calls[1]["startUrls"][0]["url"].endswith("/abc12/where_to_buy/"), fa.calls[1]
    searched = [c["searches"][0] for c in fa.calls[2:] if c.get("searches")]
    comms = dict(conn.execute("SELECT name, members FROM reddit_communities WHERE client_id=%s", [cid]).fetchall())
    assert comms == {"IndianFashionAddicts": 520000, "pune": 240000}, comms                       # adult communities skipped
    assert conn.execute("SELECT count(*) FROM reddit_items WHERE client_id=%s AND item_id LIKE 'in%%'", [cid]).fetchone()[0] > 0   # read inside the top community
    assert searched[:4] == ["Gargi", "Gargi by PNG", "PNG Brothers", "Zavya"] and "silver anklets" in searched, searched
    items = {r[0]: r[1:] for r in conn.execute("SELECT item_id, brands, sentiment, found_by, is_question, pain_point FROM reddit_items WHERE client_id=%s", [cid])}
    assert set(items["abc12"][0]) == {"pngbrothers.com", "Zavya"} and items["abc12"][2] == "cited", items["abc12"]      # PNG Brothers is not Gargi
    assert items["c1"][1:] == ("negative", "cited", False, "Tarnish and care"), items["c1"]
    assert items["gar5x0"][0] == ["Gargi"] and items["gar5x0"][1] == "positive" and items["gar5x0"][3], items["gar5x0"]
    used = reddit.items_used(conn) - base
    assert used <= 40 and "Reddit results: 2 communities found" in out, (used, out)
    # lifetime limit: 60 in total, so the second run gets at most 20 and then stops
    fa2 = FakeApify(); o2 = reddit.run(conn, cid, "TOKEN", max_items=40, session=fa2)
    assert reddit.items_used(conn) - base == 60 and all(c["maxItems"] <= 60 - used for c in fa2.calls), reddit.items_used(conn)
    assert reddit.run(conn, cid, "TOKEN", 40, session=FakeApify()).startswith("stopped") if reddit.items_left(conn) == 0 else True
print("reddit: cited thread and searches pulled through Apify with the token in a header; brands, tone, questions and pain points labelled; result limits hold")

# ---------------------------------------------------------------- YouTube
class FakeYT:
    def __init__(s): s.calls = []
    def get(s, url, params=None, timeout=None):
        ep = url.rsplit("/", 1)[1]; s.calls.append(ep)
        assert params["key"] == "YTKEY"
        if ep == "search":
            q = params["q"]
            return R(200, js={"items": [{"id": {"videoId": f"{q[:4].replace(' ', '_')}{i:07d}"[:11]}} for i in range(3)]})
        if ep == "videos":
            return R(200, js={"items": [{"id": v, "snippet": {"title": ("Gargi haul" if v.startswith("Garg") else "PNG Brothers vs Zavya anklets" if v.startswith("silv") else "unboxing"),
                                                              "description": "", "channelId": "UCown" if v.startswith("Garg") else "UCcreator",
                                                              "channelTitle": "Gargi by PNG" if v.startswith("Garg") else "Style With Asha", "publishedAt": "2026-05-01T00:00:00Z"},
                                         "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "3"}, "contentDetails": {"duration": "PT4M10S"}}
                                        for v in params["id"].split(",")]})
        if ep == "channels":
            return R(200, js={"items": [{"id": c, "snippet": {"title": c}, "statistics": {"subscriberCount": "5000", "videoCount": "40", "viewCount": "100000"}}
                                        for c in params["id"].split(",")]})
        if ep == "commentThreads":
            return R(200, js={"items": [{"id": f"{params['videoId']}c", "snippet": {"topLevelComment": {"snippet": {
                "textDisplay": "Does this turn black after a month?", "likeCount": 7, "publishedAt": "2026-06-01T00:00:00Z"}}}}]})
        return R(404, "")

with connect() as conn:
    ybase = youtube.used_today(conn); youtube.DAILY = int((ybase + 900) / 0.9) + 1
    fy = FakeYT()
    out = youtube.run(conn, cid, "YTKEY", per_search=3, comment_videos=2, session=fy)
    assert fy.calls.count("search") == 5, fy.calls                         # Gargi, Gargi by PNG, PNG Brothers, Zavya, silver anklets
    vids = {r[0]: r[1:] for r in conn.execute("SELECT video_id, brands, own_channel, found_by, duration_s FROM youtube_videos WHERE client_id=%s", [cid])}
    assert vids["vidCITED001"][2] == "cited", vids["vidCITED001"]
    own = [v for v in vids.values() if v[1]]
    assert own and all(v[0] == ["Gargi"] for v in own) and own[0][3] == 250, own
    silv = [v for k, v in vids.items() if k.startswith("silv")]
    assert silv and set(silv[0][0]) == {"pngbrothers.com", "Zavya"}, silv                # PNG Brothers is not Gargi
    assert conn.execute("SELECT count(*) FROM youtube_comments WHERE client_id=%s AND is_question AND pain_point='Tarnish and care'", [cid]).fetchone()[0] == 2
    assert youtube.used_today(conn) - ybase == 5 * 100 + 1 + 1 + 2, youtube.used_today(conn)
    # quota guard: 1,000 a day, stops at 900; another full run cannot fit its searches
    fy2 = FakeYT(); out2 = youtube.run(conn, cid, "YTKEY", per_search=3, comment_videos=2, session=fy2)
    assert "quota" in out2 and youtube.used_today(conn) - ybase <= 901, (out2, youtube.used_today(conn))
    conn.execute("DELETE FROM clients WHERE client_id=%s", [cid])
print("youtube: brand, competitor and topic searches plus cited videos; own channel found; comments read for questions and pain points; daily quota guard holds")
