"""The six Semrush-era features with no real API calls: technical crawl (fake site) and its fix tracker, rankings from
Search Console, content ideas, backlinks, keyword gap, Site Audit import, Position Tracking, SERP features, and the
per-feature and lifetime caps. Needs DATABASE_URL (aeo_app) on a database with migration 007."""
import os, sys, json, datetime as dt
os.environ["SEMRUSH_TOTAL_CAP"] = "10000000"              # this test checks feature caps; the lifetime cap has its own test
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo")]
from common import connect
import crawler, rank, semrush, semrush_suite, semrush_projects

# ---------------------------------------------------------------- fake site for the crawler
D = "https://www.crawl-test.shop"
def page(title, desc="", h1s=("Heading",), links=(), imgs=(), robots="", canonical=None, extra=""):
    return ("<html><head>" + (f"<title>{title}</title>" if title else "") + (f'<meta name="description" content="{desc}">' if desc else "")
            + (f'<meta name="robots" content="{robots}">' if robots else "") + (f'<link rel="canonical" href="{canonical}">' if canonical else "")
            + extra + "</head><body>" + "".join(f"<h1>{h}</h1>" for h in h1s) + "".join(f'<a href="{l}">x</a>' for l in links)
            + "".join(f'<img src="/i{i}.jpg"{a}>' for i, a in enumerate(imgs)) + "</body></html>")
SITE = {
    D + "/": (200, page("Gargi silver jewellery", "Shop silver", links=["/collections/a", "/collections/b", "/products/old", "/pages/gone", "/collections/a#top"])),
    D + "/collections/a": (200, page("Silver earrings", "Earrings", links=["/", "/collections/b"], imgs=[' alt="Jhumka"'])),
    D + "/collections/b": (200, page("Silver earrings", "", links=["/"], canonical=D + "/collections/b",
                                     extra='<link rel="alternate" hreflang="en-us" href="/pages/gone"><script src="http://cdn.x.com/a.js"></script>')),
    D + "/products/old": (301, "", D + "/products/mid"),
    D + "/products/mid": (301, "", D + "/products/new"),
    D + "/products/new": (200, page("Anklet", "Anklet", h1s=("One", "Two"), imgs=["", ' alt=""'])),
    D + "/pages/gone": (404, "not found"),
    D + "/pages/noidx": (200, page("Hidden", "Hidden", robots="noindex,follow")),
    D + "/pages/orphan": (200, page("Orphan", "Orphan", h1s=())),
}
class R:
    def __init__(s, code, text="", loc=None, js=None):
        s.status_code, s.text, s.headers, s._js = code, text if js is None else json.dumps(js), ({"Location": loc} if loc else {}), js
    def json(s): return s._js
class FakeSite:
    def __init__(s, site): s.site, s.calls = site, []
    def get(s, url, headers=None, timeout=None, allow_redirects=True):
        s.calls.append(url); v = s.site.get(url, (404, ""))
        return R(v[0], v[1], v[2] if len(v) > 2 else None)

with connect() as conn:
    conn.execute("DELETE FROM clients WHERE domain IN ('crawl-test.shop','suite-test.shop')")
    cid = conn.execute("INSERT INTO clients (name, domain) VALUES ('CrawlTest','crawl-test.shop') RETURNING client_id").fetchone()[0]
    for u, t in [("/", "home"), ("/collections/a", "collection"), ("/collections/b", "collection"), ("/pages/noidx", "static"), ("/pages/orphan", "static")]:
        conn.execute("INSERT INTO pages (client_id, url, page_type) VALUES (%s,%s,%s)", [cid, D + (u if u != "/" else "/"), t])
    fake = FakeSite(SITE)
    res = crawler.run(conn, cid, session=fake, pause=0)
    issues = dict(conn.execute("SELECT issue_code, pages FROM tech_issues WHERE client_id=%s", [cid]).fetchall())
    expect = {"page_4xx": 1, "broken_internal_link": 1, "redirect_chain": 1, "link_to_redirect": 1, "duplicate_title": 2,
              "missing_description": 1, "multiple_h1": 1, "missing_h1": 1, "img_missing_alt": 1, "noindex_in_sitemap": 1,
              "orphan_page": 2, "mixed_content": 1, "hreflang_broken": 1}
    assert issues == expect, issues                               # noidx and orphan are in the sitemap but not linked: both orphans
    assert res["pages"] == 7 and res["error"] == 2 and res["health"] == 71.4, res    # 5 of 7 URLs free of errors (redirect counted once)
    assert fake.calls.count(D + "/collections/a") == 1            # "#top" link is the same page
    sev = dict(conn.execute("SELECT issue_code, severity FROM tech_issues WHERE client_id=%s", [cid]).fetchall())
    assert sev["broken_internal_link"] == "error" and sev["orphan_page"] == "notice"
    due = conn.execute("SELECT due_date - current_date FROM tech_issues WHERE client_id=%s AND issue_code='page_4xx'", [cid]).fetchone()[0]
    assert due == 7
    # The team marks two fixed; one really is fixed, one is not. The re-crawl verifies.
    conn.execute("UPDATE tech_issues SET status='fixed', owner='Dev team' WHERE client_id=%s AND issue_code IN ('missing_description','page_4xx')", [cid])
    conn.execute("UPDATE tech_issues SET status='in_progress' WHERE client_id=%s AND issue_code='multiple_h1'", [cid])
    SITE[D + "/collections/b"] = (200, page("Silver earrings b", "Now has one", links=["/"]))
    SITE[D + "/products/new"] = (200, page("Anklet", "Anklet", imgs=[' alt="a"']))
    crawler.run(conn, cid, session=FakeSite(SITE), pause=0)
    st = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT issue_code, status, pages FROM tech_issues WHERE client_id=%s", [cid])}
    assert st["missing_description"] == ("verified", 0), st      # fixed and gone: verified
    assert st["page_4xx"] == ("open", 1), st                      # marked fixed but still broken: reopened
    assert st["multiple_h1"] == ("in_progress", 0) and st["duplicate_title"] == ("open", 0), st
    assert conn.execute("SELECT owner FROM tech_issues WHERE client_id=%s AND issue_code='page_4xx'", [cid]).fetchone()[0] == "Dev team"
    assert conn.execute("SELECT count(*) FROM tech_crawls WHERE client_id=%s", [cid]).fetchone()[0] == 2
    conn.execute("DELETE FROM clients WHERE client_id=%s", [cid])
print("crawl: 13 issue types found on a fake site, severity and due dates set, fixes verified and false fixes reopened on re-crawl")

# ---------------------------------------------------------------- rankings from Search Console
with connect() as conn:
    cid = conn.execute("INSERT INTO clients (name, domain, aliases) VALUES ('SuiteTest','suite-test.shop','{}') RETURNING client_id").fetchone()[0]
    conn.execute("INSERT INTO pages (client_id, url, page_type, primary_keyword, search_volume) VALUES (%s,'https://suite-test.shop/collections/rings','collection','silver rings',9000)", [cid])
    d1, d2 = dt.date.today() - dt.timedelta(days=4), dt.date.today() - dt.timedelta(days=3)
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO gsc_search_daily VALUES (%s,%s,%s,%s,'ind',%s,%s,%s,0,%s,%s)", [
            (cid, d1, "silver rings", "https://suite-test.shop/collections/rings", "MOBILE", 10, 100, 4.0, False),
            (cid, d1, "silver rings", "https://suite-test.shop/products/r1", "DESKTOP", 2, 300, 8.0, False),
            (cid, d2, "Silver Rings", "https://suite-test.shop/collections/rings", "MOBILE", 12, 100, 3.0, False),
            (cid, d1, "toe rings", "https://suite-test.shop/collections/toe", "MOBILE", 5, 50, 9.0, False),
            (cid, d1, "suitetest rings", "https://suite-test.shop/", "MOBILE", 50, 60, 1.0, True)])
    out = rank.sync_client(conn, cid)
    kws = {r[0]: r[1] for r in conn.execute("SELECT keyword, tags FROM rank_keywords WHERE client_id=%s", [cid])}
    assert kws == {"silver rings": ["auto"], "toe rings": ["auto"]}, kws          # collection keyword + top non-branded search; branded left out
    pos = {r[0]: r[1:] for r in conn.execute("""SELECT date, position, url, clicks, impressions FROM rank_positions
                                                WHERE client_id=%s AND keyword='silver rings' AND source='gsc'""", [cid])}
    assert pos[d1] == (7.0, "https://suite-test.shop/collections/rings", 12, 400), pos   # (4x100 + 8x300)/400 = 7.0; page with most clicks
    assert pos[d2][0] == 3.0                                                       # query match ignores case
    assert rank.sync_client(conn, cid).startswith("3 daily positions") and "started" not in rank.sync_client(conn, cid)
print("rankings: tracked keywords seeded from collections and non-branded searches; daily impression-weighted position from Search Console")

# ---------------------------------------------------------------- Semrush features with a fake API
class FakeSemrush:
    def __init__(s): s.urls = []
    def get(s, url, timeout=None):
        s.urls.append(url)
        if "type=phrase_questions" in url:
            return R(200, "Keyword;Search Volume;Keyword Difficulty;Intents;SERP Features\nhow to clean silver rings;2400;22;1;11,21,52\nare silver rings good;880;18;1;21\n")
        if "type=phrase_related" in url:
            return R(200, "Keyword;Search Volume;Keyword Difficulty;Intents;SERP Features\nsilver rings;9000;40;3;5,34\nsterling silver rings for women;3600;35;3;34,16\n")
        if "type=backlinks_overview" in url:
            dom = url.split("target=")[1].split("&")[0]
            return R(200, "ascore;total;domains_num;follows_num;nofollows_num\n" + {"suite-test.shop": "21;3400;180;2500;900", "rival.in": "38;90000;1500;70000;20000"}.get(dom, "10;100;10;80;20") + "\n")
        if "type=backlinks_refdomains" in url:
            return R(200, "domain_ascore;domain;backlinks_num;first_seen;last_seen\n55;vogue.in;3;1700000000;1760000000\n12;blog.example.com;40;1710000000;1760000000\n")
        if "type=backlinks_matrix" in url:
            assert "targets%5B%5D=rival.in" in url and "targets%5B%5D=suite-test.shop" in url, url
            return R(200, "domain;domain_ascore;matches_num;backlinks_num_0;backlinks_num_1\nelle.in;60;1;4;0\nalready.in;30;2;2;5\nnone.in;20;0;0;0\n")
        if "type=domain_domains" in url:
            return R(200, "Keyword;rival.in;suite-test.shop;Search Volume;CPC;Competition\nsilver chain for men;4;0;14800;0.3;1\n")
        if "type=domain_organic" in url:
            return R(200, "Keyword;Position;Previous Position;Search Volume;CPC;Url;Traffic (%);Competition;Keyword Difficulty;Intents;Keywords SERP Features;SERP Features\n"
                          "silver rings;6;8;9000;0.4;https://suite-test.shop/collections/rings;5.5;1;40;3;5,11,52;5\n")
        return R(200, "ERROR 50 :: NOTHING FOUND")
    def request(s, method, url, json=None, timeout=None):
        s.urls.append(url)
        if "siteaudit/info" in url:
            return R(200, js={"pages_crawled": 950, "errors": 120, "warnings": 400, "notices": 900, "defects": {"4": 30, "105": 12, "212": 300},
                              "current_snapshot": {"snapshot_id": "snap1", "quality": {"value": 87}}})
        if "siteaudit/meta/issues" in url:
            return R(200, js={"issues": [{"id": 4, "title": "Broken internal links"}, {"id": 105, "title": "Duplicate meta descriptions"}, {"id": 212, "title": "Orphaned pages"}]})
        if "/issue/4" in url:
            return R(200, js={"data": [{"source_url": "https://suite-test.shop/a", "target_url": "https://suite-test.shop/gone"}]})
        if "tracking" in url:
            assert "url=%2A.suite-test.shop%2F%2A" in url, url
            return R(200, js={"total": 2, "data": {"0": {"Ph": "silver rings", "Dt": {"20261110": {"*.suite-test.shop/*": 5}, "20261111": {"*.suite-test.shop/*": 4}},
                                                       "Lu": {"*.suite-test.shop/*": {"20261111": "https://suite-test.shop/collections/rings"}}},
                                                 "1": {"Ph": "toe rings", "Dt": {"20261111": {"*.suite-test.shop/*": 0}}}}})
        return R(404, "not found")

def units(conn, feature): return semrush.feature_units(conn, feature)

with connect() as conn:
    conn.execute("INSERT INTO competitors (client_id, name, domain, tracked, common_keywords) VALUES (%s,'Rival','rival.in',true,50)", [cid])
    f = FakeSemrush()
    assert semrush_suite.estimate("ideas", per_seed=10, seeds=1) == 800
    out = semrush_suite.sync_ideas(conn, cid, "KEY", seeds=["silver rings"], per_seed=10, max_units=800, session=f)
    ideas = {r[0]: r[1:] for r in conn.execute("SELECT keyword, kind, search_volume, serp_features FROM keyword_ideas WHERE client_id=%s", [cid])}
    assert set(ideas) == {"how to clean silver rings", "are silver rings good", "sterling silver rings for women"}, ideas   # the seed itself left out
    assert ideas["how to clean silver rings"] == ("question", 2400, "11,21,52")
    assert units(conn, "ideas") == 40 * 4, units(conn, "ideas")                    # charged per line returned, not per line asked for
    assert "KEY" not in out
    # per-feature cap: 100 left for ideas, a 10-line request (400 worst case) is refused before any call
    os.environ["SEMRUSH_CAP_IDEAS"] = str(160 + 100)
    n_calls = len(f.urls)
    assert "stopped" in semrush_suite.sync_ideas(conn, cid, "KEY", seeds=["anklets"], session=f) and len(f.urls) == n_calls
    del os.environ["SEMRUSH_CAP_IDEAS"]

    out = semrush_suite.sync_backlinks(conn, cid, "KEY", competitors=3, refdomains=20, gap=30, max_units=2400, session=f)
    ov = dict(conn.execute("SELECT domain, ascore FROM backlink_overview WHERE client_id=%s", [cid]).fetchall())
    assert ov == {"suite-test.shop": 21, "rival.in": 38}, ov
    rd = conn.execute("SELECT domain, first_seen FROM backlink_refdomains WHERE client_id=%s ORDER BY ascore DESC", [cid]).fetchall()
    assert rd[0][0] == "vogue.in" and rd[0][1] == dt.date(2023, 11, 14), rd
    gap = conn.execute("SELECT domain, links_to FROM backlink_gap WHERE client_id=%s", [cid]).fetchall()
    assert gap == [("elle.in", {"rival.in": 4})], gap                             # already linking to the client, or to nobody: left out
    assert units(conn, "backlinks") == 40 * 2 + 40 * 2 + 40 * 3, units(conn, "backlinks")

    assert "1 untapped" in semrush_suite.sync_gap(conn, cid, "KEY", lines=30, max_units=2400, session=f)
    assert units(conn, "gap") == 80

    msg = semrush.sync_client(conn, cid, "KEY", n_keywords=20, n_competitors=0, max_units=200, session=f, nonbranded=False)
    sf = conn.execute("SELECT serp_features, serp_features_owned FROM semrush_keywords WHERE client_id=%s AND keyword='silver rings'", [cid]).fetchone()
    assert sf == ("5,11,52", "5"), sf

    try: semrush_projects.import_audit(conn, cid, "KEY", session=f); raise SystemExit("should need a project id")
    except ValueError: pass
    conn.execute("UPDATE clients SET semrush_project_id='777' WHERE client_id=%s", [cid])
    out = semrush_projects.import_audit(conn, cid, "KEY", issue_pages=5, max_units=700, session=f)
    si = {r[0]: r[1:] for r in conn.execute("SELECT issue_code, title, severity, pages FROM tech_issues WHERE client_id=%s", [cid])}
    assert si == {"semrush:4": ("Broken internal links", "error", 30), "semrush:105": ("Duplicate meta descriptions", "warning", 12),
                  "semrush:212": ("Orphaned pages", "notice", 300)}, si
    assert conn.execute("SELECT health_score FROM tech_crawls WHERE client_id=%s AND source='semrush'", [cid]).fetchone()[0] == 87
    assert conn.execute("SELECT count(*) FROM tech_issue_pages WHERE client_id=%s AND issue_code='semrush:4'", [cid]).fetchone()[0] == 1
    assert units(conn, "audit") == 300, units(conn, "audit")                       # info + issue names + pages for the one error type
    assert not any("snapshot?" in u or u.endswith("/siteaudit/snapshot") for u in f.urls)   # the 10,000-unit report is never called

    out = semrush_projects.import_positions(conn, cid, "KEY", keywords=20, max_units=2000, session=f, today=dt.date(2026, 11, 12))
    sp = conn.execute("SELECT keyword, date, position, url FROM rank_positions WHERE client_id=%s AND source='semrush' ORDER BY keyword, date", [cid]).fetchall()
    assert sp[:2] == [("silver rings", dt.date(2026, 11, 10), 5, None), ("silver rings", dt.date(2026, 11, 11), 4, "https://suite-test.shop/collections/rings")], sp
    assert sp[2] == ("toe rings", dt.date(2026, 11, 11), None, None)              # 0 means not in the top 100
    assert units(conn, "rank") == 200                                               # 2 keywords returned x 100
    print("semrush:", out)
    conn.execute("DELETE FROM clients WHERE client_id=%s", [cid])
print("semrush suite: ideas, backlinks and prospects, gap, SERP features, Site Audit import and Position Tracking read; per-line billing logged by feature; feature cap refuses before calling")
