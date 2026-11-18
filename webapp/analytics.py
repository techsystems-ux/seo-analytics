"""Read-side queries for the dashboard screens. Every function takes an open connection and a client_id and returns
plain dicts, so screens stay thin and the numbers can be tested without a browser."""
import datetime as dt, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "seo"))

BANDS = [(80, "Leader"), (60, "Competitive"), (40, "Emerging"), (0, "Not shortlisted")]
def band(s): return None if s is None else next(n for f, n in BANDS if float(s) >= f)

def pct_change(now, before):
    if not before: return None
    return round(100 * (float(now or 0) - float(before)) / float(before), 1)

def periods(conn, client_id, days=28, date_range=None):
    """Two back-to-back windows ending on the last day Search Console has data for."""
    if date_range is not None:
        start, end = date_range
        length = (end - start).days + 1
        if length < 1 or length > 366: raise ValueError("Choose a range of 1 to 366 days.")
        return (start, end), (start - dt.timedelta(days=length), start - dt.timedelta(days=1))
    last = conn.execute("SELECT max(date) FROM gsc_search_daily WHERE client_id=%s", [client_id]).fetchone()[0]
    last = last or dt.date.today() - dt.timedelta(days=3)
    cur = (last - dt.timedelta(days=days - 1), last)
    prev = (cur[0] - dt.timedelta(days=days), cur[0] - dt.timedelta(days=1))
    return cur, prev

def rows(cur, cols):
    return [dict(zip(cols, r)) for r in cur.fetchall()]

# ------------------------------------------------------------------ search
def search_totals(conn, client_id, days=28, date_range=None):
    cur, prev = periods(conn, client_id, days, date_range)
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

def daily_clicks(conn, client_id, days=90, date_range=None):
    cur, _ = periods(conn, client_id, days, date_range)
    return rows(conn.execute("""SELECT date, sum(clicks) FILTER (WHERE NOT is_branded), sum(clicks) FILTER (WHERE is_branded)
                                FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s
                                GROUP BY date ORDER BY date""", [client_id, *cur]), ["date", "nonbranded", "branded"])


def top_queries(conn, client_id, branded=None, limit=50, days=28, date_range=None):
    cur, prev = periods(conn, client_id, days, date_range)
    flt = "" if branded is None else (" AND is_branded" if branded else " AND NOT is_branded")
    return rows(conn.execute(f"""WITH a AS (SELECT query, sum(clicks) c, sum(impressions) i, sum(position*impressions)/nullif(sum(impressions),0) p
                                             FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s{flt} GROUP BY query),
                                      b AS (SELECT query, sum(position*impressions)/nullif(sum(impressions),0) p, sum(clicks) c
                                             FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s{flt} GROUP BY query)
                                 SELECT a.query, a.c, a.i, round(100.0*a.c/nullif(a.i,0),1), round(a.p,1), round(b.p - a.p, 1), b.c
                                 FROM a LEFT JOIN b USING (query) ORDER BY a.c DESC, a.i DESC LIMIT %s""",
                             [client_id, *cur, client_id, *prev, limit]),
                ["query", "clicks", "impressions", "ctr", "position", "position_gain", "prev_clicks"])

def top_pages(conn, client_id, limit=50, days=28, date_range=None):
    cur, _ = periods(conn, client_id, days, date_range)
    return rows(conn.execute("""SELECT page, sum(clicks), sum(impressions), round(100.0*sum(clicks)/nullif(sum(impressions),0),1),
                                       round(sum(position*impressions)/nullif(sum(impressions),0),1),
                                       (array_agg(query ORDER BY clicks DESC))[1]
                                FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s AND NOT is_branded
                                GROUP BY page ORDER BY 2 DESC LIMIT %s""", [client_id, *cur, limit]),
                ["page", "clicks", "impressions", "ctr", "position", "top_query"])

# ------------------------------------------------------------------ revenue
def organic_revenue(conn, client_id, days=28, date_range=None):
    if date_range is not None:
        start, end = date_range
    else:
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

def revenue_by_landing(conn, client_id, limit=30, days=28, date_range=None):
    """Organic landing pages ranked by revenue, each with the Search Console query that sends it most clicks."""
    if date_range is not None:
        start, end = date_range
    else:
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
    return rows(conn.execute("""SELECT competitor_id, name, domain, source, tracked, relevance, common_keywords, organic_keywords, organic_traffic,
                                       own_brand, aliases
                                FROM competitors WHERE client_id=%s ORDER BY own_brand, tracked DESC, common_keywords DESC NULLS LAST, name""", [client_id]),
                ["id", "name", "domain", "source", "tracked", "relevance", "common", "keywords", "traffic", "own", "aliases"])

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
    out = rows(conn.execute("""SELECT keyword, position, prev_position, search_volume, url, traffic_pct, kd, intent, serp_features, serp_features_owned
                               FROM semrush_keywords WHERE client_id=%s AND domain=%s AND pulled_on=%s
                               ORDER BY traffic_pct DESC NULLS LAST, search_volume DESC LIMIT %s""",
                            [client_id, dom, last, limit]), ["keyword", "position", "prev", "volume", "url", "traffic_pct", "kd", "intent", "fk", "fp"])
    for k in out: k["features"] = serp_features(k["fk"], k["fp"])
    return last, out

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
                                WHERE client_id=%s AND collector NOT LIKE '%%\\_auto' ORDER BY collector, started_at DESC""", [client_id]),
                ["collector", "status", "started", "finished", "note"])


# ------------------------------------------------------------------ SERP features (Semrush codes)
SERP = {"0": "Instant answer", "1": "Knowledge panel", "2": "Carousel", "3": "Local pack", "4": "Top stories", "5": "Image pack",
        "6": "Sitelinks", "7": "Reviews", "9": "Video", "11": "Featured snippet", "16": "Shopping ads", "20": "Video carousel",
        "21": "People also ask", "22": "FAQ", "34": "Popular products", "36": "Related searches", "38": "Short videos",
        "41": "Buying guide", "43": "Things to know", "45": "Discussions and forums", "46": "Explore brands",
        "47": "Questions and answers", "48": "Popular stores", "50": "People also search", "52": "AI Overview"}
SERP_FIRST = ["52", "11", "21", "34", "5", "9", "38", "3", "16"]          # shown first: what an SEO can act on

def serp_features(fk, fp=None):
    """'5,11,52', '5' -> [{name, owned}] with AI Overview, featured snippet and People also ask first."""
    codes = [c.strip() for c in (fk or "").split(",") if c.strip()]
    owned = {c.strip() for c in (fp or "").split(",") if c.strip()}
    codes.sort(key=lambda c: (SERP_FIRST.index(c) if c in SERP_FIRST else 99, int(c) if c.isdigit() else 999))
    return [{"code": c, "name": SERP.get(c, f"Feature {c}"), "owned": c in owned} for c in codes]

def semrush_by_feature(conn):
    return dict(conn.execute("SELECT coalesce(feature, 'keywords'), sum(units) FROM semrush_usage GROUP BY 1").fetchall())

# ------------------------------------------------------------------ rankings
def rankings(conn, client_id, days=28, date_range=None):
    """Tracked keywords: latest Search Console position (and Semrush's when tracked there), change over 7 days, 28-day clicks."""
    cur, _ = periods(conn, client_id, days, date_range)
    out = rows(conn.execute("""
        WITH g AS (SELECT keyword, date, position, url, clicks, impressions FROM rank_positions WHERE client_id=%(c)s AND source='gsc'
                   AND date BETWEEN %(start)s AND %(end)s),
             last AS (SELECT DISTINCT ON (keyword) keyword, date, position, url FROM g ORDER BY keyword, date DESC),
             wk AS (SELECT DISTINCT ON (keyword) keyword, position FROM g WHERE date <= (SELECT max(date) FROM g) - 7 ORDER BY keyword, date DESC),
             tot AS (SELECT keyword, sum(clicks) clicks, sum(impressions) impressions, min(position) best FROM g GROUP BY keyword),
             sm AS (SELECT DISTINCT ON (keyword) keyword, position, date FROM rank_positions WHERE client_id=%(c)s AND source='semrush' AND date <= %(end)s
                    ORDER BY keyword, date DESC)
        SELECT k.keyword, k.tags, k.target_url, last.position, last.date, last.url, wk.position, tot.clicks, tot.impressions, tot.best, sm.position, sm.date
        FROM rank_keywords k LEFT JOIN last USING (keyword) LEFT JOIN wk USING (keyword) LEFT JOIN tot USING (keyword) LEFT JOIN sm USING (keyword)
        WHERE k.client_id=%(c)s ORDER BY last.position IS NULL, tot.clicks DESC NULLS LAST, k.keyword""", {"c": client_id, "start": cur[0], "end": cur[1]}),
        ["keyword", "tags", "target", "position", "date", "url", "week_ago", "clicks", "impressions", "best", "semrush", "semrush_date"])
    feats = {r[0].lower(): (r[1], r[2]) for r in conn.execute("""SELECT DISTINCT ON (keyword) keyword, serp_features, serp_features_owned
                                                                FROM semrush_keywords WHERE client_id=%s ORDER BY keyword, pulled_on DESC""", [client_id])}
    for r in out:
        r["change"] = round(float(r["week_ago"]) - float(r["position"]), 1) if r["position"] is not None and r["week_ago"] is not None else None
        r["features"] = serp_features(*feats.get(r["keyword"].lower(), (None, None)))
        r["off_target"] = bool(r["target"] and r["url"] and r["url"].rstrip("/").split("://")[-1].removeprefix("www.")
                               != r["target"].rstrip("/").split("://")[-1].removeprefix("www."))
    return out

def rank_trend(conn, client_id, keyword, days=90, date_range=None):
    cur, _ = periods(conn, client_id, days, date_range)
    return rows(conn.execute("""SELECT date, position FROM rank_positions WHERE client_id=%s AND keyword=%s AND source='gsc'
                                AND date BETWEEN %s AND %s ORDER BY date""", [client_id, keyword, *cur]), ["date", "position"])

# ------------------------------------------------------------------ Search Console: countries, devices, indexing, sitemaps, vitals
def search_by(conn, client_id, dim, days=28, date_range=None):
    """Clicks, impressions, CTR and position by country or device, with the change in clicks against the period before."""
    if dim not in ("country", "device"): raise ValueError(dim)
    cur, prev = periods(conn, client_id, days, date_range)
    out = rows(conn.execute(f"""WITH a AS (SELECT {dim} k, sum(clicks) c, sum(impressions) i, sum(position*impressions)/nullif(sum(impressions),0) p
                                           FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s GROUP BY 1),
                                     b AS (SELECT {dim} k, sum(clicks) c FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s GROUP BY 1)
                                SELECT a.k, a.c, a.i, round(100.0*a.c/nullif(a.i,0),1), round(a.p,1), b.c
                                FROM a LEFT JOIN b USING (k) ORDER BY a.c DESC, a.i DESC LIMIT 100""",
                            [client_id, *cur, client_id, *prev]), ["key", "clicks", "impressions", "ctr", "position", "prev_clicks"])
    for r in out: r["change"] = pct_change(r["clicks"], r["prev_clicks"])
    return out

def index_status(conn, client_id, state=None, limit=500):
    """Google's index status per inspected URL (URL Inspection API), counted by coverage state; not-indexed pages first."""
    summary = rows(conn.execute("""SELECT coalesce(coverage_state, 'Unknown'), verdict, count(*) FROM gsc_url_inspection WHERE client_id=%s
                                   GROUP BY 1, 2 ORDER BY verdict IS NOT DISTINCT FROM 'PASS', 3 DESC""", [client_id]), ["state", "verdict", "n"])
    flt = " AND coalesce(coverage_state, 'Unknown') = %s" if state else ""
    pages = rows(conn.execute(f"""SELECT url, verdict, coverage_state, indexing_state, robots_state, fetch_state, last_crawl, crawled_as,
                                         google_canonical, user_canonical, rich_types, inspected_at
                                  FROM gsc_url_inspection WHERE client_id=%s{flt}
                                  ORDER BY verdict IS NOT DISTINCT FROM 'PASS', last_crawl NULLS FIRST LIMIT %s""",
                              [client_id, *([state] if state else []), limit]),
                 ["url", "verdict", "coverage_state", "indexing_state", "robots_state", "fetch_state", "last_crawl", "crawled_as",
                  "google_canonical", "user_canonical", "rich_types", "inspected_at"])
    return {"summary": summary, "pages": pages, "total": sum(s["n"] for s in summary),
            "indexed": sum(s["n"] for s in summary if s["verdict"] == "PASS")}

def sitemaps(conn, client_id):
    out = rows(conn.execute("""SELECT path, type, is_index, is_pending, last_submitted, last_downloaded, errors, warnings, contents, fetched_at
                               FROM gsc_sitemaps WHERE client_id=%s ORDER BY is_index DESC, path""", [client_id]),
               ["path", "type", "is_index", "is_pending", "last_submitted", "last_downloaded", "errors", "warnings", "contents", "fetched_at"])
    for r in out: r["submitted"] = sum(int(c.get("submitted") or 0) for c in r["contents"] or [])
    return out

def vitals(conn, client_id):
    """Latest PageSpeed result per page, mobile and desktop side by side. Field values are real Chrome users (CrUX)."""
    res = rows(conn.execute("""SELECT DISTINCT ON (url, strategy) url, strategy, fetched_at, perf_score, lcp_ms, cls, tbt_ms, ttfb_ms,
                                      field_lcp_ms, field_cls, field_inp_ms, field_category, error
                               FROM pagespeed_results WHERE client_id=%s ORDER BY url, strategy, fetched_at DESC""", [client_id]),
               ["url", "strategy", "fetched_at", "perf_score", "lcp_ms", "cls", "tbt_ms", "ttfb_ms", "field_lcp_ms", "field_cls",
                "field_inp_ms", "field_category", "error"])
    out = {}
    for r in res: out.setdefault(r["url"], {"url": r["url"]})[r["strategy"]] = r
    return sorted(out.values(), key=lambda p: ((p.get("mobile") or {}).get("perf_score") is None, (p.get("mobile") or {}).get("perf_score") or 0))

# ------------------------------------------------------------------ technical audit
def tech_summary(conn, client_id):
    last = {}
    for src in ("hovers", "semrush"):
        r = conn.execute("""SELECT finished_at, pages_crawled, health_score, errors, warnings, notices FROM tech_crawls
                            WHERE client_id=%s AND source=%s AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1""", [client_id, src]).fetchone()
        if r: last[src] = dict(zip(["at", "pages", "health", "errors", "warnings", "notices"], r))
    return last

def tech_issues(conn, client_id, show="open"):
    where = {"open": "pages > 0 AND status IN ('open','in_progress')", "fixed": "status IN ('fixed','verified')", "all": "true"}[show]
    return rows(conn.execute(f"""SELECT issue_code, source, title, severity, team, how_to_fix, status, owner, due_date, pages, first_seen, last_seen
                                 FROM tech_issues WHERE client_id=%s AND {where}
                                 ORDER BY array_position(ARRAY['error','warning','notice'], severity), pages DESC""", [client_id]),
                ["code", "source", "title", "severity", "team", "fix", "status", "owner", "due", "pages", "first_seen", "last_seen"])

def tech_issue_pages(conn, client_id, code, limit=500):
    crawl = conn.execute("""SELECT crawl_id FROM tech_issue_pages WHERE client_id=%s AND issue_code=%s
                            ORDER BY (SELECT finished_at FROM tech_crawls t WHERE t.crawl_id=tech_issue_pages.crawl_id) DESC NULLS LAST LIMIT 1""",
                         [client_id, code]).fetchone()
    if not crawl: return []
    return rows(conn.execute("""SELECT url, detail FROM tech_issue_pages WHERE crawl_id=%s AND issue_code=%s ORDER BY url LIMIT %s""",
                             [crawl[0], code, limit]), ["url", "detail"])

# ------------------------------------------------------------------ content ideas
def ideas(conn, client_id, kind=None, status="new"):
    return rows(conn.execute("""SELECT kind, seed, keyword, search_volume, kd, intent, serp_features, status, pulled_on FROM keyword_ideas
                                WHERE client_id=%s AND (%s::text IS NULL OR kind=%s) AND (%s='all' OR status=%s)
                                ORDER BY search_volume DESC NULLS LAST LIMIT 300""", [client_id, kind, kind, status, status]),
                ["kind", "seed", "keyword", "volume", "kd", "intent", "fk", "status", "pulled_on"])

# ------------------------------------------------------------------ backlinks
def backlinks(conn, client_id):
    day = conn.execute("SELECT max(pulled_on) FROM backlink_overview WHERE client_id=%s", [client_id]).fetchone()[0]
    if not day: return None
    me = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    me = me.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    ov = rows(conn.execute("""SELECT domain, ascore, backlinks, refdomains, follows, nofollows FROM backlink_overview
                              WHERE client_id=%s AND pulled_on=%s ORDER BY domain = %s DESC, ascore DESC NULLS LAST""", [client_id, day, me]),
              ["domain", "ascore", "backlinks", "refdomains", "follows", "nofollows"])
    rday = conn.execute("SELECT max(pulled_on) FROM backlink_refdomains WHERE client_id=%s", [client_id]).fetchone()[0]
    ref = rows(conn.execute("""SELECT domain, ascore, backlinks, first_seen, last_seen FROM backlink_refdomains WHERE client_id=%s AND pulled_on=%s
                               ORDER BY ascore DESC NULLS LAST LIMIT 100""", [client_id, rday]), ["domain", "ascore", "backlinks", "first_seen", "last_seen"]) if rday else []
    gday = conn.execute("SELECT max(pulled_on) FROM backlink_gap WHERE client_id=%s", [client_id]).fetchone()[0]
    gap = rows(conn.execute("""SELECT domain, ascore, links_to, status FROM backlink_gap WHERE client_id=%s AND pulled_on=%s AND status <> 'dismissed'
                               ORDER BY ascore DESC NULLS LAST LIMIT 200""", [client_id, gday]), ["domain", "ascore", "links_to", "status"]) if gday else []
    return {"day": day, "me": me, "overview": ov, "refdomains": ref, "gap": gap, "gap_day": gday}

# ------------------------------------------------------------------ research lookups (Semrush)
def lookups(conn, client_id, tool, limit=12):
    return rows(conn.execute("""SELECT lookup_id, query, database, pulled_at, units, sample FROM research_lookups WHERE client_id=%s AND tool=%s
                                ORDER BY pulled_at DESC LIMIT %s""", [client_id, tool, limit]), ["id", "query", "database", "at", "units", "sample"])

def lookup(conn, client_id, tool, lookup_id=None):
    q = """SELECT lookup_id, query, database, pulled_at, units, result, sample FROM research_lookups WHERE client_id=%s AND tool=%s"""
    r = conn.execute(q + (" AND lookup_id::text=%s" if lookup_id else " ORDER BY pulled_at DESC LIMIT 1"),
                     [client_id, tool] + ([lookup_id] if lookup_id else [])).fetchone()
    if not r: return None
    out = dict(zip(["id", "query", "database", "at", "units", "result", "sample"], r))
    res = out["result"]
    for sec in res.values():                               # SERP feature names for every row that has codes
        for row in (sec if isinstance(sec, list) else [sec] if isinstance(sec, dict) else []):
            if isinstance(row, dict) and ("fk" in row or "fp" in row):
                row["features"] = serp_features(row.get("fk"), row.get("fp"))
    return out

# ------------------------------------------------------------------ Reddit
def _client_brand(conn, client_id):
    return conn.execute("SELECT name FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]

def reddit_summary(conn, client_id):
    total, last = conn.execute("SELECT count(*), max(pulled_on) FROM reddit_items WHERE client_id=%s", [client_id]).fetchone()
    if not total: return None
    me = _client_brand(conn, client_id)
    brands = rows(conn.execute("""SELECT b, count(*), count(*) FILTER (WHERE sentiment='positive'), count(*) FILTER (WHERE sentiment='negative'),
                                         count(DISTINCT subreddit), coalesce(sum(score),0)
                                  FROM reddit_items, unnest(brands) b WHERE client_id=%s GROUP BY b ORDER BY count(*) DESC""", [client_id]),
                  ["brand", "mentions", "positive", "negative", "subreddits", "upvotes"])
    allm = sum(b["mentions"] for b in brands) or 1
    for b in brands: b["share"] = b["mentions"] / allm; b["is_client"] = b["brand"] == me
    q = conn.execute("SELECT count(*) FILTER (WHERE is_question), count(*) FILTER (WHERE pain_point IS NOT NULL) FROM reddit_items WHERE client_id=%s",
                     [client_id]).fetchone()
    return {"total": total, "last": last, "brands": brands, "me": me, "questions": q[0], "pains": q[1]}

def subreddit_map(conn, client_id, brand_names):
    out = rows(conn.execute("""SELECT subreddit, count(*), count(*) FILTER (WHERE found_by='cited'), max(created_at), coalesce(sum(score),0),
                                      count(*) FILTER (WHERE is_question)
                               FROM reddit_items WHERE client_id=%s AND subreddit IS NOT NULL GROUP BY subreddit ORDER BY count(*) DESC LIMIT 40""", [client_id]),
               ["subreddit", "posts", "cited", "last", "upvotes", "questions"])
    members = {n.lower(): (n, m, rel) for n, m, rel in conn.execute("SELECT name, members, relevance FROM reddit_communities WHERE client_id=%s", [client_id])}
    seen = {r["subreddit"].lower() for r in out}
    for r in out:
        r["members"] = members.get(r["subreddit"].lower(), (None, None))[1]
    for low, (n, m, rel) in sorted(members.items(), key=lambda x: -(x[1][2] or 0)):
        if low not in seen:                       # found for the topics, nothing about the brands there yet: a place to start
            out.append({"subreddit": n, "posts": 0, "cited": 0, "last": None, "upvotes": 0, "questions": 0, "members": m})
    per = {}
    for sub, b, n in conn.execute("""SELECT subreddit, b, count(*) FROM reddit_items, unnest(brands) b WHERE client_id=%s GROUP BY 1, 2""", [client_id]):
        per.setdefault(sub, {})[b] = n
    for r in out: r["by_brand"] = [per.get(r["subreddit"], {}).get(b, 0) for b in brand_names]
    return out

def reddit_items(conn, client_id, brand=None, sentiment=None, limit=60):
    return rows(conn.execute("""SELECT kind, subreddit, title, body, url, score, comments, created_at, brands, sentiment, found_by
                                FROM reddit_items WHERE client_id=%s AND (%s::text IS NULL OR %s = ANY(brands)) AND (%s::text IS NULL OR sentiment=%s)
                                AND cardinality(brands) > 0 ORDER BY score DESC NULLS LAST LIMIT %s""",
                             [client_id, brand, brand, sentiment, sentiment, limit]),
                ["kind", "subreddit", "title", "body", "url", "score", "comments", "created_at", "brands", "sentiment", "found_by"])

def reddit_questions(conn, client_id):
    qs = rows(conn.execute("""SELECT coalesce(topic, query, 'Other') t, title, body, url, subreddit, score, comments FROM reddit_items
                              WHERE client_id=%s AND is_question ORDER BY t, score DESC NULLS LAST""", [client_id]),
              ["topic", "title", "body", "url", "subreddit", "score", "comments"])
    groups = {}
    for q in qs: groups.setdefault(q["topic"], []).append(q)
    pains = rows(conn.execute("""SELECT pain_point, count(*), count(*) FILTER (WHERE cardinality(brands) > 0),
                                        (array_agg(coalesce(title, left(body, 160)) ORDER BY score DESC NULLS LAST))[1:3]
                                 FROM reddit_items WHERE client_id=%s AND pain_point IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""", [client_id]),
                 ["theme", "items", "with_brand", "examples"])
    return groups, pains

def reddit_cited(conn, client_id):
    """Reddit threads ChatGPT cited for this brand's prompts, with the brands named in each (from the pulled thread)."""
    import re as _re
    cites = rows(conn.execute("""SELECT c.url, count(*), count(DISTINCT r.prompt_id),
                                        bool_or(EXISTS (SELECT 1 FROM mentions m WHERE m.answer_id = c.answer_id AND m.is_client))
                                 FROM citations c JOIN answers a ON a.answer_id = c.answer_id JOIN runs r ON r.run_id = a.run_id
                                 WHERE c.client_id=%s AND (c.domain = 'reddit.com' OR c.domain LIKE '%%.reddit.com') GROUP BY c.url ORDER BY 2 DESC""", [client_id]),
                 ["url", "times", "prompts", "client_in_answer"])
    items = conn.execute("SELECT url, subreddit, title, brands, score, comments FROM reddit_items WHERE client_id=%s AND url LIKE '%%/comments/%%'",
                         [client_id]).fetchall()
    tid = lambda u: (_re.search(r"/comments/([a-z0-9]+)", u or "", _re.I) or [None, None])[1]
    by_thread = {}
    for u, sub, title, brands, score, ncom in items:
        t = tid(u)
        if not t: continue
        d = by_thread.setdefault(t.lower(), {"subreddit": sub, "title": None, "brands": set(), "score": None, "comments": None})
        d["brands"] |= set(brands or [])
        if title: d.update(title=title, score=score, comments=ncom)
    have = {(tid(c["url"]) or "").lower() for c in cites}
    for u, in conn.execute("""SELECT DISTINCT url FROM reddit_items WHERE client_id=%s AND sample AND found_by='cited'""", [client_id]).fetchall():
        if (tid(u) or "").lower() not in have:           # sample rows stand in for citations until ChatGPT runs
            cites.append({"url": u, "times": 1, "prompts": 1, "client_in_answer": False}); have.add((tid(u) or "").lower())
    for c in cites:
        d = by_thread.get((tid(c["url"]) or "").lower(), {})
        c.update(subreddit=d.get("subreddit"), title=d.get("title"), brands=sorted(d.get("brands", [])), score=d.get("score"), comments=d.get("comments"),
                 pulled=bool(d))
    return cites

# ------------------------------------------------------------------ YouTube
def youtube_summary(conn, client_id):
    total, last = conn.execute("SELECT count(*), max(pulled_on) FROM youtube_videos WHERE client_id=%s", [client_id]).fetchone()
    if not total: return None
    me = _client_brand(conn, client_id)
    brands = rows(conn.execute("""SELECT b, count(*), coalesce(sum(views),0), count(*) FILTER (WHERE found_by='topic'),
                                         count(DISTINCT channel_id)
                                  FROM youtube_videos, unnest(brands) b WHERE client_id=%s GROUP BY b ORDER BY sum(views) DESC NULLS LAST""", [client_id]),
                  ["brand", "videos", "views", "in_topic", "channels"])
    allv = sum(b["views"] for b in brands) or 1
    for b in brands: b["share"] = b["views"] / allv; b["is_client"] = b["brand"] == me
    own = conn.execute("SELECT count(*), coalesce(sum(views),0) FROM youtube_videos WHERE client_id=%s AND own_channel", [client_id]).fetchone()
    return {"videos": total, "last": last, "brands": brands, "me": me, "own_videos": own[0], "own_views": own[1]}

def youtube_topics(conn, client_id, brand_names):
    """For each topic search: who appears in the top results and how high."""
    out = []
    for (q,) in conn.execute("SELECT DISTINCT query FROM youtube_videos WHERE client_id=%s AND found_by='topic' ORDER BY 1", [client_id]).fetchall():
        vids = rows(conn.execute("""SELECT search_rank, title, channel, views, brands, own_channel, video_id FROM youtube_videos
                                    WHERE client_id=%s AND found_by='topic' AND query=%s ORDER BY search_rank NULLS LAST""", [client_id, q]),
                    ["rank", "title", "channel", "views", "brands", "own", "id"])
        best = {b: min([v["rank"] for v in vids if b in v["brands"] and v["rank"]] or [None], key=lambda x: x or 999) for b in brand_names}
        out.append({"query": q, "videos": vids, "count": [sum(1 for v in vids if b in v["brands"]) for b in brand_names],
                    "best": [best[b] for b in brand_names]})
    return out

def youtube_videos(conn, client_id, brand=None, limit=60):
    return rows(conn.execute("""SELECT video_id, title, channel, published_at, views, likes, comments, duration_s, brands, own_channel, found_by, query
                                FROM youtube_videos WHERE client_id=%s AND (%s::text IS NULL OR %s = ANY(brands)) AND cardinality(brands) > 0
                                ORDER BY views DESC NULLS LAST LIMIT %s""", [client_id, brand, brand, limit]),
                ["id", "title", "channel", "published", "views", "likes", "comments", "duration", "brands", "own", "found_by", "query"])

def youtube_creators(conn, client_id, limit=30):
    return rows(conn.execute("""SELECT c.channel_id, c.title, c.subscribers, count(v.*), coalesce(sum(v.views),0),
                                       array_remove(array_agg(DISTINCT b), NULL)
                                FROM youtube_channels c JOIN youtube_videos v ON v.client_id=c.client_id AND v.channel_id=c.channel_id
                                LEFT JOIN LATERAL unnest(v.brands) b ON true
                                WHERE c.client_id=%s AND NOT v.own_channel GROUP BY c.channel_id, c.title, c.subscribers
                                ORDER BY c.subscribers DESC NULLS LAST LIMIT %s""", [client_id, limit]),
                ["id", "title", "subscribers", "videos", "views", "brands"])

def youtube_comment_insights(conn, client_id):
    qs = rows(conn.execute("""SELECT c.text, c.likes, v.title, v.video_id FROM youtube_comments c JOIN youtube_videos v USING (client_id, video_id)
                              WHERE c.client_id=%s AND c.is_question ORDER BY c.likes DESC NULLS LAST LIMIT 40""", [client_id]),
              ["text", "likes", "video", "video_id"])
    pains = rows(conn.execute("""SELECT pain_point, count(*), (array_agg(left(text, 160) ORDER BY likes DESC NULLS LAST))[1:3]
                                 FROM youtube_comments WHERE client_id=%s AND pain_point IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""", [client_id]),
                 ["theme", "items", "examples"])
    return qs, pains

def youtube_cited(conn, client_id):
    return rows(conn.execute("""SELECT v.video_id, v.title, v.channel, v.views, v.brands, greatest(count(c.*), CASE WHEN v.sample THEN 1 ELSE 0 END)
                                FROM youtube_videos v LEFT JOIN citations c ON c.client_id=v.client_id AND (c.url LIKE '%%' || v.video_id || '%%')
                                WHERE v.client_id=%s AND (v.found_by='cited' OR c.url IS NOT NULL) GROUP BY 1,2,3,4,5, v.sample ORDER BY 6 DESC""", [client_id]),
                ["id", "title", "channel", "views", "brands", "times"])

def source_status(conn, client_id, name):
    """Last runs of one collector: when it last worked, whether one is running, and the last error."""
    r = conn.execute("""SELECT max(finished_at) FILTER (WHERE status='ok' AND collector NOT LIKE '%%\\_auto'),
                               max(started_at) FILTER (WHERE (collector=%s AND status='running' AND started_at > now() - interval '1 hour')
                                                         OR (collector=%s || '_auto' AND started_at > now() - interval '3 minutes')),
                               (array_agg(error ORDER BY started_at DESC) FILTER (WHERE status='error'))[1],
                               max(started_at) FILTER (WHERE status='error')
                        FROM collector_runs WHERE client_id=%s AND collector IN (%s, %s || '_auto')""", [name, name, client_id, name, name]).fetchone()
    return {"last_ok": r[0], "running": r[1], "error": (r[2] or "").split("\n")[0] if r[3] and (not r[0] or r[3] > r[0]) else None, "error_at": r[3]}

def reddit_communities(conn, client_id, limit=12):
    return rows(conn.execute("""SELECT c.name, c.title, c.members, c.relevance, c.found_for,
                                       (SELECT count(*) FROM reddit_items i WHERE i.client_id=c.client_id AND i.subreddit=c.name)
                                FROM reddit_communities c WHERE c.client_id=%s ORDER BY c.relevance DESC NULLS LAST, c.members DESC NULLS LAST LIMIT %s""",
                             [client_id, limit]), ["name", "title", "members", "relevance", "found_for", "posts"])

# ------------------------------------------------------------------ sources AI trusts (niche-aware)
def _on(domain, d):
    return domain == d or domain.endswith("." + d)

def source_presence(conn, client_id, niche, days=30):
    """For the niche's high-authority sources (plus the generic four and any the team added): how often ChatGPT cites each
    one in this brand's prompts, and which brands those answers name. Free: built from the answers Orbit already stores.
    Returns {"sources": [...], "answers": n, "others": [top cited domains not on the list], "brands": [competitor names]}."""
    import niches as N
    me = _client_brand(conn, client_id)
    saved = {r[0]: r for r in conn.execute("""SELECT domain, name, kind, status, listing_url, note, updated_at FROM client_sources
                                             WHERE client_id=%s""", [client_id])}
    lst = [dict(s) for s in N.preset(niche)]
    for d, r in saved.items():
        if not any(s["domain"] == d for s in lst):
            lst.append({"domain": d, "name": r[1] or d, "kind": r[2] or "custom", "why": r[5] or "", "generic": False})
    for s in lst:
        r = saved.get(s["domain"])
        s.update(status=r[3] if r else "unknown", listing_url=r[4] if r else None, updated_at=r[6] if r else None)
    lst = [s for s in lst if s["status"] != "hidden"]
    ans = conn.execute("""SELECT a.answer_id, p.text,
                                 array(SELECT DISTINCT c.domain FROM citations c WHERE c.answer_id=a.answer_id),
                                 EXISTS (SELECT 1 FROM mentions m WHERE m.answer_id=a.answer_id AND m.is_client AND m.rank_in_answer IS NOT NULL),
                                 array(SELECT DISTINCT m.brand FROM mentions m WHERE m.answer_id=a.answer_id AND NOT m.is_client AND m.rank_in_answer IS NOT NULL)
                          FROM answers a JOIN runs r USING (run_id) JOIN prompts p ON p.prompt_id=r.prompt_id
                          WHERE a.client_id=%s AND r.run_at > now() - make_interval(days => %s)""", [client_id, days]).fetchall()
    total = len(ans)
    tracked = [r[0] for r in conn.execute("SELECT name FROM competitors WHERE client_id=%s AND tracked AND NOT coalesce(own_brand,false) ORDER BY name",
                                          [client_id])]
    for s in lst:
        hit = [a for a in ans if any(_on(d, s["domain"]) for d in a[2])]
        s["answers"] = len(hit)
        s["share"] = len(hit) / total if total else 0
        s["you"] = sum(1 for a in hit if a[3])
        comp = {}
        for a in hit:
            for b in a[4]: comp[b] = comp.get(b, 0) + 1
        s["rivals"] = sorted(comp.items(), key=lambda x: -x[1])[:3]
        s["prompts"] = sorted({a[1] for a in hit})[:4]
        s["gap"] = bool(hit) and s["you"] < (s["rivals"][0][1] if s["rivals"] else 0)
    lst.sort(key=lambda s: (-s["answers"], not s.get("generic"), s["name"]))
    listed = {s["domain"] for s in lst}
    counts = {}
    for a in ans:
        for d in set(a[2]): counts[d] = counts.get(d, 0) + 1
    own = (conn.execute("SELECT domain FROM clients WHERE client_id=%s", [client_id]).fetchone()[0] or "").lower().removeprefix("www.")
    others = sorted(((d, n) for d, n in counts.items()
                     if not any(_on(d, x) for x in listed) and not (own and _on(d, own))), key=lambda x: -x[1])[:8]
    return {"sources": lst, "answers": total, "others": others, "brands": tracked, "me": me}

# ------------------------------------------------------------------ overview: the keywords and prompts we committed to
def keyword_board(conn, client_id, days=28):
    """Tracked target keywords: position now (Search Console, 7-day average), position a month ago, clicks, ranking page."""
    out = rows(conn.execute("""
        WITH g AS (SELECT keyword, date, position, url, clicks FROM rank_positions WHERE client_id=%(c)s AND source='gsc'
                   AND date > current_date - 70),
             mx AS (SELECT max(date) d FROM g),
             now_ AS (SELECT keyword, avg(position) pos, sum(clicks) clicks FROM g, mx WHERE g.date > mx.d - 7 GROUP BY keyword),
             then_ AS (SELECT keyword, avg(position) pos FROM g, mx WHERE g.date <= mx.d - %(d)s AND g.date > mx.d - %(d)s - 7 GROUP BY keyword),
             clk AS (SELECT keyword, sum(clicks) clicks FROM g, mx WHERE g.date > mx.d - %(d)s GROUP BY keyword),
             pg AS (SELECT DISTINCT ON (keyword) keyword, url FROM g ORDER BY keyword, date DESC)
        SELECT k.keyword, now_.pos, then_.pos, clk.clicks, pg.url
        FROM rank_keywords k LEFT JOIN now_ USING (keyword) LEFT JOIN then_ USING (keyword) LEFT JOIN clk USING (keyword) LEFT JOIN pg USING (keyword)
        WHERE k.client_id=%(c)s ORDER BY now_.pos IS NULL, now_.pos, k.keyword""", {"c": client_id, "d": days}),
        ["keyword", "position", "before", "clicks", "url"])
    for r in out:
        p, b = (float(r["position"]) if r["position"] is not None else None), (float(r["before"]) if r["before"] is not None else None)
        r["position"], r["before"] = p, b
        r["change"] = round(b - p, 1) if p is not None and b is not None else None
        r["band"] = ("top3" if p <= 3.5 else "page1" if p <= 10.5 else "page2" if p <= 20.5 else "beyond") if p is not None else "none"
    n = len(out)
    top10 = sum(1 for r in out if r["position"] is not None and r["position"] <= 10.5)
    top10_before = sum(1 for r in out if r["before"] is not None and r["before"] <= 10.5)
    ranked = [r["position"] for r in out if r["position"] is not None]
    before = [r["before"] for r in out if r["before"] is not None and r["position"] is not None]
    now_ranked = [r["position"] for r in out if r["before"] is not None and r["position"] is not None]
    return {"rows": out, "n": n, "top10": top10, "top10_before": top10_before, "top3": sum(1 for r in out if r["band"] == "top3"),
            "avg": round(sum(ranked) / len(ranked), 1) if ranked else None,
            "avg_change": round(sum(before) / len(before) - sum(now_ranked) / len(now_ranked), 1) if before else None,
            "improved": sum(1 for r in out if (r["change"] or 0) >= 1), "declined": sum(1 for r in out if (r["change"] or 0) <= -1),
            "clicks": sum(r["clicks"] or 0 for r in out)}

def prompt_board(conn, client_id, engine="chatgpt"):
    """Approved prompts: named or not in the latest ChatGPT run, the position in the answer, and the run before for change."""
    cycles = [r[0] for r in conn.execute("""SELECT DISTINCT cycle FROM runs WHERE client_id=%s AND engine=%s AND status='ok'
                                           ORDER BY cycle DESC LIMIT 2""", [client_id, engine])]
    cur, prev = (cycles + [None, None])[:2]
    def at(cycle):
        if not cycle: return {}
        return {r[0]: r[1:] for r in conn.execute("""
            SELECT r.prompt_id, bool_or(m.is_client AND m.rank_in_answer IS NOT NULL), min(m.rank_in_answer) FILTER (WHERE m.is_client),
                   (array_agg(m.brand ORDER BY m.rank_in_answer) FILTER (WHERE NOT m.is_client AND m.rank_in_answer IS NOT NULL))[1]
            FROM runs r JOIN answers a USING (run_id) LEFT JOIN mentions m ON m.answer_id=a.answer_id
            WHERE r.client_id=%s AND r.engine=%s AND r.cycle=%s GROUP BY r.prompt_id""", [client_id, engine, cycle])}
    now_, then_ = at(cur), at(prev)
    out = []
    for pid, text, stage in conn.execute("""SELECT prompt_id, text, funnel_stage FROM prompts WHERE client_id=%s AND approved AND active
                                           ORDER BY funnel_stage, text""", [client_id]):
        n, t = now_.get(pid), then_.get(pid)
        out.append({"text": text, "stage": stage, "run": n is not None, "named": bool(n and n[0]), "rank": n[1] if n else None,
                    "leader": n[2] if n else None, "was": bool(t and t[0]) if t is not None else None})
    asked = [p for p in out if p["run"]]
    return {"rows": out, "n": len(out), "asked": len(asked), "named": sum(1 for p in asked if p["named"]),
            "named_before": sum(1 for p in out if p["was"]), "first": sum(1 for p in asked if p["rank"] == 1),
            "won": [p["text"] for p in out if p["named"] and p["was"] is False], "lost": [p["text"] for p in out if p["was"] and p["run"] and not p["named"]],
            "cycle": cur, "prev": prev}

def headlines(kb, pb):
    """Three to five plain-language lines on what moved, for the overview."""
    out = []
    up = [r for r in kb["rows"] if r["before"] is not None and r["position"] is not None and r["before"] > 10.5 >= r["position"]]
    down = [r for r in kb["rows"] if r["before"] is not None and r["position"] is not None and r["before"] <= 10.5 < r["position"]]
    if up: out.append(("up", f"{len(up)} target keyword{'s' if len(up) > 1 else ''} moved onto page 1: " + ", ".join(r["keyword"] for r in up[:3])))
    best = max((r for r in kb["rows"] if r["change"]), key=lambda r: r["change"], default=None)
    if best and best["change"] >= 2: out.append(("up", f"Biggest climb: “{best['keyword']}” up {best['change']:g} places to {best['position']:.0f}"))
    if pb["won"]: out.append(("up", f"ChatGPT now names you for {len(pb['won'])} more prompt{'s' if len(pb['won']) > 1 else ''}, e.g. “{pb['won'][0]}”"))
    if down: out.append(("down", f"{len(down)} keyword{'s' if len(down) > 1 else ''} slipped off page 1: " + ", ".join(r["keyword"] for r in down[:3])))
    if pb["lost"]: out.append(("down", f"No longer named for “{pb['lost'][0]}”" + (f" and {len(pb['lost']) - 1} more" if len(pb["lost"]) > 1 else "")))
    if not out and (kb["n"] or pb["n"]): out.append(("flat", "No big moves since last month. Positions are holding."))
    return out[:5]

# ------------------------------------------------------------------ technical audit checklist
def tech_checklist(conn, client_id):
    """The crawler's checklist with a tick or a count per item, from the latest Orbit crawl. None before the first crawl."""
    import crawler
    if not conn.execute("SELECT 1 FROM tech_crawls WHERE client_id=%s AND source='hovers' AND finished_at IS NOT NULL LIMIT 1", [client_id]).fetchone():
        return None
    open_ = {r[0]: (r[1], r[2]) for r in conn.execute("""SELECT issue_code, pages, status FROM tech_issues
                                                         WHERE client_id=%s AND source='hovers' AND pages > 0""", [client_id])}
    out = []
    for group, items in crawler.CHECKLIST:
        rows = [{"code": code, "name": name, "severity": crawler.ISSUES[code][0], "pages": open_.get(code, (0, None))[0],
                 "status": open_.get(code, (0, None))[1]} for code, name in items]
        out.append({"group": group, "items": rows, "passed": sum(1 for r in rows if not r["pages"])})
    total = sum(len(g["items"]) for g in out)
    return {"groups": out, "passed": sum(g["passed"] for g in out), "total": total}

# ------------------------------------------------------------------ technical SEO index (Pro)
TECH_PILLARS = [
    ("crawl", "Crawlability and indexation", "Status codes, index directives, canonicalisation and redirects that decide what Googlebot can crawl and index.",
     ["page_4xx", "page_5xx", "noindex_in_sitemap", "canonical_broken", "canonical_other", "redirect_chain", "soft_404",
      "sitemap_bad_urls", "sitemap_missing", "sitemap_not_in_robots", "robots_missing"]),
    ("links", "Internal linking and link equity", "Broken and redirected internal links and orphan URLs that leak PageRank and crawl budget.",
     ["broken_internal_link", "link_to_redirect", "orphan_page"]),
    ("onpage", "On-page relevance and metadata", "Title tags, meta descriptions, heading hierarchy and image alt attributes that carry topical relevance and SERP CTR.",
     ["missing_title", "duplicate_title", "missing_description", "duplicate_description", "missing_h1", "multiple_h1", "img_missing_alt"]),
    ("intl", "Security and internationalisation", "HTTPS integrity (mixed content) and hreflang reciprocity for regional versions.",
     ["mixed_content", "hreflang_broken", "http_not_https", "www_both_live"]),
    ("perf", "Performance and Core Web Vitals", "Server response (TTFB) from the crawl, plus LCP, INP and CLS from PageSpeed (field data where CrUX has enough traffic).",
     ["slow_page"]),
]
SEV_W = {"error": 10, "warning": 4, "notice": 1}

def tech_index(conn, client_id):
    """Five technical SEO pillars scored 0-100 from open crawl issues (weighted by severity and affected URLs),
    plus Core Web Vitals from the latest PageSpeed results."""
    issues = {r[0]: r[1:] for r in conn.execute("""SELECT issue_code, severity, pages FROM tech_issues
                                                  WHERE client_id=%s AND source='hovers' AND status IN ('open','in_progress') AND pages > 0""", [client_id])}
    crawled = conn.execute("""SELECT pages_crawled FROM tech_crawls WHERE client_id=%s AND source='hovers' AND finished_at IS NOT NULL
                              ORDER BY finished_at DESC LIMIT 1""", [client_id]).fetchone()
    n = (crawled[0] if crawled else 0) or 0
    cwv = conn.execute("""SELECT avg(perf_score), percentile_cont(.75) WITHIN GROUP (ORDER BY coalesce(field_lcp_ms, lcp_ms)),
                                 percentile_cont(.75) WITHIN GROUP (ORDER BY field_inp_ms), percentile_cont(.75) WITHIN GROUP (ORDER BY coalesce(field_cls, cls)),
                                 percentile_cont(.75) WITHIN GROUP (ORDER BY ttfb_ms), count(*)
                          FROM (SELECT DISTINCT ON (url) * FROM pagespeed_results WHERE client_id=%s AND strategy='mobile' AND error IS NULL
                                ORDER BY url, fetched_at DESC) x""", [client_id]).fetchone()
    out = []
    for key, name, what, codes in TECH_PILLARS:
        hit = [(c, issues[c]) for c in codes if c in issues]
        penalty = sum(SEV_W[sev] * min(pages, max(n, 1)) / max(n, 1) * 10 for c, (sev, pages) in hit)
        score = None if not n else max(0, round(100 - penalty))
        out.append({"key": key, "name": name, "what": what, "score": score, "issues": len(hit),
                    "urls": sum(p for _, (s, p) in hit), "codes": [c for c, _ in hit]})
    vit = None
    if cwv and cwv[5]:
        vit = {"perf": round(float(cwv[0])) if cwv[0] is not None else None, "lcp": cwv[1], "inp": cwv[2], "cls": cwv[3], "ttfb": cwv[4], "urls": cwv[5]}
        if vit["perf"] is not None:
            p = next(x for x in out if x["key"] == "perf")
            p["score"] = round((vit["perf"] + (p["score"] if p["score"] is not None else vit["perf"])) / 2)
    scored = [x["score"] for x in out if x["score"] is not None]
    return {"pillars": out, "overall": round(sum(scored) / len(scored)) if scored else None, "crawled": n, "cwv": vit}
