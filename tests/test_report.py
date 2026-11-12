"""Monthly report: numbers computed from stored Search Console and GA4 rows for two months (and the same month last
year), the work log and plan drafted from the tool, an analyst's edits taking over, and a valid .pptx with 10 slides.
Needs DATABASE_URL (aeo_app) on a migrated database. Writes the deck to $REPORT_OUT if set."""
import os, sys, datetime as dt, random
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "webapp")]
from common import connect
import report

assert report.inr(1139819) == "₹11,39,819" and report.inr(939614.4) == "₹9,39,614" and report.inr(512) == "₹512"
assert report.default_month(dt.date(2026, 11, 12)) == dt.date(2026, 10, 1) and report.default_month(dt.date(2026, 11, 2)) == dt.date(2026, 9, 1)
assert report.path_of("https://www.x.shop/collections/rings/?a=1") == "/collections/rings" and report.path_of("https://x.shop/") == "Home page"

OCT, SEP, LY = dt.date(2026, 10, 1), dt.date(2026, 9, 1), dt.date(2025, 10, 1)
rnd = random.Random(3)
with connect() as conn:
    conn.execute("DELETE FROM clients WHERE domain='report-test.shop'")
    cid = conn.execute("INSERT INTO clients (name, domain) VALUES ('Gargi','report-test.shop') RETURNING client_id").fetchone()[0]
    def days(m):
        a, b = report.month_bounds(m); d = a
        while d <= b: yield d; d += dt.timedelta(days=1)
    # Search Console: totals and query/page rows. "silver earrings" climbs into the top 10, "toe rings" drops out.
    Q = {"gargi earrings": (True, 4.0, 3.0, "/collections/earrings"), "silver earrings": (False, 14.0, 6.0, "/collections/earrings"),
         "toe rings": (False, 8.0, 15.0, "/collections/toe-rings"), "silver anklets": (False, 3.0, 2.0, "/collections/anklets"),
         "oxidised jhumka": (False, 25.0, 9.0, "/products/jhumka")}
    with conn.cursor() as cur:
        for m, k in ((SEP, 1), (OCT, 2)):
            for d in days(m):
                cur.execute("INSERT INTO gsc_totals_daily VALUES (%s,%s,%s,%s,%s,%s)", [cid, d, 100 * k, 4000, 0.025 * k, 8.0 - k])
                for q, (br, ps, po, page) in Q.items():
                    pos = ps if m == SEP else po
                    cur.execute("INSERT INTO gsc_search_daily VALUES (%s,%s,%s,%s,'ind','MOBILE',%s,%s,0,%s,%s)",
                                [cid, d, q, "https://report-test.shop" + page, (10 if br else 3) * k, 100, pos, br])
            # GA4: organic and direct, organic landing pages
            for d in days(m):
                cur.execute("INSERT INTO ga4_daily (client_id, date, channel_group, sessions, engaged_sessions, key_events, transactions, revenue, new_users) "
                            "VALUES (%s,%s,'Organic Search',%s,%s,%s,%s,%s,%s)", [cid, d, 50 * k, 40 * k, 20 * k, k, 1000.0 * k, 30 * k])
                cur.execute("INSERT INTO ga4_daily (client_id, date, channel_group, sessions, engaged_sessions, key_events, transactions, revenue, new_users) "
                            "VALUES (%s,%s,'Direct',100,50,10,1,500,20)", [cid, d])
                for page, sh in (("/", 20), ("/collections/earrings", 15), ("/collections/anklets?utm=x", 10 if m == SEP else 25)):
                    cur.execute("INSERT INTO ga4_organic_landing (client_id, date, landing_page, sessions, engaged_sessions, key_events, transactions, revenue) "
                                "VALUES (%s,%s,%s,%s,%s,%s,0,%s)", [cid, d, page, sh * k, sh, sh, 100.0 * k])
        for d in days(LY):
            cur.execute("INSERT INTO ga4_daily (client_id, date, channel_group, sessions, engaged_sessions, key_events, transactions, revenue, new_users) "
                        "VALUES (%s,%s,'Organic Search',80,60,10,1,800,40)", [cid, d])
        cur.execute("INSERT INTO ga4_organic_items VALUES (%s,%s,'Evil eye anklet',40,24000),(%s,%s,'Oxidised jhumka',22,15500)", [cid, OCT, cid, OCT])
    # work recorded in October
    conn.execute("""INSERT INTO tech_issues (client_id, issue_code, source, title, severity, pages, status, updated_at)
                    VALUES (%s,'page_4xx','hovers','Page returns a 4xx error','error',0,'verified','2026-10-14'),
                           (%s,'missing_h1','hovers','No H1 heading','warning',12,'open','2026-10-02')""", [cid, cid])
    conn.execute("""INSERT INTO keyword_ideas (client_id, seed, kind, keyword, search_volume, pulled_on, status, updated_at) VALUES
                    (%s,'silver earrings','question','how to clean silver earrings',880,'2026-10-01','done','2026-10-20'),
                    (%s,'silver anklets','question','which leg to wear anklet',590,'2026-10-01','planned','2026-10-20'),
                    (%s,'silver anklets','related','silver payal',9900,'2026-10-01','new','2026-10-20')""", [cid, cid, cid])

    R = report.compute(conn, cid, OCT)
    rk = R["rank"]
    assert rk["cur"]["top10"] == 4 and rk["prev"]["top10"] == 3, (rk["cur"], rk["prev"])          # +silver earrings, +jhumka, -toe rings
    assert [e[0] for e in rk["entered"]] and {e[0] for e in rk["entered"]} == {"silver earrings", "oxidised jhumka"}, rk["entered"]
    assert [l[0] for l in rk["left"]] == ["toe rings"] and rk["left"][0][1] == 15.0
    assert abs(rk["position"] - 6.0) < 1e-9 and abs(rk["pposition"] - 7.0) < 1e-9
    t, pt = R["search"]["cur"], R["search"]["prev"]
    assert t["clicks"] == 6200 and pt["clicks"] == 3000 and t["impressions"] == 124000
    g, pg, lg = R["traffic"]["cur"], R["traffic"]["prev"], R["traffic"]["ly"]
    assert g["sessions"] == 3100 and pg["sessions"] == 1500 and lg["sessions"] == 2480 and g["new_users"] == 1860
    assert abs(g["share"] - 3100 / 6200) < 1e-9 and abs(g["engagement"] - .8) < 1e-9
    assert R["landing"][0][0] == "/collections/anklets" and R["landing"][0][1] == 25 * 2 * 31, R["landing"]       # query strings merged
    assert R["revenue"]["cur"] == 62000 and R["revenue"]["prev"] == 30000 and R["revenue"]["ly"] == 24800
    assert R["products"][0] == ("Evil eye anklet", 40, 24000.0)
    b = R["branded"]["cur"]
    assert b["b_clicks"] == 20 * 31 and b["n_clicks"] == 6 * 4 * 31 and len(R["branded"]["hist"]) == 2
    assert R["gaining"][0][0] == "/collections/earrings" and R["declining"] == [] or R["declining"][0][3] < 0
    areas = [w[0] for w in R["work"]]
    assert areas == ["Technical fixes", "Content"], areas
    assert "how to clean silver earrings" in R["work"][1][1][0]
    titles = [p["title"] for p in R["plan"]]
    assert titles[0] == "New collection pages" and "silver payal" in R["plan"][0]["bullets"][0] and "Technical fixes" in titles, R["plan"]
    # the analyst edits, round trip through the text format
    wt, pt_ = report.work_text(R["work_auto"]), report.plan_text(R["plan_auto"])
    assert report.parse_work(wt)[0][0] == "Technical fixes" and report.parse_plan(pt_)[0]["title"] == "New collection pages"
    conn.execute("INSERT INTO monthly_reports (client_id, month, work_done, plan, updated_by) VALUES (%s,%s,%s,%s,'seo@hovers.in')",
                 [cid, OCT, "Content | Published 6 festive blogs / Refreshed the Diwali page | Diwali page: 116 clicks", "Five blogs\nGoal: answer questions\n- one\n- two"])
    R2 = report.compute(conn, cid, OCT)
    assert R2["work"] == [("Content", ["Published 6 festive blogs", "Refreshed the Diwali page"], "Diwali page: 116 clicks")]
    assert R2["plan"] == [{"title": "Five blogs", "goal": "answer questions", "bullets": ["one", "two"]}]
    data = report.build(R)
    from pptx import Presentation
    import io
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) == 10
    alltext = " ".join(sh.text_frame.text for sl in prs.slides for sh in sl.shapes if sh.has_text_frame)
    assert "4 keywords rank in the top 10, up from 3" in alltext and "Organic sessions are up 107% on September" in alltext, alltext[:600]
    assert "Organic revenue is up 107% on September" in alltext and "₹62,000" in alltext
    if os.environ.get("REPORT_OUT"): open(os.environ["REPORT_OUT"], "wb").write(data)
    # a month with no data at all still builds
    empty = report.build(report.compute(conn, cid, dt.date(2024, 1, 1)))
    assert len(Presentation(io.BytesIO(empty)).slides) == 10
    conn.execute("DELETE FROM clients WHERE client_id=%s", [cid])
print("report: rankings, search, traffic, revenue (with last year), branded split, URLs, work log and plan from the tool, analyst edits, 10-slide deck")
