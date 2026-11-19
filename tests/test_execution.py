"""Execution end to end: topic research from the brand's data, a draft written (OpenAI faked), cleaned and checked,
reviewed and edited on screen, previewed by link, the Shopify store connected (OAuth faked, signature checked), the post
published with its SEO fields, the token refreshed when it expires, and who may do what. Needs ADMIN_DATABASE_URL."""
import os, sys, json, re, hmac, hashlib, datetime as dt
from urllib.parse import urlparse, parse_qs, urlencode
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "webapp"), os.path.join(ROOT, "scripts")]
import psycopg

admin = os.environ["ADMIN_DATABASE_URL"]
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS aeo_exec")
    if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='site_owner'").fetchone():
        c.execute("CREATE ROLE site_owner LOGIN PASSWORD 'owner-pw' CREATEROLE NOSUPERUSER")
    c.execute("CREATE DATABASE aeo_exec OWNER site_owner")
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname='aeo_app'").fetchone():
        c.execute("GRANT aeo_app TO site_owner WITH ADMIN OPTION")
owner = re.sub(r"postgresql://[^@]*@/[^?]*", "postgresql://site_owner:owner-pw@/aeo_exec", admin)
from start_local import migrate
with psycopg.connect(owner, autocommit=True) as c: migrate(c)
os.environ.update(DATABASE_URL=owner, DB_SET_ROLE="aeo_app", FLASK_SECRET="t", TOKEN_KEY="x" * 43 + "=",
                  GOOGLE_OAUTH_CLIENT_ID="x", GOOGLE_OAUTH_CLIENT_SECRET="x", OAUTH_REDIRECT_URI="http://localhost/cb")
os.environ.pop("SHOPIFY_CLIENT_ID", None); os.environ.pop("SHOPIFY_CLIENT_SECRET", None)
from cryptography.fernet import Fernet
os.environ["TOKEN_KEY"] = Fernet.generate_key().decode()
from common import connect
import blog_writer as W, blogs as B

A, Bc = "22222222-0000-0000-0000-00000000000a", "22222222-0000-0000-0000-00000000000b"
today = dt.date.today()
with connect() as conn:
    conn.execute("INSERT INTO clients (client_id, name, domain, niche) VALUES (%s,'Gargi','gargi.shop','jewellery'), (%s,'Other','other.com',NULL)", [A, Bc])
    for q, imp, pos, br in [("how to clean silver jewellery at home", 900, 14.2, False), ("how to clean silver jewelry", 300, 18.0, False),
                            ("buy silver anklets online", 2000, 6.0, False), ("silver anklets", 5000, 9.0, False),
                            ("gargi silver earrings", 800, 1.2, True), ("best silver earrings for sensitive ears", 400, 22.0, False),
                            ("oxidised vs silver jewellery difference", 120, 31.0, False)]:
        conn.execute("""INSERT INTO gsc_search_daily (client_id, date, query, page, country, device, clicks, impressions, position, is_branded)
                        VALUES (%s,%s,%s,'https://gargi.shop/','ind','MOBILE',3,%s,%s,%s)""", [A, today - dt.timedelta(days=3), q, imp, pos, br])
    pid = conn.execute("INSERT INTO prompts (client_id, text, approved) VALUES (%s,'which silver jewellery brands are hallmarked in india',true) RETURNING prompt_id", [A]).fetchone()[0]
    rid = conn.execute("INSERT INTO runs (client_id, prompt_id, engine, region, sample_no, cycle) VALUES (%s,%s,'chatgpt','IN',1,%s) RETURNING run_id", [A, pid, today]).fetchone()[0]
    aid = conn.execute("INSERT INTO answers (client_id, run_id, full_text) VALUES (%s,%s,'GIVA and Palmonas') RETURNING answer_id", [A, rid]).fetchone()[0]
    conn.execute("INSERT INTO mentions (client_id, answer_id, brand, is_client, rank_in_answer) VALUES (%s,%s,'GIVA',false,1)", [A, aid])
    conn.execute("""INSERT INTO reddit_items (client_id, item_id, kind, subreddit, title, score, comments, found_by, is_question, pulled_on)
                    VALUES (%s,'t3_1','post','IndianFashionAddicts','Is 925 silver worth it for daily wear?',40,22,'topic',true,%s)""", [A, today])
    conn.execute("""INSERT INTO pages (client_id, url, page_type, primary_keyword) VALUES
        (%s,'https://gargi.shop/collections/silver-earrings','collection','silver earrings'),
        (%s,'https://gargi.shop/products/silver-polishing-cloth','product','silver polishing cloth'),
        (%s,'https://gargi.shop/blogs/news/silver-anklet-designs','blog','silver anklet designs')""", [A, A, A])
    conn.execute("INSERT INTO brand_rules (client_id, brand_spellings, banned_words, tone) VALUES (%s,'{Gargi}','{elevate}','warm and expert')", [A])

class S:                                                    # fake Google suggest
    def __init__(s): s.calls = 0
    def get(s, url, params=None, timeout=None):
        s.calls += 1
        class R: status_code = 200
        R.json = staticmethod(lambda: [params["q"], [params["q"] + " at home", "how to store silver jewellery", "silver"]])
        return R()
with connect() as conn:
    sug = S()
    n = W.research(conn, A, use_suggest=True, session=sug)
    ideas = {k: (p, ev) for k, p, ev in conn.execute("SELECT target_keyword, priority, evidence FROM content_drafts WHERE client_id=%s", [A])}
assert "how to clean silver jewellery at home" in ideas and "how to clean silver jewelry" not in ideas      # near-duplicates merged
assert len(ideas["how to clean silver jewellery at home"][1]) >= 2                                        # ... and their evidence kept
assert "buy silver anklets online" not in ideas and "silver anklets" not in ideas and not any("gargi" in k for k in ideas)  # no shopping, head or branded terms
assert "which silver jewellery brands are hallmarked in india" in ideas and "Is 925 silver worth it for daily wear?" in ideas
assert ideas["how to clean silver jewellery at home"][0] > ideas["Is 925 silver worth it for daily wear?"][0]
with connect() as conn:
    assert W.research(conn, A, use_suggest=False) == 0                                                     # second run adds nothing new
print(f"research: {n} ideas from Search Console, ChatGPT, Reddit and suggest; duplicates merged; shopping, head and branded terms left out")

# ---------------------------------------------------------------- writing (OpenAI faked)
LONG = " ".join(["Silver tarnishes when sulphur in the air reacts with the metal, so regular care keeps it bright."] * 60)
OUT = {"title": "How to Clean Silver Jewellery at Home — A Simple Guide", "meta_title": "How to Clean Silver Jewellery at Home: 5 Safe Ways",
       "meta_description": "Clean silver jewellery at home with five safe methods, what to avoid, and how to store pieces so they stay bright between wears and gifts.",
       "slug": "How to clean silver jewellery at home!", "excerpt": "Five safe ways to clean silver at home.", "tags": ["silver care", "how to"],
       "body_html": ("<h1>dup</h1><p>Clean silver with mild soap — then dry it.</p><script>alert(1)</script>"
                     "<h2>Why does silver tarnish?</h2><p>" + LONG + "</p><h2>What should you avoid?</h2><p>Avoid toothpaste; see "
                     "<a href='https://www.gia.edu/silver'>GIA</a>.</p><h2>How should you store silver?</h2><p>Use a "
                     "<a href='https://gargi.shop/products/silver-polishing-cloth'>polishing cloth</a>, browse "
                     "<a href='https://gargi.shop/collections/silver-earrings'>silver earrings</a>, not "
                     "<a href='https://gargi.shop/collections/made-up'>made up page</a> or <a href='javascript:x'>bad</a>.</p>"),
       "faqs": [{"q": f"Question {i}?", "a": f"Answer {i}."} for i in range(5)], "sources": [{"url": "https://www.gia.edu/silver", "title": "GIA"}]}
class O:
    def __init__(s): s.bodies = []
    def post(s, url, headers=None, json=None, timeout=None):
        s.bodies.append(json)
        class R: status_code = 200
        R.json = staticmethod(lambda: {"output": [{"type": "web_search_call"}, {"type": "message", "content": [{"type": "output_text", "text": __import__("json").dumps(OUT)}]}]})
        return R()
oa = O()
with connect() as conn:
    W.QUEUE = 6
    done = W.write(conn, A, "sk-test", n=1, session=oa)
    d = dict(zip(["status", "title", "slug", "body_html", "internal_links", "score", "checks", "preview_token", "faqs", "target_keyword", "word_count"],
                 conn.execute("""SELECT status, title, slug, body_html, internal_links, score, checks, preview_token, faqs, target_keyword, word_count
                                 FROM content_drafts WHERE client_id=%s AND status='draft'""", [A]).fetchone()))
sysmsg = oa.bodies[0]["input"][0]["content"]
assert "https://gargi.shop/collections/silver-earrings" in sysmsg and "elevate" in sysmsg and "warm and expert" in sysmsg
assert oa.bodies[0]["tools"] == [{"type": "web_search"}] and oa.bodies[0]["text"]["format"]["type"] == "json_schema"
assert d["status"] == "draft" and d["target_keyword"] == "how to clean silver jewellery at home" and "—" not in d["title"]
assert d["slug"] == "how-to-clean-silver-jewellery-at-home"
assert "<script" not in d["body_html"] and "<h1>" not in d["body_html"] and "—" not in d["body_html"]
assert 'href="https://www.gia.edu/silver" rel="nofollow noopener"' in d["body_html"]
assert "made-up" not in d["body_html"] and "made up page" in d["body_html"] and "javascript" not in d["body_html"]
assert set(d["internal_links"]) == {"https://gargi.shop/products/silver-polishing-cloth", "https://gargi.shop/collections/silver-earrings"}
ids = {r["check_id"]: r["result"] for r in d["checks"]}
assert ids["BLOG-08"] == "pass" and ids["BLOG-07"] == "pass" and ids["WRITE-01"] == "pass" and ids["BLOG-05"] == "pass" and ids["BLOG-13"] == "pass", ids
assert d["score"] and d["preview_token"] and len(d["faqs"]) == 5 and d["word_count"] > 900
print("writer: brand rules and real link targets in the brief; script, H1, em dashes, invented and unsafe links removed; source links nofollow; checks run")

# ---------------------------------------------------------------- screens
import app as webapp, shopify
cl = webapp.app.test_client()
with cl.session_transaction() as s: s["staff_email"] = "seo@hovers.in"; s["csrf"] = "tok"
with connect() as conn: did = str(conn.execute("SELECT draft_id FROM content_drafts WHERE client_id=%s AND status='draft'", [A]).fetchone()[0])
page = cl.get(f"/c/{A}/blogs").get_data(as_text=True)
assert "How to Clean Silver Jewellery at Home, A Simple Guide" in page and "Preview link" in page and "Not connected" in page and "Blog posts" in page
assert "Simple" not in re.sub(r"<[^>]+>", " ", page).split("Blog posts for")[0][-400:]                 # no Simple/Pro switch any more
page = cl.get(f"/c/{A}/blogs?tab=ideas").get_data(as_text=True)
assert "which silver jewellery brands are hallmarked in india" in page and "brand not named" in page and "Write this next" in page
page = cl.get(f"/c/{A}/blogs/{did}").get_data(as_text=True)
assert "How it shows in Google" in page and "Publish to Shopify" in page and "Connect the Shopify store" in page and "Checks" in page
r = cl.get(f"/p/{d['preview_token']}")
assert r.status_code == 200 and r.headers["X-Robots-Tag"] == "noindex, nofollow" and "Draft for review" in r.get_data(as_text=True)
assert "FAQPage" not in r.get_data(as_text=True) and "Question 4?" in r.get_data(as_text=True)
assert cl.get("/p/not-a-real-token-at-all").status_code == 404
form = {"csrf": "tok", "what": "save", "title": "How to clean silver jewellery at home", "meta_title": "Clean silver at home: 5 safe ways",
        "meta_description": "x" * 150, "slug": "clean-silver-at-home", "tags": "silver care, guides", "excerpt": "Short.",
        "body_html": d["body_html"] + "<p>Added by the editor <a href='https://gargi.shop/pages/care-guide'>care guide</a>.</p><iframe src='x'></iframe>",
        **{f"faq_q_{i}": f"Q{i}?" for i in range(5)}, **{f"faq_a_{i}": f"A{i}." for i in range(5)}}
r = cl.post(f"/c/{A}/blogs/{did}", data=form); assert "Saved" in r.headers["Location"]
with connect() as conn:
    t, slug, body, links, by = conn.execute("SELECT title, slug, body_html, internal_links, edited_by FROM content_drafts WHERE draft_id=%s", [did]).fetchone()
assert t == "How to clean silver jewellery at home" and slug == "clean-silver-at-home" and "care-guide" in body and "<iframe" not in body and by == "seo@hovers.in"
assert "https://gargi.shop/pages/care-guide" in links                                                    # a person's own link to the site is kept
r = cl.post(f"/c/{A}/blogs/{did}", data={"csrf": "tok", "what": "publish"})
assert "/publishing" in r.headers["Location"] and "No+Shopify+store" in r.headers["Location"]
print("screens: list, ideas, review, preview link (noindex), edits saved and re-checked, publish waits for a store")

# ---------------------------------------------------------------- connect Shopify (OAuth faked)
page = cl.get(f"/c/{A}/publishing").get_data(as_text=True)
assert "Shopify publishing is not switched on yet" in page and "Save this store's app" in page
os.environ.update(SHOPIFY_CLIENT_ID="orbit-app", SHOPIFY_CLIENT_SECRET="shh")
r = cl.post(f"/c/{A}/publishing", data={"csrf": "tok", "what": "connect", "shop": "evil.com"}); assert "err=" in r.headers["Location"]
r = cl.post(f"/c/{A}/publishing", data={"csrf": "tok", "what": "connect", "shop": "gargi-store"})
u = urlparse(r.headers["Location"]); q = parse_qs(u.query)
assert u.netloc == "gargi-store.myshopify.com" and u.path == "/admin/oauth/authorize" and q["scope"] == ["read_content,write_content"] and q["client_id"] == ["orbit-app"]
state = q["state"][0]
def signed(params, secret="shh"):
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return {**params, "hmac": hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()}
calls = []
class R:
    def __init__(s, code, j): s.status_code, s._j, s.text = code, j, json.dumps(j)
    def json(s): return s._j
ARTICLES = {}
def fake_post(url, data=None, json=None, headers=None, timeout=None):
    calls.append((url, data, json, headers))
    if url.endswith("/admin/oauth/access_token"):
        if data.get("grant_type") == "refresh_token":
            return R(200, {"access_token": "tok-2", "refresh_token": "ref-2", "expires_in": 3600, "refresh_token_expires_in": 7776000, "scope": "write_content"})
        assert data["expiring"] == "1" and data["code"] == "c0de" and data["client_secret"] == "shh"
        return R(200, {"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 3600, "refresh_token_expires_in": 7776000, "scope": "write_content"})
    assert "/admin/api/2026-10/graphql.json" in url and headers["X-Shopify-Access-Token"] in ("tok-1", "tok-2")
    qy = json["query"]
    if "primaryDomain" in qy:
        return R(200, {"data": {"shop": {"name": "Gargi by PNG", "primaryDomain": {"url": "https://gargi.shop"}},
                                "blogs": {"nodes": [{"id": "gid://shopify/Blog/1", "title": "News", "handle": "news"},
                                                    {"id": "gid://shopify/Blog/2", "title": "Journal", "handle": "journal"}]}}})
    if "articleCreate" in qy:
        a = json["variables"]["article"]; ARTICLES["create"] = a
        if a["handle"] == "clean-silver-at-home" and "taken" not in ARTICLES:
            ARTICLES["taken"] = 1
            return R(200, {"data": {"articleCreate": {"article": None, "userErrors": [{"code": "TAKEN", "field": ["handle"], "message": "Handle has already been taken"}]}}})
        return R(200, {"data": {"articleCreate": {"article": {"id": "gid://shopify/Article/9", "handle": a["handle"], "blog": {"handle": "journal"}}, "userErrors": []}}})
    if "articleUpdate" in qy:
        ARTICLES.setdefault("update", []).append(json["variables"])
        return R(200, {"data": {"articleUpdate": {"article": {"id": "gid://shopify/Article/9", "handle": "clean-silver-at-home-2", "isPublished": True, "blog": {"handle": "journal"}}, "userErrors": []}}})
    raise AssertionError(qy)
shopify.requests.post = fake_post
bad = signed({"code": "c0de", "shop": "gargi-store.myshopify.com", "state": state, "timestamp": "1"}, secret="wrong")
r = cl.get("/shopify/callback?" + urlencode(bad)); assert "err=" in r.headers["Location"]          # forged signature refused
r = cl.post(f"/c/{A}/publishing", data={"csrf": "tok", "what": "connect", "shop": "gargi-store.myshopify.com"})
state = parse_qs(urlparse(r.headers["Location"]).query)["state"][0]
r = cl.get("/shopify/callback?" + urlencode(signed({"code": "c0de", "shop": "gargi-store.myshopify.com", "state": state, "timestamp": "1"})))
assert "Connected+gargi-store.myshopify.com" in r.headers["Location"], r.headers["Location"]
with connect() as conn:
    row = conn.execute("SELECT status, blog_id, access_token_enc, primary_domain, author_name FROM cms_connections WHERE client_id=%s", [A]).fetchone()
assert row[0] == "active" and row[1] == "gid://shopify/Blog/1" and b"tok-1" not in bytes(row[2]) and row[3] == "https://gargi.shop" and row[4] == "Gargi by PNG"
r = cl.post(f"/c/{A}/publishing", data={"csrf": "tok", "what": "settings", "blog_id": "gid://shopify/Blog/2", "author": "Meera Joshi, Gargi gemmologist"})
page = cl.get(f"/c/{A}/publishing").get_data(as_text=True); assert "Connected" in page and "Journal (/blogs/journal)" in page
print("shopify connect: shop address checked, authorize URL with blog scopes only, forged callback refused, expiring tokens stored encrypted, blogs loaded")

# ---------------------------------------------------------------- publish, refresh, update, hide
r = cl.post(f"/c/{A}/blogs/{did}", data={"csrf": "tok", "what": "publish"})
assert "Published" in r.headers["Location"] and "journal%2Fclean-silver-at-home-2" in r.headers["Location"], r.headers["Location"]
a = ARTICLES["create"]
assert a["blogId"] == "gid://shopify/Blog/2" and a["isPublished"] is True and a["author"] == {"name": "Meera Joshi, Gargi gemmologist"}
assert {m["key"]: m["value"] for m in a["metafields"]} == {"title_tag": "Clean silver at home: 5 safe ways", "description_tag": "x" * 150}
assert '"@type": "FAQPage"' in a["body"] and "<h2>Frequently asked questions</h2>" in a["body"] and a["tags"] == ["silver care", "guides"]
with connect() as conn:
    st, url, ext, pb = conn.execute("SELECT status, published_url, external_id, published_by FROM content_drafts WHERE draft_id=%s", [did]).fetchone()
    assert (st, url, ext, pb) == ("published", "https://gargi.shop/blogs/journal/clean-silver-at-home-2", "gid://shopify/Article/9", "seo@hovers.in")
    assert conn.execute("SELECT page_type FROM pages WHERE url=%s", [url]).fetchone()[0] == "blog"         # joins the site's pages
    conn.execute("UPDATE cms_connections SET expires_at = now() - interval '1 minute' WHERE client_id=%s", [A])
calls.clear()
form["what"] = "update"; form["title"] = "How to clean silver jewellery at home safely"
r = cl.post(f"/c/{A}/blogs/{did}", data=form); assert "Updated+on+the+store" in r.headers["Location"]
assert calls[0][1]["grant_type"] == "refresh_token" and calls[1][3]["X-Shopify-Access-Token"] == "tok-2"   # refreshed first
assert ARTICLES["update"][-1]["article"]["title"] == "How to clean silver jewellery at home safely"
r = cl.post(f"/c/{A}/blogs/{did}", data={"csrf": "tok", "what": "unpublish"}); assert "Hidden" in r.headers["Location"]
assert ARTICLES["update"][-1]["article"] == {"isPublished": False}
assert "View live" not in cl.get(f"/c/{A}/blogs?tab=published").get_data(as_text=True)
print("publish: chosen blog and author, SEO title and description, FAQ schema, taken handle retried, live URL saved; expired token refreshed; update and hide")

# ---------------------------------------------------------------- who may do what
import portal
with connect() as conn:
    conn.execute("INSERT INTO client_viewers (client_id, email, role) VALUES (%s,'owner@gargi.shop','owner'), (%s,'viewer@gargi.shop','viewer')", [A, A])
    d2 = str(conn.execute("""INSERT INTO content_drafts (client_id, status, target_keyword, title, body_html, slug, preview_token)
                             VALUES (%s,'draft','silver care tips','Silver care tips','<p>x</p>','silver-care-tips','tokentokentokentoken1') RETURNING draft_id""", [A]).fetchone()[0])
    conn.execute("INSERT INTO content_drafts (client_id, status, target_keyword, title, body_html) VALUES (%s,'draft','other brand post','Other brand post','<p>y</p>')", [Bc])
def as_viewer(email):
    c = webapp.app.test_client()
    with c.session_transaction() as s: s["viewer_email"] = email; s["viewer_clients"] = [A]; s["csrf"] = "tok"
    return c
v = as_viewer("viewer@gargi.shop")
page = v.get(f"/c/{A}/blogs").get_data(as_text=True)
assert "Silver care tips" in page and "Find topics and write drafts" not in page and "Other brand post" not in page and ">Publishing<" not in page
assert v.post(f"/c/{A}/blogs/{d2}", data={"csrf": "tok", "what": "approve"}).headers["Location"].endswith("/portal")
assert v.get(f"/c/{A}/publishing").headers["Location"].endswith("/portal")
o = as_viewer("owner@gargi.shop")
page = o.get(f"/c/{A}/blogs").get_data(as_text=True); assert "Generate drafts" in page and ">Publishing<" in page
r = o.post(f"/c/{A}/blogs/{d2}", data={"csrf": "tok", "what": "approve"}); assert "Approved" in r.headers["Location"]
assert o.get(f"/c/{A}/publishing").status_code == 200 and "Save this store's app" not in o.get(f"/c/{A}/publishing").get_data(as_text=True)
assert o.post(f"/c/{A}/publishing", data={"csrf": "tok", "what": "app", "app_client_id": "x", "app_secret": "y"}).status_code == 403
assert o.get(f"/c/{Bc}/blogs").headers["Location"].endswith("/portal")
with connect() as conn: assert conn.execute("SELECT status, reviewed_by FROM content_drafts WHERE draft_id=%s", [d2]).fetchone() == ("approved", "owner@gargi.shop")
print("access: viewers read; the brand's owner reviews, approves, publishes and connects the store; only Hovers sets app keys; other brands stay hidden")
