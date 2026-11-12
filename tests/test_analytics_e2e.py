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
    assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 6
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
            assert "%2A%7Cor%7Cgiva.co%7C%2B%7Cor%7Cpalmonas.com%7C-%7Cor%7Cgargi.shop" in url, url
            return R(200, "Keyword;giva.co;palmonas.com;gargi.shop;Search Volume;CPC;Competition\n"
                          "silver anklets for women;3;0;0;22200;0.3;1\ndemi fine jewellery;0;5;0;6600;0.5;1\n")
        return R(200, "ERROR 50 :: NOTHING FOUND")
with connect() as conn:                                     # the seed carries 400 credits already spent: a pull that could pass 500 never calls
    assert A.semrush_budget(conn) == {"cap": 500, "used": 400, "left": 100}
    assert conn.execute("SELECT count(*) FROM semrush_keywords WHERE client_id=%s", [cid]).fetchone()[0] == 20
    blocked = FakeSemrush(); out = semrush.sync_client(conn, cid, "KEY", session=blocked)
    assert blocked.urls == [] and "stopped" in out and "(0 units)" in out, out
semrush.TOTAL_CAP = 100000                                 # lift the cap only to exercise parsing against the fake API
fs = FakeSemrush()
with connect() as conn:
    out = semrush.sync_client(conn, cid, "KEY", n_keywords=2, n_competitors=2, n_gap=2, session=fs, today=dt.date(2026, 11, 9), competitor_refresh_days=0)
    assert "2 non-branded keywords" in out and "2 competitors" in out and "2 untapped keywords vs 2 competitors" in out and "(260 units)" in out, out
    assert conn.execute("SELECT sum(units) FROM semrush_usage WHERE client_id=%s", [cid]).fetchone()[0] == 660
    assert conn.execute("SELECT tracked FROM competitors WHERE client_id=%s AND name='myntra.com'", [cid]).fetchone()[0] is False
    stopped = semrush.sync_client(conn, cid, "KEY", n_keywords=2, n_competitors=2, n_gap=2, max_units=100, session=FakeSemrush(), competitor_refresh_days=0)
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
checks = {"/c": ["Gargi", "95"], f"/c/{cid}": ["Where Gargi stands", "Untapped keywords", "silver anklets for women", "+50.0%"],
          f"/c/{cid}/search": ["Non-branded clicks", "silver mangalsutra"], f"/c/{cid}/search?tab=revenue": ["/collections/mangalsutra", "100.0%"],
          f"/c/{cid}/search?tab=pages": ["/collections/mangalsutra"], f"/c/{cid}/competitors": ["silver anklets for women", "giva.co · 3"],
          f"/c/{cid}/competitors?tab=list": ["myntra.com", "Track"], f"/c/{cid}/competitors?tab=keywords": ["silver bracelet for women"], f"/c/{cid}/settings?tab=data": ["Semrush is off", "disabled"],
          f"/c/{cid}/ai": ["Hovers Visibility Score", "#2", "Zaveri Pearls", "Failed"], f"/c/{cid}/ai/answer/{aid}": ["GIVA is the best known", "Read only"],
          f"/c/{cid}/pages": ["Not checked", "silver mangalsutra"], f"/c/{cid}/settings": ["Gargi by PNG"],
          f"/c/{cid}/settings?tab=prompts": ["best silver jewellery brands in India", "Approve all"]}
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
print("every screen renders; brand rules and competitor tracking save; CSRF enforced")

with psycopg.connect(owner, autocommit=True) as c:                                                 # a client session sees only itself
    c.execute("SET ROLE aeo_app"); c.execute("SELECT set_config('app.client_id', %s, false)", [str(other)])
    for t in ("semrush_keywords", "keyword_gaps", "answers", "citations", "scores_daily", "ga4_organic_landing", "competitors"):
        assert c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0, t
print("tenant isolation holds for the new tables")
