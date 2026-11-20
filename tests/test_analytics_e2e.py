"""End to end for analytics v1, the way the server runs it: database owned by a non-superuser that switches to aeo_app,
Semrush and OpenAI replaced by fakes, then every dashboard screen rendered. Needs ADMIN_DATABASE_URL (superuser, used
only to create the owner role and an empty database)."""
import os, sys, json, datetime as dt, re
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "webapp"), os.path.join(ROOT, "scripts")]
import psycopg

admin = os.environ["ADMIN_DATABASE_URL"]                  # e.g. postgresql://postgres@/postgres?host=/tmp&port=5433
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS aeo_e2e")
    if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='site_owner'").fetchone():
        c.execute("CREATE ROLE site_owner LOGIN PASSWORD 'owner-pw' CREATEROLE NOSUPERUSER")
    c.execute("CREATE DATABASE aeo_e2e OWNER site_owner")
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname='aeo_app'").fetchone():   # roles are server-wide: left over from other tests
        c.execute("GRANT aeo_app TO site_owner WITH ADMIN OPTION")                # same rights the owner gets when it creates the role
owner = re.sub(r"postgresql://[^@]*@/[^?]*", "postgresql://site_owner:owner-pw@/aeo_e2e", admin)
from start_local import migrate
with psycopg.connect(owner, autocommit=True) as c:
    migrate(c)
    assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 23
os.environ.update(DATABASE_URL=owner, DB_SET_ROLE="aeo_app")
from common import connect
with connect() as c:
    assert c.execute("SELECT current_user").fetchone()[0] == "aeo_app"
print("migrations run as a non-superuser owner; the app acts as aeo_app")

import setup_brand, semrush, ai_visibility as av, analytics as A
seed = json.load(open(os.path.join(ROOT, "seeds", "gargi.json")))
with connect() as conn:
    cid, n = setup_brand.setup(conn, seed)
    assert n == 14 and setup_brand.setup(conn, seed)[1] == 0                      # second run adds nothing
    cid = str(cid)
    other = conn.execute("INSERT INTO clients (name, domain) VALUES ('Other brand','other.in') RETURNING client_id").fetchone()[0]
    first2 = [r[0] for r in conn.execute("SELECT prompt_id FROM prompts WHERE client_id=%s ORDER BY text LIMIT 2", [cid])]
    conn.execute("UPDATE prompts SET approved=true WHERE prompt_id = ANY(%s)", [first2])
    for name, dom, al in (("GIVA", "giva.co", ["Giva Jewellery"]), ("Palmonas", "palmonas.com", [])):
        conn.execute("INSERT INTO competitors (client_id, name, domain, aliases, tracked) VALUES (%s,%s,%s,%s,true)", [cid, name, dom, al])

# ------------------------------------------------------------------ Semrush with a fake API
class R:
    def __init__(s, code, text=None, js=None): s.status_code, s.text, s._js = code, text if text is not None else json.dumps(js), js
    def json(s): return s._js
class FakeSemrush:
    def __init__(s): s.urls = []
    def get(s, url, timeout=None):
        s.urls.append(url)
        if "type=domain_organic_organic" in url:
            return R(200, "Domain;Competitor Relevance;Common Keywords;Organic Keywords;Organic Traffic;Organic Cost;Adwords Keywords\n"
                          "giva.co;0.41;820;40100;510000;0;0\nmyntra.com;0.12;600;900000;9000000;0;0\n")
        if "type=domain_organic" in url:
            return R(200, "Keyword;Position;Previous Position;Search Volume;CPC;Url;Traffic (%);Competition;Keyword Difficulty;Intents\n"
                          "silver mangalsutra;4;7;12100;0.4;https://gargi.shop/collections/mangalsutra;9.1;1;38;3\n"
                          "oxidised earrings;11;9;27100;0.2;https://gargi.shop/collections/earrings;3.2;1;45;0,3\n")
        if "type=domain_domains" in url:
            assert "%2A%7Cor%7Cpngexclusive.com%7C%2B%7Cor%7Cpngbrothers.com%7C%2B%7Cor%7Cgiva.co%7C%2B%7Cor%7Cpalmonas.com%7C-%7Cor%7Cgargi.shop" in url, url
            assert "pngadgilandsons" not in url                                   # the group's own brand is never a gap competitor
            return R(200, "Keyword;giva.co;palmonas.com;gargi.shop;Search Volume;CPC;Competition\n"
                          "silver anklets for women;3;0;0;22200;0.3;1\ndemi fine jewellery;0;5;0;6600;0.5;1\n")
        return R(200, "ERROR 50 :: NOTHING FOUND")
with connect() as conn:                                     # the seed carries 400 credits already spent: a pull that could pass 500 never calls
    assert A.semrush_budget(conn) == {"cap": 500, "used": 400, "left": 100}
    assert conn.execute("SELECT count(*) FROM semrush_keywords WHERE client_id=%s", [cid]).fetchone()[0] == 20
    mark = conn.execute("SELECT max(usage_id) FROM semrush_usage").fetchone()[0]
    small = FakeSemrush(); out = semrush.sync_client(conn, cid, "KEY", session=small)            # 100 left: fewer keywords, not none
    assert len(small.urls) == 1 and "display_limit=10" in small.urls[0], small.urls
    assert A.semrush_budget(conn)["used"] <= 500, A.semrush_budget(conn)
    conn.execute("INSERT INTO semrush_usage (client_id, report, lines, units, feature) VALUES (%s,'top-up',0,%s,'keywords')",
                 [cid, 500 - A.semrush_budget(conn)["used"]])                                       # now at the cap
    blocked = FakeSemrush(); out = semrush.sync_client(conn, cid, "KEY", session=blocked)
    assert blocked.urls == [] and "stopped" in out and "(0 units)" in out, out
    conn.execute("DELETE FROM semrush_usage WHERE usage_id > %s", [mark])                         # back to the seed's 400 for what follows
    conn.execute("DELETE FROM semrush_keywords WHERE client_id=%s AND pulled_on = current_date", [cid])
semrush.TOTAL_CAP = 100000                                 # lift the cap only to exercise parsing against the fake API
fs = FakeSemrush()
with connect() as conn:
    out = semrush.sync_client(conn, cid, "KEY", n_keywords=2, n_competitors=2, n_gap=2, session=fs, today=dt.date(2026, 11, 9), competitor_refresh_days=0)
    assert "2 non-branded keywords" in out and "2 competitors" in out and "2 untapped keywords vs 4 competitors" in out and "(260 units)" in out, out
    assert conn.execute("SELECT sum(units) FROM semrush_usage WHERE client_id=%s", [cid]).fetchone()[0] == 660
    assert conn.execute("SELECT tracked FROM competitors WHERE client_id=%s AND name='myntra.com'", [cid]).fetchone()[0] is False
    stopped = semrush.sync_client(conn, cid, "KEY", n_keywords=2, n_competitors=2, n_gap=2, max_units=100, session=FakeSemrush(), today=dt.date(2026, 11, 9), competitor_refresh_days=0)
    assert "stopped" in stopped and "domain_domains could cost 160 units; 0 left" in stopped, stopped
    assert all("key=KEY" in u for u in fs.urls)
    kw_url = next(u for u in fs.urls if "type=domain_organic&" in u)          # non-branded only: brand words excluded
    assert "display_filter=-%7CPh%7CCo%7Cgadgil%7C-%7CPh%7CCo%7Cgargi%7C-%7CPh%7CCo%7Cpng" in kw_url, kw_url
    again = FakeSemrush(); note = semrush.sync_client(conn, cid, "KEY", n_keywords=2, session=again, today=dt.date(2026, 11, 12))
    assert "competitors already pulled this month" in note and not any("domain_organic_organic" in u for u in again.urls), note
semrush.TOTAL_CAP = 500
print("semrush: lifetime cap blocks with zero calls; keywords, competitors (suggested, not tracked), untapped keywords, unit log and budget stop")

# ------------------------------------------------------------------ ChatGPT with a fake API
ANSWER = ("For silver jewellery in India, GIVA is the best known name. Gargi by PNG is another good option, especially for "
          "mangalsutra and oxidised pieces. Some shoppers also like Zaveri Pearls.")
class FakeOpenAI:
    def __init__(s): s.calls = []
    def post(s, url, headers=None, json=None, timeout=None):
        s.calls.append(json); assert headers["Authorization"] == "Bearer SK"
        if "tools" in json:
            assert json["tools"][0]["type"] == "web_search" and json["tools"][0]["user_location"]["country"] == "IN"
            if "affordable" in json["input"]: return R(500, "upstream error")
            return R(200, js={"model": "gpt-test", "id": "resp_1", "usage": {"total_tokens": 900}, "output": [
                {"type": "web_search_call", "status": "completed", "action": {"type": "search", "query": "silver jewellery brands india",
                 "sources": [{"type": "url", "url": "https://www.reddit.com/r/IndianFashionAddicts/x"}, {"type": "url", "url": "https://giva.co/blog/silver"}]}},
                {"type": "message", "content": [{"type": "output_text", "text": ANSWER, "annotations": [
                    {"type": "url_citation", "url": "https://giva.co/blog/silver", "title": "GIVA blog", "start_index": 0, "end_index": 10},
                    {"type": "url_citation", "url": "https://www.gargi.shop/collections/mangalsutra", "title": "Gargi", "start_index": 11, "end_index": 20}]}]}]})
        brands = {"brands": [{"name": "GIVA", "sentiment": "positive", "recommended": True}, {"name": "Gargi by PNG", "sentiment": "positive", "recommended": True},
                             {"name": "Zaveri Pearls", "sentiment": "neutral", "recommended": False}]}
        return R(200, js={"output": [{"type": "message", "content": [{"type": "output_text", "text": json_dumps(brands)}]}]})
json_dumps = json.dumps
fo = FakeOpenAI(); day = dt.date(2026, 11, 9)
with connect() as conn:
    tally, scores = av.sync_client(conn, cid, "SK", samples=1, cycle=day, session=fo)
    assert tally == {"ok": 1, "error": 1}, tally
    s = scores["IN"]
    # Gargi: 2 runs but 1 failed, so 1 answer: named (place 2 -> 0.8), cited, positive -> 100*(.35+.25*.8+.25+.15) = 95
    assert s["Gargi"]["score"] == 95.0 and s["Gargi"]["avg_position"] == 2, s["Gargi"]
    assert s["GIVA"]["score"] == 100.0 and s["Palmonas"]["score"] == 0.0
    m = dict(conn.execute("SELECT brand, rank_in_answer FROM mentions WHERE client_id=%s", [cid]).fetchall())
    assert m == {"GIVA": 1, "Gargi": 2, "Zaveri Pearls": None}, m                # Zaveri kept as an untracked brand
    cit = conn.execute("SELECT domain, cited, is_client, source_type FROM citations WHERE client_id=%s ORDER BY domain", [cid]).fetchall()
    assert cit == [("gargi.shop", True, True, "own_site"), ("giva.co", True, False, "competitor"), ("reddit.com", False, False, "reddit")], cit
    again, _ = av.sync_client(conn, cid, "SK", samples=1, cycle=day, session=fo)  # rerun: retries only the failed prompt
    assert again == {"skipped": 1, "error": 1}, again
print("chatgpt: answer stored, mentions in order, citations split cited/read, failure retried, score = 95 (Competitive... Leader band)", av.band(95))

# ------------------------------------------------------------------ Search Console and GA4 rows, then the screens
with connect() as conn:
    end = dt.date(2026, 11, 8)
    with conn.cursor() as cur:
        for i in range(56):
            d = end - dt.timedelta(days=i); recent = i < 28
            cur.execute("""INSERT INTO gsc_search_daily VALUES (%s,%s,'silver mangalsutra','https://gargi.shop/collections/mangalsutra','ind','MOBILE',%s,%s,0.05,%s,false),
                                                               (%s,%s,'gargi jewellery','https://gargi.shop/','ind','MOBILE',20,100,0.2,1.2,true)""",
                        [cid, d, 12 if recent else 8, 240, 4.1 if recent else 6.0, cid, d])
            cur.execute("INSERT INTO ga4_daily VALUES (%s,%s,'Organic Search',50,30,5,2,4000),(%s,%s,'Paid Social',200,90,10,6,12000)", [cid, d, cid, d])
            cur.execute("INSERT INTO ga4_organic_landing VALUES (%s,%s,'/collections/mangalsutra?page=2',30,20,3,2,4000)", [cid, d])
    t = A.search_totals(conn, cid)
    assert t["nb_clicks"] == 336 and t["nb_clicks_change"] == 50.0, t                # 12 vs 8 a day
    q = A.top_queries(conn, cid, branded=False)[0]
    assert q["query"] == "silver mangalsutra" and float(q["position_gain"]) == 1.9
    rev = A.organic_revenue(conn, cid)
    assert rev["revenue_share"] == 25.0 and rev["session_share"] == 20.0
    lp = A.revenue_by_landing(conn, cid)[0]
    assert lp["page"] == "/collections/mangalsutra" and lp["top_query"] == "silver mangalsutra" and lp["revenue_share"] == 100.0, lp
print("search and revenue numbers check out")

os.environ.setdefault("FLASK_SECRET", "test"); os.environ.setdefault("TOKEN_KEY", "x" * 44)
for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"): os.environ.setdefault(k, "x")
os.environ.setdefault("OAUTH_REDIRECT_URI", "http://localhost:8080/auth/google/callback")
import app as webapp
cl = webapp.app.test_client()
assert cl.get(f"/c/{cid}").status_code == 302                                        # signed out: sent to sign-in
with cl.session_transaction() as s: s["staff_email"] = "seo@hovers.in"; s["csrf"] = "tok"
with connect() as conn:
    aid = conn.execute("SELECT answer_id FROM answers WHERE client_id=%s", [cid]).fetchone()[0]
    pid = conn.execute("INSERT INTO pages (client_id, url, page_type, primary_keyword) VALUES (%s,'https://gargi.shop/collections/mangalsutra','collection','silver mangalsutra') RETURNING page_id", [cid]).fetchone()[0]
checks = {"/c": ["Gargi", "95"], f"/c/{cid}": ["Brand visibility score", "LLMs overview", "Tracked keywords", "Keyword gap", "silver anklets for women", "+50.0%"], f"/c/{cid}?tab=competitors": ["Brand visibility ranking", "Google footprint", "GIVA", "Primary"], f"/c/{cid}/ai-traffic": ["AI traffic performance"], f"/c/{cid}/ai?b=GIVA": ["LLM prompts", "GIVA"],
          f"/c/{cid}/search": ["Non-branded clicks", "silver mangalsutra"], f"/c/{cid}/search?tab=revenue": ["/collections/mangalsutra", "100.0%"],
          f"/c/{cid}/search?tab=pages": ["/collections/mangalsutra"], f"/c/{cid}/competitors": ["silver anklets for women", "giva.co · 3"],
          f"/c/{cid}/competitors?tab=list": ["myntra.com", "Track"], f"/c/{cid}/competitors?tab=keywords": ["silver bracelet for women", "Refresh keyword data"], f"/c/{cid}/settings?tab=data": ["Uses up to 400 Semrush credits", "disabled", "Semrush project ID", "Refresh keyword data now", "Data sources", "Refresh history"],
          f"/c/{cid}/technical": ["Crawlability, indexation and site health", "Technical SEO index", "No crawl yet", "Import Site Audit"], f"/c/{cid}/ideas": ["Query demand", "No ideas yet"],
          f"/c/{cid}/search?tab=rankings": ["silver mangalsutra", "Track"], f"/c/{cid}/competitors?tab=backlinks": ["No backlink data yet", "Pull backlinks"],
          f"/c/{cid}/research/keyword": ["Keyword research", "No lookups yet", "disabled"], f"/c/{cid}/research/domain": ["Quick pick", "gargi.shop"],
          f"/c/{cid}/research/backlinks": ["Backlink analytics"], f"/c/{cid}/research/paid": ["Paid and shopping"],
          f"/c/{cid}/reddit": ["What Reddit says", "Reddit insights appear after the first weekly refresh", "Not connected yet"], f"/c/{cid}/youtube": ["What YouTube shows", "Not connected yet"],
          f"/c/{cid}/ai": ["Hovers Visibility Score", "#2", "Zaveri Pearls", "Failed"], f"/c/{cid}/ai/answer/{aid}": ["GIVA is the best known", "Read only"],
          f"/c/{cid}/pages": ["Pages found", "No checked pages in this view yet"], f"/c/{cid}/pages?show=unchecked": ["silver mangalsutra", "Not checked yet"], f"/c/{cid}/settings": ["Gargi by PNG"],
          f"/c/{cid}/settings?tab=prompts": ["best silver jewellery brands in India", "Approve all"]}
import rank
with connect() as conn: rank.sync_client(conn, cid)                                 # tracked keywords start from Search Console data
for url, needles in checks.items():
    r = cl.get(url); body = r.get_data(as_text=True)
    assert r.status_code == 200, (url, r.status_code, body[:500])
    for nd in needles: assert nd in body, (url, nd)
r = cl.post(f"/c/{cid}/settings", data={"csrf": "tok", "what": "brand", "brand_spellings": "Gargi, Gargi by PNG", "usps": "925 silver, 30-day returns",
                                         "term_fixes": "sterling => 925 sterling silver", "review_days": "3", "region_words": "India"})
assert r.status_code == 302
with connect() as conn:
    br = A.rows(conn.execute("SELECT usps, term_fixes, review_days FROM brand_rules WHERE client_id=%s", [cid]), ["u", "t", "d"])[0]
    assert br == {"u": ["925 silver", "30-day returns"], "t": {"sterling": "925 sterling silver"}, "d": 3}, br
assert cl.post(f"/c/{cid}/settings", data={"what": "brand"}).status_code == 400                    # no CSRF token
r = cl.post(f"/c/{cid}/competitors", data={"csrf": "tok", "add_name": "Zaveri Pearls"})
with connect() as conn:
    assert conn.execute("SELECT tracked FROM competitors WHERE client_id=%s AND name='Zaveri Pearls'", [cid]).fetchone()[0] is True
r = cl.post(f"/c/{cid}/semrush", data={"csrf": "tok", "feature": "audit", "back": "https://evil.example/"})
assert r.status_code == 302 and r.location.startswith(f"/c/{cid}/settings") and "msg=Keyword+data+is+not+connected" in r.location, r.location    # not ready: nothing runs, no outside redirect
with connect() as conn:
    assert conn.execute("SELECT count(*) FROM collector_runs WHERE client_id=%s AND collector LIKE 'semrush_%%'", [cid]).fetchone()[0] == 0
    conn.execute("""INSERT INTO tech_issues (client_id, issue_code, source, title, severity, pages) VALUES (%s,'page_4xx','hovers','Page returns a 4xx error','error',2)""", [cid])
r = cl.post(f"/c/{cid}/technical", data={"csrf": "tok", "issue": "page_4xx", "status": "in_progress", "owner": "Dev", "due": "2026-12-01"})
assert r.status_code == 302
assert "Dev" in cl.get(f"/c/{cid}/technical?issue=page_4xx").get_data(as_text=True)
r = cl.post(f"/c/{cid}/rankings", data={"csrf": "tok", "keywords": "Silver Toe Rings | https://gargi.shop/collections/toe-rings"})
with connect() as conn:
    assert conn.execute("SELECT target_url FROM rank_keywords WHERE client_id=%s AND keyword='silver toe rings'", [cid]).fetchone()[0].endswith("toe-rings")
print("every screen renders; brand rules, competitor tracking, issue tracker and rank keywords save; CSRF enforced; Semrush buttons refuse when not ready")

# ------------------------------------------------------------------ sister brands and competitor names that contain the brand's words
import gsc
with connect() as conn:
    day = dt.date.today() - dt.timedelta(days=4)
    for q in ("png brothers silver", "png exclusive rings", "png gargi earrings", "png gadgil and sons", "gargi silver chain", "silver toe rings"):
        conn.execute("INSERT INTO gsc_search_daily VALUES (%s,%s,%s,'https://gargi.shop/','ind','MOBILE',1,10,0.1,5,true) ON CONFLICT DO NOTHING", [cid, day, q])
    name, dom, al = conn.execute("SELECT name, domain, aliases FROM clients WHERE client_id=%s", [cid]).fetchone()
    gsc.relabel_branded(conn, cid, gsc.brand_terms(name, dom, al))
    lab = dict(conn.execute("SELECT query, is_branded FROM gsc_search_daily WHERE client_id=%s AND date=%s AND query LIKE ANY (%s)",
                            [cid, day, ["png%", "%silver chain", "silver toe rings"]]).fetchall())
    assert lab == {"png brothers silver": False, "png exclusive rings": False, "png gargi earrings": True, "png gadgil and sons": True,
                   "gargi silver chain": True, "silver toe rings": False}, lab
    assert gsc.is_branded("PNG Brothers", gsc.brand_terms(name, dom, al), ["pngbrothers"]) is False
    m = av.find_mentions("Good options: PNG Brothers for gold, then Gargi by PNG for silver.", av.tracked_brands(conn, cid))
    assert [(x["name"], x["rank"]) for x in m] == [("pngbrothers.com", 1), ("Gargi", 2)], m          # "PNG" inside "PNG Brothers" is not Gargi
    zid = conn.execute("SELECT competitor_id FROM competitors WHERE client_id=%s AND domain='zavya.co'", [cid]).fetchone()[0]
    own = dict(conn.execute("SELECT domain, own_brand FROM competitors WHERE client_id=%s AND domain LIKE 'png%%'", [cid]).fetchall())
    assert own == {"pngexclusive.com": False, "pngbrothers.com": False, "pngadgilandsons.com": True}, own
cl.post(f"/c/{cid}/competitors", data={"csrf": "tok", "own_brand": str(zid)})
with connect() as conn:
    assert conn.execute("SELECT own_brand, tracked FROM competitors WHERE competitor_id=%s", [zid]).fetchone() == (True, False)
    assert "zavya.co" in conn.execute("SELECT sister_domains FROM brand_rules WHERE client_id=%s", [cid]).fetchone()[0]
assert "own brands, never tracked" in cl.get(f"/c/{cid}/competitors?tab=list").get_data(as_text=True)
cl.post(f"/c/{cid}/competitors", data={"csrf": "tok", "own_brand": str(zid)})
with connect() as conn:
    assert "zavya.co" not in conn.execute("SELECT sister_domains FROM brand_rules WHERE client_id=%s", [cid]).fetchone()[0]
# ------------------------------------------------------------------ Reddit and YouTube start on their own once a key is set
import dashboard
started = []
real_start_job = dashboard.start_job
dashboard.start_job = lambda cid_, only, **k: started.append(only)
os.environ["APIFY_TOKEN"] = "x"; os.environ["YOUTUBE_API_KEY"] = "x"
page = cl.get(f"/c/{cid}/reddit").get_data(as_text=True)
assert started == ["reddit"] and "Pulling now" in page and "Not connected yet" not in page, (started, page[:200])
cl.get(f"/c/{cid}/reddit"); assert started == ["reddit"]                     # not started twice
assert "Pulling now" in cl.get(f"/c/{cid}/youtube").get_data(as_text=True) and started == ["reddit", "youtube"]
with connect() as conn:                                                     # a finished run this week: nothing starts
    conn.execute("DELETE FROM collector_runs WHERE client_id=%s AND collector LIKE '%%_auto'", [cid])
    conn.execute("INSERT INTO collector_runs (client_id, collector, status, finished_at) VALUES (%s,'reddit','ok',now())", [cid])
assert "Automatic" in cl.get(f"/c/{cid}/reddit").get_data(as_text=True) and started == ["reddit", "youtube"]
del os.environ["APIFY_TOKEN"], os.environ["YOUTUBE_API_KEY"]
with connect() as conn: conn.execute("DELETE FROM collector_runs WHERE client_id=%s AND collector IN ('reddit','reddit_auto','youtube_auto')", [cid])
print("reddit and youtube: the first pull starts by itself when a key is set and the screen opens, once; weekly after that")

# ------------------------------------------------------------------ monthly report: page, edits, download
import report as _rep
mstr = _rep.default_month().strftime("%Y-%m")
page = cl.get(f"/c/{cid}/report?month={mstr}").get_data(as_text=True)
assert "Monthly SEO report" in page and "Download PowerPoint" in page and "Work done in" in page, page[:300]
r = cl.post(f"/c/{cid}/report", data={"csrf": "tok", "month": mstr, "work": "Content | Wrote 4 blogs | 30 clicks", "plan": "Blogs\nGoal: x\n- y"})
assert r.status_code == 302 and "Edited by" in cl.get(f"/c/{cid}/report?month={mstr}").get_data(as_text=True)
r = cl.get(f"/c/{cid}/report/{mstr}.pptx")
assert r.status_code == 200 and r.data[:2] == b"PK" and "SEO Report" in r.headers["Content-Disposition"], r.headers
from pptx import Presentation as _P
import io as _io
assert "Wrote 4 blogs" in " ".join(sh.text_frame.text for sl in _P(_io.BytesIO(r.data)).slides for sh in sl.shapes if sh.has_text_frame) or \
       any("Wrote 4 blogs" in c.text for sl in _P(_io.BytesIO(r.data)).slides for sh in sl.shapes if sh.has_table for row in sh.table.rows for c in row.cells)
cl.post(f"/c/{cid}/report", data={"csrf": "tok", "month": mstr, "do": "reset"})
assert "Edited by" not in cl.get(f"/c/{cid}/report?month={mstr}").get_data(as_text=True)
assert cl.get(f"/c/{cid}/report?month=bad").status_code == 400
print("monthly report: page shows the month's numbers and drafts, edits save and reset, the download is a PowerPoint with the edits")

print("sister brands: P N Gadgil & Sons is Gargi's own, PNG Brothers and PNG Exclusive searches and mentions are not Gargi's")

# ------------------------------------------------------------------ sample data for reviewing the Semrush screens
with connect() as conn:
    used = conn.execute("SELECT coalesce(sum(units),0), count(*) FROM semrush_usage").fetchone()
    real_feats = conn.execute("SELECT count(*) FROM semrush_keywords WHERE client_id=%s AND serp_features IS NOT NULL", [cid]).fetchone()[0]
cl.post(f"/c/{cid}/settings", data={"csrf": "tok", "what": "sample_load"})
for url, needles in {f"/c/{cid}/ideas": ["Sample data", "how to clean silver earrings at home"],
                     f"/c/{cid}/competitors?tab=backlinks": ["Sample data", "vogue-style.example", "Strongest sites"],
                     f"/c/{cid}/competitors?tab=keywords": ["AI Overview"], f"/c/{cid}/competitors?tab=gap": ["silver toe rings"],
                     f"/c/{cid}/technical": ["Duplicate title tags", "Semrush Site Audit, imported"],
                     f"/c/{cid}/technical?issue=semrush:4": ["Broken internal links"],
                     f"/c/{cid}/search?tab=rankings": ["Semrush"], f"/c/{cid}/settings?tab=data": ["Remove sample data"],
                     f"/c/{cid}/research/keyword": ["silver anklets", "27,100", "silver payal", "Plan content"], f"/c/{cid}/research/keyword?tab=questions": ["how to clean silver anklets"],
                     f"/c/{cid}/research/keyword?tab=serp": ["marketplace-one.example"],
                     f"/c/{cid}/research/domain": ["Ranking keywords", "Organic traffic, last 12 months"],
                     f"/c/{cid}/research/domain?tab=competitors": ["Shared keywords"],
                     f"/c/{cid}/research/backlinks?tab=anchors": ["gargi by png"], f"/c/{cid}/research/backlinks?tab=new": ["Festive gifting guide"],
                     f"/c/{cid}/research/paid?tab=copies": ["Sample ad"], f"/c/{cid}/research/paid?tab=shopping": ["Oxidised jhumka"],
                     f"/c/{cid}/reddit": ["Brand vs competitors", "Gargi (you)", "r/pune"],
                     f"/c/{cid}/reddit?tab=subreddits": ["r/weddingsIndia"], f"/c/{cid}/reddit?tab=questions": ["Tarnish and care", "Questions people ask"],
                     f"/c/{cid}/reddit?tab=cited": ["Where to buy real silver jewellery online"],
                     f"/c/{cid}/youtube": ["Topic searches", "silver anklets"], f"/c/{cid}/youtube?tab=videos": ["haul"],
                     f"/c/{cid}/youtube?tab=creators": ["Style With Asha"], f"/c/{cid}/youtube?tab=comments": ["Questions viewers ask"],
                     f"/c/{cid}/youtube?tab=cited": ["Real vs fake silver"]}.items():
    page = cl.get(url).get_data(as_text=True)
    for n in needles: assert n in page, (url, n)
assert "Sample data" not in cl.get(f"/c/{cid}/ai").get_data(as_text=True)
sys.path.insert(0, os.path.join(ROOT, "scripts")); import sample_semrush
with connect() as conn:                                    # a real YouTube pull removes only YouTube's sample rows
    sample_semrush.clear(conn, cid, only="youtube")
    assert conn.execute("SELECT count(*) FROM youtube_videos WHERE client_id=%s AND sample", [cid]).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM reddit_items WHERE client_id=%s AND sample", [cid]).fetchone()[0] > 0
    assert conn.execute("SELECT sample_semrush FROM clients WHERE client_id=%s", [cid]).fetchone()[0] is True
cl.post(f"/c/{cid}/settings", data={"csrf": "tok", "what": "sample_clear"})
with connect() as conn:
    assert conn.execute("SELECT coalesce(sum(units),0), count(*) FROM semrush_usage").fetchone() == used        # no credits, nothing logged
    for t in ("keyword_ideas", "keyword_gaps", "backlink_overview", "backlink_gap", "tech_crawls", "tech_issues", "rank_positions",
              "research_lookups", "reddit_items", "youtube_videos", "youtube_comments"):
        assert conn.execute(f"SELECT count(*) FROM {t} WHERE client_id=%s AND sample", [cid]).fetchone()[0] == 0, t
    assert conn.execute("SELECT count(*) FROM semrush_keywords WHERE client_id=%s AND serp_features IS NOT NULL", [cid]).fetchone()[0] == real_feats
    assert conn.execute("SELECT count(*) FROM tech_issues WHERE client_id=%s AND issue_code='page_4xx'", [cid]).fetchone()[0] == 1   # real rows kept
assert "Sample data" not in cl.get(f"/c/{cid}/ideas").get_data(as_text=True)
print("sample data: every Semrush screen fills with marked rows, no credits logged, one click removes only the sample")

with psycopg.connect(owner, autocommit=True) as c:                                                 # a client session sees only itself
    c.execute("SET ROLE aeo_app"); c.execute("SELECT set_config('app.client_id', %s, false)", [str(other)])
    for t in ("semrush_keywords", "keyword_gaps", "answers", "citations", "scores_daily", "ga4_organic_landing", "competitors",
              "tech_issues", "rank_keywords", "rank_positions", "keyword_ideas", "backlink_overview", "backlink_gap",
              "research_lookups", "reddit_items", "youtube_videos", "monthly_reports"):
        assert c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0, t
print("tenant isolation holds for the new tables")

# nightly trigger for the free cloud host: off without CRON_TOKEN, refuses a wrong token, starts one pull at a time
import dashboard
assert cl.post("/cron/daily").status_code == 403
os.environ["CRON_TOKEN"] = "night-token"
assert cl.post("/cron/daily", headers={"Authorization": "Bearer wrong"}).status_code == 403
started = []
class FakeProc:
    def poll(s): return None
real_run = dashboard.run_daily
dashboard.run_daily = lambda args, out=None: (started.append(args), dashboard.JOBS.append(FakeProc()))
h = {"Authorization": "Bearer night-token"}
assert cl.post("/cron/daily", headers=h).get_json() == {"started": True, "running": True} and started == [[]]
assert cl.post("/cron/daily", headers=h).get_json()["started"] is False and len(started) == 1
assert cl.get("/cron/status", headers=h).get_json() == {"running": True}
dashboard.JOBS.clear(); dashboard.run_daily = real_run
assert cl.get("/cron/status", headers=h).get_json() == {"running": False}
del os.environ["CRON_TOKEN"]
print("nightly trigger: needs the token, starts one pull at a time, reports when it is done")

# online copy (Vercel): a pull asked for on screen becomes tasks for the server worker, which starts at once (no Mac)
import worker as _worker
dashboard.start_job = real_start_job
dashboard.ONLINE = True
_kicks = []; _real_kick = _worker.kick; _worker.kick = lambda: _kicks.append(1) or True
r = cl.post(f"/c/{cid}/youtube", data={"csrf": "tok"}, headers={"Referer": f"http://localhost/c/{cid}/youtube"})
assert r.status_code == 302 and "msg=Started.+Results+appear" in r.headers["Location"] and _kicks, r.headers.get("Location")
dashboard.ONLINE = False; _worker.kick = _real_kick
with connect() as conn:
    q = conn.execute("SELECT pull_id, label, args, requested_by, started_at FROM pull_queue WHERE client_id=%s", [cid]).fetchall()
    tasks = conn.execute("SELECT step, status FROM pull_tasks WHERE pull_id=%s", [q[0][0]]).fetchall()
assert len(q) == 1 and q[0][1] == "youtube" and q[0][2][:4] == ["--client-id", cid, "--only", "youtube"] and q[0][3] == "seo@hovers.in" and q[0][4] is not None, q
assert tasks == [("youtube", "queued")], tasks
print("online copy: a pull on screen becomes tasks for the server worker, which is started straight away")

# ------------------------------------------------------------------ client sign-in: read-only, own brand only
import portal, flask
r = cl.post(f"/c/{cid}/settings", data={"csrf": "tok", "what": "viewer_add", "emails": "Marketing@Gargi.shop, not-an-email"})
assert "Not+an+email" in r.headers["Location"]
cl.post(f"/c/{cid}/settings", data={"csrf": "tok", "what": "viewer_add", "emails": "Marketing@Gargi.shop"})
assert "marketing@gargi.shop" in cl.get(f"/c/{cid}/settings?tab=access").get_data(as_text=True)
os.environ["RESEND_API_KEY"] = "test"
sent = []
portal.send_link = lambda email, link: sent.append((email, link))
vc = webapp.app.test_client()
assert "Email me a sign-in link" in vc.get("/portal/login").get_data(as_text=True)
with vc.session_transaction() as s: tok = s["csrf"]
body = vc.post("/portal/login", data={"csrf": tok, "email": "stranger@else.com"}).get_data(as_text=True)
assert "If this email has access" in body and not sent                  # same answer, nothing sent
vc.post("/portal/login", data={"csrf": tok, "email": "marketing@gargi.shop"})
assert len(sent) == 1 and sent[0][0] == "marketing@gargi.shop"
link = sent[0][1].split("localhost", 1)[1]
r = vc.get(link); assert r.status_code == 302 and r.headers["Location"].endswith("/portal")
assert vc.get(link).headers["Location"].find("expired") > 0                # a link works once
r = vc.get("/portal"); assert r.status_code == 302 and r.headers["Location"].endswith(f"/c/{cid}")
for path in ("", "?tab=competitors", "/search", "/search?tab=rankings", "/ai", "/ai-traffic", "/sources", "/competitors", "/competitors?tab=keywords", "/reddit", "/youtube", "/report", "/blogs"):
    r = vc.get(f"/c/{cid}{path}"); assert r.status_code == 200, (path, r.status_code)
    page = r.get_data(as_text=True)
    assert 'class="viewer"' in page and "marketing@gargi.shop" in page and "Google connections" not in page and "Keyword research" not in page, path
    low = page.lower()                                                # nothing team-only reaches a client's browser, not even hidden
    assert not __import__("re").search(r"class=\"[^\"]*\bstaff-only", page) and "credits" not in low and "apify" not in low and "intelligence layer" not in low \
        and "on the server" not in low and "confidential" not in low, (path, [w for w in ("credits", "apify", "on the server", "confidential") if w in low])
for path in (f"/c/{cid}/settings", f"/c/{cid}/pages", f"/c/{cid}/technical", f"/c/{cid}/research/keyword", "/c", f"/c/{other}", f"/c/{other}/search"):
    r = vc.get(path); assert r.status_code == 302 and r.headers["Location"].endswith("/portal"), (path, r.status_code)
for path, data in ((f"/c/{cid}/settings", {"what": "viewer_add", "emails": "x@y.com"}), (f"/c/{cid}/youtube", {}), (f"/c/{cid}/report", {})):
    with vc.session_transaction() as s: data["csrf"] = s["csrf"]
    r = vc.post(path, data=data); assert r.status_code == 302 and r.headers["Location"].endswith("/portal"), path
assert vc.get("/connections").status_code == 302
with webapp.app.test_request_context(f"/c/{other}"):                        # the database itself narrows a client's session
    flask.session["viewer_email"] = "marketing@gargi.shop"; flask.session["viewer_clients"] = [cid]
    flask.request.view_args = {"cid": str(other)}
    with dashboard.connect() as conn:
        assert conn.execute("SELECT count(*) FROM clients").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM client_viewers").fetchone()[0] == 0
    flask.request.view_args = {"cid": cid}
    with dashboard.connect() as conn:
        assert [str(r[0]) for r in conn.execute("SELECT client_id FROM clients")] == [cid]
r = vc.get(f"/c/{cid}/report/{dt.date(2026, 10, 1):%Y-%m}.pptx"); assert r.status_code == 200 and r.data[:2] == b"PK"
cl.post(f"/c/{cid}/settings", data={"csrf": "tok", "what": "viewer_remove", "emails": "marketing@gargi.shop"})
r = vc.get("/portal"); assert "no+longer+has+access" in r.headers["Location"]  # removal takes effect at once
assert vc.get(f"/c/{cid}").status_code == 302
# Google sign-in for clients: only a verified email on the list gets in; it never becomes a staff session
os.environ.update(CLIENT_OAUTH_CLIENT_ID="cid", CLIENT_OAUTH_CLIENT_SECRET="sec")
cl.post(f"/c/{cid}/settings", data={"csrf": "tok", "what": "viewer_add", "emails": "founder@gargi.shop"})
class _R:
    def __init__(s, j, code=200): s._j, s.status_code = j, code
    def json(s): return s._j
gc = webapp.app.test_client()
def google_as(email, verified=True):
    r = gc.get("/portal/auth/google"); assert "scope=openid+email+profile" in r.headers["Location"] and "webmasters" not in r.headers["Location"]
    with gc.session_transaction() as s: st = s["portal_state"]
    portal.requests.post = lambda *a, **k: _R({"access_token": "a"})
    portal.requests.get = lambda *a, **k: _R({"email": email, "email_verified": verified})
    return gc.get(f"/portal/auth/google/callback?state={st}&code=x")
assert "has+no+access" in google_as("someone@gargi.shop").headers["Location"]
assert "did+not+confirm" in google_as("founder@gargi.shop", verified=False).headers["Location"]
assert google_as("founder@gargi.shop").headers["Location"].endswith("/portal")
with gc.session_transaction() as s: assert s["viewer_email"] == "founder@gargi.shop" and "staff_email" not in s
assert gc.get(f"/c/{cid}/ai").status_code == 200 and gc.get(f"/c/{cid}/settings").status_code == 302
for k in ("RESEND_API_KEY", "CLIENT_OAUTH_CLIENT_ID", "CLIENT_OAUTH_CLIENT_SECRET"): del os.environ[k]
print("client sign-in: email link (once, generic answer) and Google (verified, listed), read-only screens, own brand only in the database, removal at once")
# Self-serve: the Orbit page, 'Talk to sales', trial sign-up through Google, the owner's own brand only, and the trial gate
import google_auth
webapp.ALLOWED[:] = ["hovers.in"]
an = webapp.app.test_client()
r = an.get("/"); assert r.status_code == 200 and b"Start free trial" in r.data and b"Talk to sales" in r.data
with an.session_transaction() as s: tokc = s["csrf"]
r = an.post("/demo", data={"csrf": tokc, "name": "Asha", "email": "asha@brand.in", "company": "Brand", "source": "utm=meta"})
assert r.status_code == 302 and "sent=1" in r.headers["Location"]
an.post("/demo", data={"csrf": tokc, "name": "Bot", "email": "bot@x.com", "company_url": "http://spam"})
assert an.post("/demo", data={"name": "No token", "email": "n@x.com"}).status_code == 400
with connect() as conn:
    assert [x[0] for x in conn.execute("SELECT email FROM demo_requests")] == ["asha@brand.in"]
assert b"Requests from the Orbit page" in cl.get("/c").data or b"asha@brand.in" in cl.get("/c").data
assert an.get("/signup").status_code == 200 and an.get("/login").status_code == 200
google_auth.exchange_code = lambda code: {"access_token": "a", "refresh_token": "r", "scope": "x"}
google_auth.userinfo = lambda tok: {"email": "owner@newbrand.in"}
google_auth.list_gsc_sites = lambda tok: [("sc-domain:newbrand.in", "siteOwner"), ("sc-domain:gargi.shop", "siteFullUser")]
google_auth.list_ga4_properties = lambda tok: [{"property_id": "999", "display_name": "NewBrand web", "account_name": "NB", "web_uris": ["https://www.newbrand.in"]}]
an.get("/signup/google")
with an.session_transaction() as s: st = s["oauth_state"]
r = an.get(f"/auth/google/callback?state={st}&code=x"); assert r.headers["Location"].endswith("/signup/choose"), r.headers["Location"]
with an.session_transaction() as s: assert "staff_email" not in s and s["signup_email"] == "owner@newbrand.in"
page = an.get("/signup/choose").get_data(as_text=True); assert "Analytics: NewBrand web" in page and "newbrand.in" in page
r = an.post("/signup/choose", data={"csrf": tokc, "pick": ["0", "1"], "site_0": "sc-domain:gargi.shop", "ga4_0": "", "site_1": "sc-domain:newbrand.in", "ga4_1": "999"})
assert "already+in+Orbit" in r.headers["Location"]                              # an existing brand can't be claimed by a trial
assert an.post("/signup/choose", data={"csrf": tokc, "pick": ["0"], "site_0": "sc-domain:other.com"}).status_code == 400
r = an.post("/signup/choose", data={"csrf": tokc, "pick": ["1"], "site_1": "sc-domain:newbrand.in", "ga4_1": "999", "name_1": "New Brand"})
assert "welcome=1" in r.headers["Location"], r.headers["Location"]
new = r.headers["Location"].split("/c/")[1].split("?")[0]
with connect() as conn:
    row = conn.execute("""SELECT c.name, c.trial_ends - current_date, i.ga4_property_id, v.role FROM clients c JOIN client_integrations i USING (client_id)
                          JOIN client_viewers v USING (client_id) WHERE c.client_id=%s""", [new]).fetchone()
    assert row == ("New Brand", 14, "999", "owner"), row
    assert conn.execute("SELECT count(*) FROM pull_queue WHERE client_id=%s", [new]).fetchone()[0] == 1
with an.session_transaction() as s: assert s["viewer_email"] == "owner@newbrand.in" and s["viewer_clients"] == [new] and "staff_email" not in s
assert "Welcome to Orbit" in an.get(r.headers["Location"]).get_data(as_text=True)
assert an.get(f"/c/{cid}").status_code == 302 and an.get(f"/c/{new}/settings").status_code == 302
with connect() as conn: conn.execute("UPDATE clients SET trial_ends = current_date - 1 WHERE client_id=%s", [new])
r = an.get(f"/c/{new}"); assert r.status_code == 402 and b"trial" in r.data.lower()
assert cl.get(f"/c/{new}").status_code == 200                                    # the Hovers team still sees it
webapp.ALLOWED[:] = []
print("self-serve: Orbit page, sales requests (csrf, honeypot), Google trial sign-up with GA4 matched, no claiming existing brands, owner sees own brand only, trial gate")
