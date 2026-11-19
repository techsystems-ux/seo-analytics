"""Data for the Tesseract-style screens: the brand and its tracked competitors as switchable entities, AI results per
engine (ChatGPT today; Gemini, Perplexity, AI Overviews and Copilot appear once runs exist), prompts against each
engine, Google AI Overview keywords from Semrush, and AI referral traffic from GA4 next to organic search.
Read-only; every query is scoped to one brand and runs under row-level security like the rest of the app."""
import datetime as dt
from analytics import rows
import viz

# engine key (as stored in runs/scores_daily), label, domain for the logo, GA4 source patterns
ENGINES = [("chatgpt", "ChatGPT", "chatgpt.com", ("chatgpt", "openai")),
           ("ai_overviews", "AI Overviews", "google.com", ()),
           ("gemini", "Gemini", "gemini.google.com", ("gemini", "bard")),
           ("perplexity", "Perplexity", "perplexity.ai", ("perplexity",)),
           ("bing_copilot", "Copilot", "copilot.microsoft.com", ("copilot", "bing")),
           ("claude", "Claude", "claude.ai", ("claude", "anthropic"))]
LABEL = {k: l for k, l, _, _ in ENGINES}

def logo(domain, size=64):
    return f"https://www.google.com/s2/favicons?domain={domain}&sz={size}"

def engines(conn, cid):
    """All engines with tracked = whether Orbit has any answers from it for this brand."""
    have = {r[0] for r in conn.execute("SELECT DISTINCT engine::text FROM scores_daily WHERE client_id=%s", [cid])}
    return [{"key": k, "label": l, "logo": logo(d), "tracked": k in have} for k, l, d, _ in ENGINES]

def source_engine(source):
    s = (source or "").lower()
    for k, _, _, pats in ENGINES:
        if any(p in s for p in pats): return k
    return "other"

def entities(conn, cid):
    """The brand first (key ''), then tracked competitors, each with a fixed colour and initial."""
    name, domain = conn.execute("SELECT name, domain FROM clients WHERE client_id=%s", [cid]).fetchone()
    out = [{"key": "", "name": name, "domain": domain, "primary": True}]
    for n, d in conn.execute("""SELECT name, domain FROM competitors WHERE client_id=%s AND tracked AND NOT coalesce(own_brand,false)
                                ORDER BY common_keywords DESC NULLS LAST, name""", [cid]):
        out.append({"key": n, "name": n, "domain": d, "primary": False})
    for i, e in enumerate(out):
        e.update(colour=viz.color(i), initial=(e["name"] or "?")[:1].upper(), logo=logo(e["domain"]) if e["domain"] else None, idx=i)
    return out

def pick(ents, key):
    return next((e for e in ents if e["key"] == (key or "")), ents[0])

def _f(v): return float(v) if v is not None else None

def ai_board(conn, cid, ents):
    """Latest AI numbers per entity and engine, weekly visibility trend, mentions, citations and sentiment."""
    names = [e["key"] for e in ents]
    latest = rows(conn.execute("""SELECT DISTINCT ON (engine, brand) engine::text, brand, date, visibility_score, mention_rate, citation_rate,
                                         share_of_voice, avg_position, sentiment, samples
                                  FROM scores_daily WHERE client_id=%s ORDER BY engine, brand, date DESC""", [cid]),
                  ["engine", "brand", "date", "score", "mention_rate", "citation_rate", "sov", "avg_position", "sentiment", "samples"])
    by = {}
    for r in latest:
        if r["brand"] in names: by.setdefault(r["brand"], {})[r["engine"]] = r
    # the score four weeks before the latest, for change
    before = {(r[0], r[1]): _f(r[2]) for r in conn.execute("""
        SELECT DISTINCT ON (s.engine, s.brand) s.engine::text, s.brand, s.visibility_score FROM scores_daily s
        JOIN (SELECT engine, brand, max(date) d FROM scores_daily WHERE client_id=%s GROUP BY 1,2) m USING (engine, brand)
        WHERE s.client_id=%s AND s.date <= m.d - 21 ORDER BY s.engine, s.brand, s.date DESC""", [cid, cid])}
    # weekly trend (ChatGPT, or the first engine with data)
    eng = "chatgpt" if any("chatgpt" in v for v in by.values()) else next((k for v in by.values() for k in v), None)
    trend = rows(conn.execute("""SELECT date_trunc('week', date)::date w, brand, avg(visibility_score) FROM scores_daily
                                 WHERE client_id=%s AND engine::text=%s AND date > current_date - 120 GROUP BY 1,2 ORDER BY 1""", [cid, eng]),
                 ["week", "brand", "score"]) if eng else []
    weeks = sorted({t["week"] for t in trend})
    tmap = {(t["brand"], t["week"]): _f(t["score"]) for t in trend}
    # mentions and citations in the last 30 days, per entity and engine
    ment = {}
    for brand, is_client, engine, n in conn.execute("""
            SELECT m.brand, m.is_client, r.engine::text, count(DISTINCT m.answer_id) FROM mentions m JOIN answers a USING (answer_id)
            JOIN runs r USING (run_id) WHERE m.client_id=%s AND r.run_at > now() - interval '30 days' AND m.rank_in_answer IS NOT NULL
            GROUP BY 1,2,3""", [cid]):
        key = "" if is_client else brand
        ment.setdefault(key, {})[engine] = ment.get(key, {}).get(engine, 0) + n
    dom = {e["key"]: (e["domain"] or "").lower().replace("www.", "") for e in ents}
    cites = {}
    for domain, is_client, n in conn.execute("""SELECT c.domain, bool_or(c.is_client), count(*) FROM citations c JOIN answers a USING (answer_id)
                                                JOIN runs r USING (run_id) WHERE c.client_id=%s AND c.cited AND r.run_at > now() - interval '30 days'
                                                GROUP BY 1""", [cid]):
        d = (domain or "").lower().replace("www.", "")
        for k, v in dom.items():
            if (k == "" and is_client) or (v and d.endswith(v)): cites[k] = cites.get(k, 0) + n
    sent = {}
    for is_client, brand, engine, pos, tot in conn.execute("""
            SELECT m.is_client, m.brand, r.engine::text, count(*) FILTER (WHERE m.sentiment='positive'), count(*) FILTER (WHERE m.sentiment IS NOT NULL)
            FROM mentions m JOIN answers a USING (answer_id) JOIN runs r USING (run_id)
            WHERE m.client_id=%s AND r.run_at > now() - interval '30 days' GROUP BY 1,2,3""", [cid]):
        key = "" if is_client else brand
        p, t = sent.get(key, {}).get(engine, (0, 0))
        sent.setdefault(key, {})[engine] = (p + pos, t + tot)
    answers = conn.execute("""SELECT count(*) FROM answers a JOIN runs r USING (run_id) WHERE a.client_id=%s
                              AND r.run_at > now() - interval '30 days'""", [cid]).fetchone()[0]
    out = []
    for e in ents:
        per = by.get(e["key"], {})
        main = per.get(eng) if eng else None
        sc = _f(main["score"]) if main else None
        b4 = before.get((eng, e["key"]))
        out.append({**e, "engines": per, "score": sc, "change": round(sc - b4, 1) if sc is not None and b4 is not None else None,
                    "mention_rate": _f(main["mention_rate"]) if main else None, "sov": _f(main["sov"]) if main else None,
                    "avg_position": _f(main["avg_position"]) if main else None,
                    "mentions": sum(ment.get(e["key"], {}).values()), "mentions_by": ment.get(e["key"], {}),
                    "citations": cites.get(e["key"], 0),
                    "sentiment_by": {k: (p / t if t else None) for k, (p, t) in sent.get(e["key"], {}).items()},
                    "trend": [tmap.get((e["key"], w)) for w in weeks]})
    return {"engine": eng, "weeks": weeks, "ents": out, "answers": answers}

def prompt_matrix(conn, cid, key, ents, engs):
    """Every approved prompt: for each engine, is the chosen entity named in the latest run (None = not asked), its place,
    and how many tracked brands the answer named."""
    me = pick(ents, key)
    names = {e["name"].lower() for e in ents if not e["primary"]}
    cycles = {r[0]: r[1] for r in conn.execute("SELECT engine::text, max(cycle) FROM runs WHERE client_id=%s AND status='ok' GROUP BY 1", [cid])}
    res = {}
    for engine, cyc in cycles.items():
        for pid, aid, is_c, brand, rank in conn.execute("""
                SELECT r.prompt_id, a.answer_id, m.is_client, m.brand, m.rank_in_answer FROM runs r JOIN answers a USING (run_id)
                LEFT JOIN mentions m ON m.answer_id=a.answer_id AND m.rank_in_answer IS NOT NULL
                WHERE r.client_id=%s AND r.engine::text=%s AND r.cycle=%s AND r.sample_no=1""", [cid, engine, cyc]):
            cell = res.setdefault(pid, {}).setdefault(engine, {"asked": True, "named": False, "rank": None, "answer_id": str(aid), "brands": 0})
            if brand is None: continue
            hit = (me["primary"] and is_c) or (not me["primary"] and not is_c and brand.lower() == me["name"].lower())
            if hit: cell.update(named=True, rank=rank)
            if is_c or brand.lower() in names: cell["brands"] += 1
    out = []
    for pid, text, stage in conn.execute("""SELECT prompt_id, text, funnel_stage::text FROM prompts WHERE client_id=%s AND approved AND active
                                           ORDER BY funnel_stage, text""", [cid]):
        cells = res.get(pid, {})
        named = sum(1 for c in cells.values() if c["named"])
        out.append({"id": str(pid), "text": text, "stage": stage, "cells": cells, "named": named,
                    "brands": max((c["brands"] for c in cells.values()), default=0)})
    tracked = [e for e in engs if e["tracked"]]
    asked = [p for p in out if p["cells"]]
    return {"rows": out, "asked": len(asked), "named": sum(1 for p in asked if p["named"]), "engines": tracked,
            "cycle": max(cycles.values()) if cycles else None}

def aio_keywords(conn, cid, limit=50):
    """Google keywords whose results page shows an AI Overview (Semrush feature 52), and whether the site is cited in it."""
    dom = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [cid]).fetchone()[0]
    dom = dom.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    last = conn.execute("SELECT max(pulled_on) FROM semrush_keywords WHERE client_id=%s AND domain=%s", [cid, dom]).fetchone()[0]
    if not last: return {"date": None, "rows": [], "with": 0, "owned": 0, "total": 0}
    allk = rows(conn.execute("""SELECT keyword, position, search_volume, serp_features, serp_features_owned, url FROM semrush_keywords
                                WHERE client_id=%s AND domain=%s AND pulled_on=%s ORDER BY search_volume DESC NULLS LAST""", [cid, dom, last]),
                ["keyword", "position", "volume", "fk", "fp", "url"])
    has = lambda s: "52" in [c.strip() for c in (s or "").split(",")]
    w = [k for k in allk if has(k["fk"])]
    for k in w: k["owned"] = has(k["fp"])
    return {"date": last, "rows": w[:limit], "with": len(w), "owned": sum(1 for k in w if k["owned"]), "total": len(allk)}

def search_overview(kb, aio):
    """Three 0-100 bars for the dashboard: cited in AI Overviews, tracked keywords in the top 10, and ranked at all."""
    n = kb["n"]
    ranked = sum(1 for r in kb["rows"] if r["position"] is not None)
    return [{"label": "Cited in AI Overviews", "value": round(100 * aio["owned"] / aio["with"]) if aio["with"] else None,
             "note": f"{aio['owned']} of {aio['with']} keywords that show one" if aio["with"] else "Needs Semrush keyword data"},
            {"label": "Top 10 on Google", "value": round(100 * kb["top10"] / n) if n else None,
             "note": f"{kb['top10']} of {n} tracked keywords" if n else "Add target keywords in Settings"},
            {"label": "Ranking anywhere", "value": round(100 * ranked / n) if n else None,
             "note": f"{ranked} of {n} tracked keywords" if n else ""}]

def ai_traffic(conn, cid, days=28):
    """Sessions from AI engines (GA4 referrals) next to organic search, for the same window, plus weekly trend and sources."""
    src = rows(conn.execute("""SELECT source, sum(sessions), sum(engaged_sessions), sum(key_events), sum(transactions), sum(revenue), sum(new_users)
                               FROM ga4_ai_referrals WHERE client_id=%s AND date > current_date - %s GROUP BY 1""", [cid, days]),
               ["source", "sessions", "engaged", "key_events", "transactions", "revenue", "new_users"])
    by = {}
    for r in src:
        k = source_engine(r["source"])
        b = by.setdefault(k, {"engine": k, "label": LABEL.get(k, "Other AI"), "sessions": 0, "engaged": 0, "key_events": 0, "transactions": 0,
                              "revenue": 0.0, "new_users": 0, "sources": []})
        for f in ("sessions", "engaged", "key_events", "transactions", "new_users"): b[f] += int(r[f] or 0)
        b["revenue"] += float(r["revenue"] or 0); b["sources"].append(r["source"])
    dom = dict((k, d) for k, _, d, _ in ENGINES)
    srcs = sorted(by.values(), key=lambda b: -b["sessions"])
    for b in srcs: b["logo"] = logo(dom.get(b["engine"], "openai.com"))
    def tot(rs):
        t = {f: sum((r.get(f) or 0) for r in rs) for f in ("sessions", "engaged", "key_events", "transactions", "revenue", "new_users")}
        t["engagement"] = t["engaged"] / t["sessions"] if t["sessions"] else None
        t["conversion"] = t["transactions"] / t["sessions"] if t["sessions"] else None
        return t
    ai = tot(srcs)
    org = rows(conn.execute("""SELECT sum(sessions), sum(engaged_sessions), sum(key_events), sum(transactions), sum(revenue), sum(new_users)
                               FROM ga4_daily WHERE client_id=%s AND channel_group='Organic Search' AND date > current_date - %s""", [cid, days]),
               ["sessions", "engaged", "key_events", "transactions", "revenue", "new_users"])
    org = tot([{k: float(v or 0) for k, v in org[0].items()}] if org else [])
    weekly = rows(conn.execute("""SELECT date_trunc('week', date)::date, sum(sessions) FROM ga4_ai_referrals WHERE client_id=%s
                                  AND date > current_date - 98 AND date < date_trunc('week', current_date) GROUP BY 1 ORDER BY 1""", [cid]), ["week", "sessions"])
    oweekly = dict(conn.execute("""SELECT date_trunc('week', date)::date, sum(sessions) FROM ga4_daily WHERE client_id=%s
                                   AND channel_group='Organic Search' AND date > current_date - 98 AND date < date_trunc('week', current_date) GROUP BY 1""", [cid]).fetchall())
    last = conn.execute("SELECT max(date) FROM ga4_ai_referrals WHERE client_id=%s", [cid]).fetchone()[0]
    return {"sources": srcs, "ai": ai, "organic": org, "days": days, "last": last,
            "weeks": [w["week"] for w in weekly], "ai_weekly": [float(w["sessions"] or 0) for w in weekly],
            "org_weekly": [float(oweekly.get(w["week"]) or 0) for w in weekly],
            "share": ai["sessions"] / (ai["sessions"] + org["sessions"]) if (ai["sessions"] + org["sessions"]) else None}

def citation_sov(conn, cid, days=30, limit=20):
    """AI citations, Tesseract's Brand SOV: total and unique cited links, the share of answers per engine that cite the
    brand's own site, weekly coverage, and every cited domain ranked by its share of all citations."""
    tot, uniq = conn.execute("""SELECT count(*), count(DISTINCT c.url) FROM citations c JOIN answers a USING (answer_id) JOIN runs r USING (run_id)
                                WHERE c.client_id=%s AND c.cited AND r.run_at > now() - make_interval(days => %s)""", [cid, days]).fetchone()
    per = {e: (n, k) for e, n, k in conn.execute("""
        SELECT r.engine::text, count(DISTINCT a.answer_id),
               count(DISTINCT a.answer_id) FILTER (WHERE EXISTS (SELECT 1 FROM citations c WHERE c.answer_id=a.answer_id AND c.cited AND c.is_client))
        FROM answers a JOIN runs r USING (run_id) WHERE a.client_id=%s AND r.run_at > now() - make_interval(days => %s) GROUP BY 1""", [cid, days])}
    weekly = rows(conn.execute("""
        SELECT date_trunc('week', r.run_at)::date, r.engine::text, count(DISTINCT a.answer_id),
               count(DISTINCT a.answer_id) FILTER (WHERE EXISTS (SELECT 1 FROM citations c WHERE c.answer_id=a.answer_id AND c.cited AND c.is_client))
        FROM answers a JOIN runs r USING (run_id) WHERE a.client_id=%s AND r.run_at > now() - interval '120 days' GROUP BY 1,2 ORDER BY 1""", [cid]),
        ["week", "engine", "answers", "cited"])
    doms = rows(conn.execute("""SELECT c.domain, bool_or(c.is_client), count(*), count(DISTINCT c.url) FROM citations c JOIN answers a USING (answer_id)
                                JOIN runs r USING (run_id) WHERE c.client_id=%s AND c.cited AND r.run_at > now() - make_interval(days => %s)
                                GROUP BY 1 ORDER BY 3 DESC LIMIT %s""", [cid, days, limit]), ["domain", "is_client", "citations", "urls"])
    for i, d in enumerate(doms):
        d["rank"], d["share"], d["logo"] = i + 1, (d["citations"] / tot if tot else 0), logo(d["domain"], 32)
    return {"total": tot or 0, "unique": uniq or 0, "per": {k: {"answers": n, "cited": c, "rate": c / n if n else None} for k, (n, c) in per.items()},
            "weekly": weekly, "domains": doms, "days": days}
