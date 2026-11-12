"""Tests with a fake HTTP session. Fixtures are SYNTHETIC, shaped like the documented GSC / GA4 responses."""
import os, sys, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))
import psycopg, gsc, ga4
from common import connect

class Resp:
    def __init__(self, j, code=200): self._j, self.status_code, self.text = j, code, str(j)
    def json(self): return self._j
class FakeSession:
    def __init__(self, pages): self.pages, self.calls = list(pages), []
    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json)); return self.pages.pop(0)

# ---- GSC ----
terms = gsc.brand_terms("Right Kind", "rightkind.in", ["rk protein"])
assert gsc.is_branded("rightkind protein bar", terms) and gsc.is_branded("right kind bars", terms) and gsc.is_branded("RK Protein review", terms)
assert not gsc.is_branded("best protein bar india", terms)
print("branded detection OK")

row = lambda q, clicks: {"keys": ["2026-10-30", q, "https://rightkind.in/p/bar", "ind", "MOBILE"], "clicks": clicks, "impressions": 100, "ctr": clicks / 100, "position": 4.2}
fs = FakeSession([Resp({"rows": [row("a", 1), row("b", 2)]}), Resp({"rows": [row("c", 3)]})])   # row_limit=2 forces a second page
rows = gsc.fetch_all(fs, "sc-domain:rightkind.in", dt.date(2026, 10, 30), dt.date(2026, 10, 30), gsc.DIMS, row_limit=2)
assert len(rows) == 3 and fs.calls[1][1]["startRow"] == 2 and "sc-domain%3Arightkind.in" in fs.calls[0][0], fs.calls
try:
    gsc.fetch_all(FakeSession([Resp({"error": "forbidden"}, 403)]), "x", dt.date.today(), dt.date.today(), gsc.DIMS); raise SystemExit("should have raised")
except RuntimeError as e: assert "403" in str(e)
print("GSC pagination + error handling OK")

# ---- DB round trip, idempotency, isolation ----
with connect() as c:
    c.execute("INSERT INTO clients (client_id,name,domain,aliases) VALUES ('33333333-3333-3333-3333-333333333333','Right Kind','rightkind.in','{rk protein}') ON CONFLICT DO NOTHING")
    cid = "33333333-3333-3333-3333-333333333333"
    c.execute("DELETE FROM gsc_search_daily WHERE client_id=%s", [cid]); c.execute("DELETE FROM ga4_daily WHERE client_id=%s", [cid]); c.execute("DELETE FROM ga4_ai_referrals WHERE client_id=%s", [cid])
    class Sess:   # drives sync_client end-to-end: detail report then totals report
        def __init__(s, clicks): s.clicks, s.n = clicks, 0
        def post(s, url, json=None, timeout=None):
            s.n += 1
            if json["dimensions"] == ["date"]:
                return Resp({"rows": [{"keys": ["2026-10-30"], "clicks": 10, "impressions": 500, "ctr": 0.02, "position": 6.1}]})
            return Resp({"rows": [row("rightkind protein bar", s.clicks), row("best protein bar india", 1)]})
    n1 = gsc.sync_client(c, Sess(5), cid, "sc-domain:rightkind.in", "Right Kind", "rightkind.in", ["rk protein"])
    n2 = gsc.sync_client(c, Sess(9), cid, "sc-domain:rightkind.in", "Right Kind", "rightkind.in", ["rk protein"])   # re-pull with revised numbers
    cnt, clicks, branded = c.execute("SELECT count(*), max(clicks) FILTER (WHERE is_branded), count(*) FILTER (WHERE is_branded) FROM gsc_search_daily WHERE client_id=%s", [cid]).fetchone()
    assert (n1, n2, cnt, clicks, branded) == (3, 3, 2, 9, 1), (n1, n2, cnt, clicks, branded)   # upsert: no duplicates, latest value wins
    print("GSC upsert idempotent, branded flag stored OK")

    # ---- GA4 ----
    mv = lambda *v: [{"value": str(x)} for x in v]
    main_rep = {"rowCount": 2, "rows": [{"dimensionValues": mv("20261030", "Organic Search"), "metricValues": mv(120, 90, 7, 3, 4500.5)},
                                          {"dimensionValues": mv("20261030", "Direct"), "metricValues": mv(60, 30, 1, 0, 0)}]}
    ai_rep = {"rowCount": 1, "rows": [{"dimensionValues": mv("20261030", "chatgpt.com"), "metricValues": mv(8, 6, 1, 0, 0)}]}
    landing_rep = {"rowCount": 1, "rows": [{"dimensionValues": mv("20261030", "/collections/rings?utm=x"), "metricValues": mv(40, 30, 2, 1, 1500)}]}
    class GSess:
        def __init__(s): s.bodies = []
        def post(s, url, json=None, timeout=None):
            s.bodies.append(json)
            if json["dimensions"][1]["name"] == "landingPagePlusQueryString":
                assert json["dimensionFilter"]["filter"]["stringFilter"]["value"] == "Organic Search"
                return Resp(landing_rep)
            return Resp(ai_rep if "dimensionFilter" in json else main_rep)
    g = GSess(); n = ga4.sync_client(c, g, cid, "123456789"); ga4.sync_client(c, GSess(), cid, "123456789")
    assert n == 4 and any("dimensionFilter" in b for b in g.bodies)
    assert c.execute("SELECT landing_page, revenue FROM ga4_organic_landing WHERE client_id=%s", [cid]).fetchone() == ("/collections/rings?utm=x", 1500)
    assert c.execute("SELECT count(*) FROM ga4_daily WHERE client_id=%s", [cid]).fetchone()[0] == 2
    assert c.execute("SELECT sessions, date FROM ga4_ai_referrals WHERE client_id=%s", [cid]).fetchone() == (8, dt.date(2026, 10, 30))
    print("GA4 parse + AI-referral report + idempotent save OK")

    # tenant isolation on the new tables, as an ordinary client login
    other = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    other.execute("SET app.client_id = '22222222-2222-2222-2222-222222222222'")
    for t in ("gsc_search_daily", "gsc_totals_daily", "ga4_daily", "ga4_ai_referrals", "client_integrations", "collector_runs"):
        assert other.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0, t
    print("tenant isolation OK on all new tables")
