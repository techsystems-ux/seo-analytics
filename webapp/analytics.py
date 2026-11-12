"""Read-side queries for the dashboard screens. Every function takes an open connection and a client_id and returns
plain dicts, so screens stay thin and the numbers can be tested without a browser."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "seo"))

BANDS = [(80, "Leader"), (60, "Competitive"), (40, "Emerging"), (0, "Not shortlisted")]
def band(s): return None if s is None else next(n for f, n in BANDS if float(s) >= f)

def pct_change(now, before):
    if not before: return None
    return round(100 * (float(now or 0) - float(before)) / float(before), 1)

def periods(conn, client_id, days=28):
    """Two back-to-back windows ending on the last day Search Console has data for."""
    last = conn.execute("SELECT max(date) FROM gsc_search_daily WHERE client_id=%s", [client_id]).fetchone()[0]
    last = last or dt.date.today() - dt.timedelta(days=3)
    cur = (last - dt.timedelta(days=days - 1), last)
    prev = (cur[0] - dt.timedelta(days=days), cur[0] - dt.timedelta(days=1))
    return cur, prev

def rows(cur, cols):
    return [dict(zip(cols, r)) for r in cur.fetchall()]

# ------------------------------------------------------------------ search
def search_totals(conn, client_id, days=28):
    cur, prev = periods(conn, client_id, days)
    q = """SELECT coalesce(sum(clicks),0), coalesce(sum(impressions),0),
                  coalesce(sum(clicks) FILTER (WHERE NOT is_branded),0), coalesce(sum(impressions) FILTER (WHERE NOT is_branded),0),
                  sum(position*impressions)/nullif(sum(impressions),0)
           FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s"""
    a = conn.execute(q, [client_id, *cur]).fetchone(); b = conn.execute(q, [client_id, *prev]).fetchone()
    tot = conn.execute("SELECT coalesce(sum(clicks),0), coalesce(sum(impressions),0) FROM gsc_totals_daily WHERE client_id=%s AND date BETWEEN %s AND %s",
                       [client_id, *cur]).fetchone()
    return {"period": cur, "clicks": tot[0] or a[0], "impressions": tot[1] or a[1], "nb_clicks": a[2], "nb_impressions": a[3],
            "position": round(float(a[4]), 1) if a[4] else None,
            "nb_clicks_change": pct_change(a[2], b[2]), "clicks_change": pct_change(a[0], b[0])}

def daily_clicks(conn, client_id, days=90):
    return rows(conn.execute("""SELECT date, sum(clicks) FILTER (WHERE NOT is_branded) AS nonbranded, sum(clicks) FILTER (WHERE is_branded) AS branded
                                FROM gsc_search_daily WHERE client_id=%s AND date > (SELECT max(date) FROM gsc_search_daily WHERE client_id=%s) - %s
                                GROUP BY date ORDER BY date""", [client_id, client_id, days]), ["date", "nonbranded", "branded"])

def top_queries(conn, client_id, branded=None, limit=50, days=28):
    cur, prev = periods(conn, client_id, days)
    flt = "" if branded is None else (" AND is_branded" if branded else " AND NOT is_branded")
    return rows(conn.execute(f"""WITH a AS (SELECT query, sum(clicks) c, sum(impressions) i, sum(position*impressions)/nullif(sum(impressions),0) p
                                             FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s{flt} GROUP BY query),
                                      b AS (SELECT query, sum(position*impressions)/nullif(sum(impressions),0) p, sum(clicks) c
                                             FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s{flt} GROUP BY query)
                                 SELECT a.query, a.c, a.i, round(100.0*a.c/nullif(a.i,0),1), round(a.p,1), round(b.p - a.p, 1), b.c
                                 FROM a LEFT JOIN b USING (query) ORDER BY a.c DESC, a.i DESC LIMIT %s""",
                             [client_id, *cur, client_id, *prev, limit]),
                ["query", "clicks", "impressions", "ctr", "position", "position_gain", "prev_clicks"])

def top_pages(conn, client_id, limit=50, days=28):
    cur, _ = periods(conn, client_id, days)
    return rows(conn.execute("""SELECT page, sum(clicks), sum(impressions), round(100.0*sum(clicks)/nullif(sum(impressions),0),1),
                                       round(sum(position*impressions)/nullif(sum(impressions),0),1),
                                       (array_agg(query ORDER BY clicks DESC))[1]
                                FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s AND NOT is_branded
                                GROUP BY page ORDER BY 2 DESC LIMIT %s""", [client_id, *cur, limit]),
                ["page", "clicks", "impressions", "ctr", "position", "top_query"])

# ------------------------------------------------------------------ revenue
def organic_revenue(conn, client_id, days=28):
    end = conn.execute("SELECT max(date) FROM ga4_daily WHERE client_id=%s", [client_id]).fetchone()[0]
    if not end: return None
    start = end - dt.timedelta(days=days - 1)
    r = conn.execute("""SELECT coalesce(sum(revenue) FILTER (WHERE channel_group='Organic Search'),0), coalesce(sum(revenue),0),
                               coalesce(sum(sessions) FILTER (WHERE channel_group='Organic Search'),0), coalesce(sum(sessions),0),
                               coalesce(sum(transactions) FILTER (WHERE channel_group='Organic Search'),0)
                        FROM ga4_daily WHERE client_id=%s AND date BETWEEN %s AND %s""", [client_id, start, end]).fetchone()
    ai = conn.execute("SELECT coalesce(sum(sessions),0), coalesce(sum(revenue),0) FROM ga4_ai_referrals WHERE client_id=%s AND date BETWEEN %s AND %s",
                      [client_id, start, end]).fetchone()
    return {"period": (start, end), "revenue_share": round(100 * float(r[0]) / float(r[1]), 1) if r[1] else None,
            "session_share": round(100 * r[2] / r[3], 1) if r[3] else None, "organic_orders": r[4],
            "organic_cvr": round(100 * r[4] / r[2], 2) if r[2] else None, "ai_sessions": ai[0], "ai_revenue": float(ai[1])}

def revenue_by_landing(conn, client_id, limit=30, days=28):
    """Organic landing pages ranked by revenue, each with the Search Console query that sends it most clicks."""
    end = conn.execute("SELECT max(date) FROM ga4_organic_landing WHERE client_id=%s", [client_id]).fetchone()[0]
    if not end: return []
    start = end - dt.timedelta(days=days - 1)
    total = conn.execute("SELECT coalesce(sum(revenue),0) FROM ga4_organic_landing WHERE client_id=%s AND date BETWEEN %s AND %s",
                         [client_id, start, end]).fetchone()[0]
    out = rows(conn.execute("""WITH l AS (SELECT split_part(landing_page,'?',1) lp, sum(sessions) s, sum(transactions) t, sum(revenue) r
                                         FROM ga4_organic_landing WHERE client_id=%s AND date BETWEEN %s AND %s GROUP BY 1),
                                    q AS (SELECT regexp_replace(page,'^https?://[^/]+','') lp, query, sum(clicks) c,
                                                 row_number() OVER (PARTITION BY regexp_replace(page,'^https?://[^/]+','') ORDER BY sum(clicks) DESC) n
                                          FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s AND NOT is_branded GROUP BY 1,2)
                               SELECT l.lp, l.s, l.t, l.r, q.query, q.c FROM l LEFT JOIN q ON q.lp = l.lp AND q.n = 1
                               ORDER BY l.r DESC NULLS LAST, l.s DESC LIMIT %s""", [client_id, start, end, client_id, start, end, limit]),
               ["page", "sessions", "orders", "revenue", "top_query", "query_clicks"])
    for o in out:
        o["revenue_share"] = round(100 * float(o["revenue"] or 0) / float(total), 1) if total else None
        o["cvr"] = round(100 * (o["orders"] or 0) / o["sessions"], 2) if o["sessions"] else None
    return out

# ------------------------------------------------------------------ AI visibility
def visibility(conn, client_id, engine="chatgpt"):
    last = conn.execute("SELECT max(date) FROM scores_daily WHERE client_id=%s AND engine=%s", [client_id, engine]).fetchone()[0]
    if not last: return None
    brands = rows(conn.execute("""SELECT brand, visibility_score, mention_rate, citation_rate, share_of_voice, avg_position, sentiment, samples
                                  FROM scores_daily WHERE client_id=%s AND engine=%s AND date=%s ORDER BY visibility_score DESC""",
                               [client_id, engine, last]), ["brand", "score", "mention_rate", "citation_rate", "sov", "avg_position", "sentiment", "samples"])
    trend = rows(conn.execute("""SELECT date, visibility_score FROM scores_daily WHERE client_id=%s AND engine=%s AND brand=''
                                 ORDER BY date DESC LIMIT 60""", [client_id, engine]), ["date", "score"])[::-1]
    you = next((b for b in brands if b["brand"] == ""), None)
    prev = trend[-8]["score"] if len(trend) >= 8 else None
    return {"date": last, "you": you, "band": band(you["score"]) if you else None, "brands": brands, "trend": trend,
            "change_7d": round(float(you["score"]) - float(prev), 1) if you and prev is not None else None}

def prompt_results(conn, client_id, cycle=None, engine="chatgpt"):
    cycle = cycle or conn.execute("SELECT max(cycle) FROM runs WHERE client_id=%s AND engine=%s AND status='ok'", [client_id, engine]).fetchone()[0]
    out = rows(conn.execute("""SELECT p.prompt_id, p.text, p.funnel_stage, r.run_id, a.answer_id,
                                      (SELECT m.rank_in_answer FROM mentions m WHERE m.answer_id=a.answer_id AND m.is_client LIMIT 1),
                                      (SELECT m.sentiment FROM mentions m WHERE m.answer_id=a.answer_id AND m.is_client LIMIT 1),
                                      EXISTS (SELECT 1 FROM citations c WHERE c.answer_id=a.answer_id AND c.cited AND c.is_client),
                                      (SELECT string_agg(m.brand, ', ' ORDER BY m.rank_in_answer) FROM mentions m
                                        WHERE m.answer_id=a.answer_id AND NOT m.is_client AND m.rank_in_answer IS NOT NULL),
                                      r.status, r.error
                               FROM prompts p LEFT JOIN runs r ON r.prompt_id=p.prompt_id AND r.cycle=%s AND r.engine=%s AND r.sample_no=1
                               LEFT JOIN answers a ON a.run_id=r.run_id
                               WHERE p.client_id=%s AND p.approved AND p.active ORDER BY p.funnel_stage, p.text""",
                            [cycle, engine, client_id]),
               ["prompt_id", "text", "stage", "run_id", "answer_id", "rank", "sentiment", "cited", "others", "status", "error"])
    return cycle, out

def answer_detail(conn, client_id, answer_id):
    a = conn.execute("""SELECT a.full_text, a.engine_version, r.run_at, p.text FROM answers a JOIN runs r USING (run_id) JOIN prompts p USING (prompt_id)
                        WHERE a.answer_id=%s AND a.client_id=%s""", [answer_id, client_id]).fetchone()
    if not a: return None
    return {"text": a[0], "model": a[1], "run_at": a[2], "prompt": a[3],
            "mentions": rows(conn.execute("""SELECT brand, is_client, rank_in_answer, sentiment, recommended FROM mentions WHERE answer_id=%s
                                             ORDER BY rank_in_answer NULLS LAST, brand""", [answer_id]), ["brand", "is_client", "rank", "sentiment", "recommended"]),
            "citations": rows(conn.execute("""SELECT url, domain, title, source_type, cited, is_client FROM citations WHERE answer_id=%s
                                              ORDER BY cited DESC, domain""", [answer_id]), ["url", "domain", "title", "type", "cited", "is_client"])}

def top_cited_domains(conn, client_id, days=30, limit=15):
    return rows(conn.execute("""SELECT c.domain, c.source_type, count(DISTINCT c.answer_id), bool_or(c.is_client)
                                FROM citations c JOIN answers a USING (answer_id) JOIN runs r USING (run_id)
                                WHERE c.client_id=%s AND c.cited AND r.run_at > now() - make_interval(days => %s)
                                GROUP BY 1,2 ORDER BY 3 DESC LIMIT %s""", [client_id, days, limit]), ["domain", "type", "answers", "is_client"])

def untracked_brands(conn, client_id, days=30, limit=10):
    """Brands the engine names that we do not track yet: candidates for the competitor list."""
    return rows(conn.execute("""SELECT m.brand, count(*) FROM mentions m JOIN answers a USING (answer_id) JOIN runs r USING (run_id)
                                WHERE m.client_id=%s AND m.rank_in_answer IS NULL AND NOT m.is_client AND r.run_at > now() - make_interval(days => %s)
                                  AND NOT EXISTS (SELECT 1 FROM competitors k WHERE k.client_id=m.client_id AND k.tracked AND lower(k.name)=lower(m.brand))
                                GROUP BY 1 ORDER BY 2 DESC LIMIT %s""", [client_id, days, limit]), ["brand", "answers"])

# ------------------------------------------------------------------ competitors (Semrush)
def competitors(conn, client_id):
    return rows(conn.execute("""SELECT competitor_id, name, domain, source, tracked, relevance, common_keywords, organic_keywords, organic_traffic
                                FROM competitors WHERE client_id=%s ORDER BY tracked DESC, common_keywords DESC NULLS LAST, name""", [client_id]),
                ["id", "name", "domain", "source", "tracked", "relevance", "common", "keywords", "traffic"])

def keyword_gaps(conn, client_id, limit=50):
    last = conn.execute("SELECT max(pulled_on) FROM keyword_gaps WHERE client_id=%s", [client_id]).fetchone()[0]
    if not last: return None, []
    return last, rows(conn.execute("""SELECT keyword, search_volume, cpc, competition, positions FROM keyword_gaps
                                      WHERE client_id=%s AND pulled_on=%s ORDER BY search_volume DESC NULLS LAST LIMIT %s""",
                                   [client_id, last, limit]), ["keyword", "volume", "cpc", "competition", "positions"])

def semrush_keywords(conn, client_id, limit=50):
    dom = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    dom = dom.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    last = conn.execute("SELECT max(pulled_on) FROM semrush_keywords WHERE client_id=%s AND domain=%s", [client_id, dom]).fetchone()[0]
    if not last: return None, []
    return last, rows(conn.execute("""SELECT keyword, position, prev_position, search_volume, url, traffic_pct, kd, intent FROM semrush_keywords
                                      WHERE client_id=%s AND domain=%s AND pulled_on=%s ORDER BY traffic_pct DESC NULLS LAST, search_volume DESC LIMIT %s""",
                                   [client_id, dom, last, limit]), ["keyword", "position", "prev", "volume", "url", "traffic_pct", "kd", "intent"])

def semrush_budget(conn):
    """Lifetime Semrush credits used against the hard cap (all brands, all pulls, including seeded data)."""
    import os
    cap = int(os.environ.get("SEMRUSH_TOTAL_CAP", "500"))
    used = conn.execute("SELECT coalesce(sum(units),0) FROM semrush_usage").fetchone()[0]
    return {"cap": cap, "used": used, "left": max(0, cap - used)}

def semrush_units(conn, client_id=None, days=30):
    return conn.execute("SELECT coalesce(sum(units),0) FROM semrush_usage WHERE called_at > now() - make_interval(days => %s)"
                        + (" AND client_id=%s" if client_id else ""), [days] + ([client_id] if client_id else [])).fetchone()[0]

# ------------------------------------------------------------------ pages and runs
def page_summary(conn, client_id):
    out = rows(conn.execute("""SELECT p.page_id, p.url, p.page_type::text, p.primary_keyword, p.intent,
                                      count(*) FILTER (WHERE c.result='fail'), count(*) FILTER (WHERE c.result='warn'),
                                      count(*) FILTER (WHERE c.result='pass'), max(c.taken_at)
                               FROM pages p LEFT JOIN page_latest_checks c USING (page_id) WHERE p.client_id=%s
                               GROUP BY p.page_id ORDER BY 6 DESC, 7 DESC, p.url""", [client_id]),
               ["page_id", "url", "type", "keyword", "kw_source", "fails", "warns", "passes", "checked_at"])
    for r in out:
        n = r["fails"] + r["warns"] + r["passes"]
        r["score"] = round(100 * (r["passes"] + 0.5 * r["warns"]) / n) if n else None
    return out

def site_issues(conn, client_id):
    """Most common problems across checked pages: one row per check, pages to fix and to improve."""
    import checks as C
    out = rows(conn.execute("""SELECT check_id, count(*) FILTER (WHERE result='fail'), count(*) FILTER (WHERE result='warn')
                               FROM page_latest_checks WHERE client_id=%s GROUP BY check_id
                               HAVING count(*) FILTER (WHERE result IN ('fail','warn')) > 0
                               ORDER BY 2 DESC, 3 DESC""", [client_id]), ["id", "fails", "warns"])
    groups = {g: label for g, label, _ in C.GROUPS}
    for r in out:
        g, r["name"] = C.info(r["id"]); r["group"] = groups[g]
    return out

def pages_with_issue(conn, client_id, check_id):
    return {str(r[0]) for r in conn.execute("""SELECT page_id FROM page_latest_checks WHERE client_id=%s AND check_id=%s
                                               AND result IN ('fail','warn')""", [client_id, check_id])}

def page_audit(conn, client_id, page_id):
    """One page: what Google shows, and every check grouped as on the SOP (fix, then improve, then pass)."""
    import checks as C
    snap = conn.execute("""SELECT content, taken_at, source FROM page_snapshots WHERE client_id=%s AND page_id=%s
                           ORDER BY taken_at DESC LIMIT 1""", [client_id, page_id]).fetchone()
    found = rows(conn.execute("""SELECT check_id, result, found, message FROM page_latest_checks WHERE client_id=%s AND page_id=%s""",
                              [client_id, page_id]), ["id", "result", "found", "message"])
    order = {"fail": 0, "warn": 1, "pass": 2, "na": 3}
    groups = []
    for g, label, about in C.GROUPS:
        items = []
        for f in found:
            grp, name = C.info(f["id"])
            if grp == g: items.append({**f, "name": name})
        if not items: continue
        items.sort(key=lambda x: (order[x["result"]], x["id"]))
        groups.append({"key": g, "label": label, "about": about, "checks": items,
                       "fails": sum(i["result"] == "fail" for i in items), "warns": sum(i["result"] == "warn" for i in items),
                       "passes": sum(i["result"] == "pass" for i in items)})
    c = (snap[0] if snap else None) or {}
    return {"checked_at": snap[1] if snap else None, "source": snap[2] if snap else None, "groups": groups,
            "title": c.get("title", ""), "description": c.get("description", ""), "h1": (c.get("h1s") or [""])[0],
            "words": c.get("word_count"), "url": c.get("url")}

def last_runs(conn, client_id):
    return rows(conn.execute("""SELECT DISTINCT ON (collector) collector, status, started_at, finished_at, error FROM collector_runs
                                WHERE client_id=%s ORDER BY collector, started_at DESC""", [client_id]), ["collector", "status", "started", "finished", "note"])
