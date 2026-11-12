#!/usr/bin/env python3
"""Semrush features beyond keywords: content ideas (questions and related searches), backlinks, keyword gap.

Every call goes through semrush.Semrush, so the run budget, the lifetime cap (SEMRUSH_TOTAL_CAP) and the
per-feature cap (SEMRUSH_CAP_IDEAS, SEMRUSH_CAP_BACKLINKS, SEMRUSH_CAP_GAP) are checked before each request.
Nothing here runs on a schedule: each is a button in Settings that shows its worst-case cost first.

Prices (Semrush API docs): phrase_questions and phrase_related 40 per line; backlinks_overview 40 per request;
backlinks_refdomains and backlinks_matrix 40 per line; domain_domains 80 per line.
"""
import argparse, datetime as dt, json, os, sys
from common import connect
import semrush as sr
from semrush import Semrush, BudgetExceeded, num, col

IDEA_COLS = "Ph,Nq,Kd,In,Fk"

# ---------------------------------------------------------------- estimates (shown on the buttons before anything is spent)
def estimate(feature, **n):
    if feature == "ideas":     return 40 * n.get("per_seed", 10) * n.get("seeds", 3) * (2 if n.get("related", True) else 1)
    if feature == "backlinks": return 40 * (1 + n.get("competitors", 3)) + 40 * n.get("refdomains", 20) + 40 * n.get("gap", 30)
    if feature == "gap":       return 80 * n.get("lines", 30)
    raise ValueError(feature)

def domain_of(conn, client_id):
    d = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    return d.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")

def tracked_competitors(conn, client_id, n=4):
    return [r[0] for r in conn.execute("""SELECT domain FROM competitors WHERE client_id=%s AND tracked AND domain IS NOT NULL
                                          ORDER BY common_keywords DESC NULLS LAST LIMIT %s""", [client_id, n])]

# ---------------------------------------------------------------- content ideas
def default_seeds(conn, client_id, n=3):
    """Topics the brand already sells: keywords of its top collection pages, else its top non-branded searches."""
    seeds = [r[0] for r in conn.execute("""SELECT primary_keyword FROM pages WHERE client_id=%s AND page_type='collection'
                                           AND primary_keyword IS NOT NULL ORDER BY search_volume DESC NULLS LAST, url LIMIT %s""", [client_id, n])]
    if len(seeds) < n:
        import gsc
        name, dom, aliases = conn.execute("SELECT name, domain, aliases FROM clients WHERE client_id=%s", [client_id]).fetchone()
        rival = gsc.competitor_terms(conn, client_id, gsc.brand_terms(name, dom, aliases))     # never a competitor's name as a topic
        seeds += [r[0] for r in conn.execute("""SELECT query FROM gsc_search_daily WHERE client_id=%s AND NOT is_branded
                                                AND date >= current_date - 90 GROUP BY query ORDER BY sum(clicks) DESC LIMIT %s""",
                                             [client_id, n * 3]) if r[0] not in seeds and not any(x in r[0].lower().replace(" ", "") for x in rival)]
    return seeds[:n]

def parse_ideas(rows):
    out = []
    for r in rows:
        kw = col(r, "Keyword", "Ph")
        if not kw: continue
        intents = (col(r, "Intents", "In") or "").split(",")
        out.append({"keyword": kw, "search_volume": num(col(r, "Search Volume", "Nq"), int), "kd": num(col(r, "Keyword Difficulty", "Kd")),
                    "intent": ",".join(sr.INTENTS.get(i.strip(), i.strip()) for i in intents if i.strip()) or None,
                    "serp_features": col(r, "SERP Features", "Keywords SERP Features", "Fk") or None})
    return out

def sync_ideas(conn, client_id, key, seeds=None, per_seed=10, related=True, database="in", max_units=1200, session=None, today=None):
    day = today or dt.date.today()
    seeds = seeds or default_seeds(conn, client_id)
    sm = Semrush(key, conn, client_id, max_units, session, feature="ideas")
    done, n = [], 0
    try:
        for seed in seeds:
            for kind, report in [("question", "phrase_questions")] + ([("related", "phrase_related")] if related else []):
                ideas = parse_ideas(sm.call(report, per_seed, phrase=seed, database=database, export_columns=IDEA_COLS, display_sort="nq_desc"))
                with conn.cursor() as cur:
                    cur.executemany("""INSERT INTO keyword_ideas (client_id, seed, kind, keyword, search_volume, kd, intent, serp_features, pulled_on)
                                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, kind, keyword) DO UPDATE SET
                                       search_volume=EXCLUDED.search_volume, kd=EXCLUDED.kd, serp_features=EXCLUDED.serp_features, pulled_on=EXCLUDED.pulled_on""",
                                    [(client_id, seed, kind, i["keyword"], i["search_volume"], i["kd"], i["intent"], i["serp_features"], day) for i in ideas
                                     if i["keyword"].lower() != seed.lower()])
                n += len(ideas)
        done.append(f"{n} ideas from {len(seeds)} topics")
    except BudgetExceeded as e:
        done.append(f"{n} ideas, stopped: {e}")
    return ", ".join(done) + f" ({sm.spent} units)"

# ---------------------------------------------------------------- backlinks
def ts_date(v):
    """Backlinks API dates are Unix seconds."""
    v = num(v, int)
    return dt.datetime.utcfromtimestamp(v).date() if v else None

def parse_matrix(rows, targets):
    """Backlink gap rows: one per referring domain, with a backlink count per target (same order as targets).
    Semrush returns the counts as backlinks_num_0..N or as one list; both are read. Domains already linking to the
    client (the last target) are left out: what remains are off-page prospects."""
    out = []
    for r in rows:
        dom = col(r, "domain", "Domain")
        if not dom: continue
        counts = [num(r.get(f"backlinks_num_{i}"), int) for i in range(len(targets))]
        if all(c is None for c in counts) and r.get("backlinks_num"):
            parts = [p for p in str(r["backlinks_num"]).replace("|", ",").split(",")]
            counts = [num(p.strip(), int) for p in parts][:len(targets)] + [None] * max(0, len(targets) - len(parts))
        links = {t: c for t, c in zip(targets[:-1], counts[:-1]) if c}
        if counts[-1] is None or counts[-1] or not links: continue     # unknown or already linking to the client: not a prospect
        out.append({"domain": dom, "ascore": num(col(r, "domain_ascore", "ascore"), int), "links_to": links})
    return out

def sync_backlinks(conn, client_id, key, competitors=3, refdomains=20, gap=30, max_units=2500, session=None, today=None):
    day = today or dt.date.today()
    me = domain_of(conn, client_id)
    comps = tracked_competitors(conn, client_id, competitors)
    sm = Semrush(key, conn, client_id, max_units, session, feature="backlinks")
    done = []
    try:
        for d in [me] + comps:
            rows = sm.call("backlinks_overview", 1, target=d, target_type="root_domain",
                           export_columns="ascore,total,domains_num,follows_num,nofollows_num")
            if rows:
                r = rows[0]
                conn.execute("""INSERT INTO backlink_overview (client_id, domain, pulled_on, ascore, backlinks, refdomains, follows, nofollows)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, domain, pulled_on) DO UPDATE SET ascore=EXCLUDED.ascore,
                                backlinks=EXCLUDED.backlinks, refdomains=EXCLUDED.refdomains, follows=EXCLUDED.follows, nofollows=EXCLUDED.nofollows""",
                             [client_id, d, day, num(r.get("ascore"), int), num(r.get("total"), int), num(r.get("domains_num"), int),
                              num(r.get("follows_num"), int), num(r.get("nofollows_num"), int)])
        done.append(f"authority for {1 + len(comps)} sites")
        if refdomains:
            rows = sm.call("backlinks_refdomains", refdomains, target=me, target_type="root_domain", display_sort="domain_ascore_desc",
                           export_columns="domain_ascore,domain,backlinks_num,first_seen,last_seen")
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO backlink_refdomains (client_id, pulled_on, domain, ascore, backlinks, first_seen, last_seen)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, pulled_on, domain) DO NOTHING""",
                                [(client_id, day, r.get("domain"), num(r.get("domain_ascore"), int), num(r.get("backlinks_num"), int),
                                  ts_date(r.get("first_seen")), ts_date(r.get("last_seen"))) for r in rows if r.get("domain")])
            done.append(f"{len(rows)} sites linking to {me}")
        if gap and comps:
            targets = comps + [me]
            rows = sm.call("backlinks_matrix", gap, **{"targets[]": targets, "target_types[]": ["root_domain"] * len(targets)},
                           export_columns="domain,domain_ascore,matches_num,backlinks_num", display_sort="domain_ascore_desc")
            prospects = parse_matrix(rows, targets)
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO backlink_gap (client_id, pulled_on, domain, ascore, links_to) VALUES (%s,%s,%s,%s,%s)
                                   ON CONFLICT (client_id, pulled_on, domain) DO UPDATE SET ascore=EXCLUDED.ascore, links_to=EXCLUDED.links_to""",
                                [(client_id, day, p["domain"], p["ascore"], json.dumps(p["links_to"])) for p in prospects])
            done.append(f"{len(prospects)} link prospects")
    except BudgetExceeded as e:
        done.append(f"stopped: {e}")
    return ", ".join(done) + f" ({sm.spent} units)"

# ---------------------------------------------------------------- keyword gap (the existing report, on its own button)
def sync_gap(conn, client_id, key, lines=30, database="in", max_units=2400, session=None, today=None):
    day = today or dt.date.today()
    me, comps = domain_of(conn, client_id), tracked_competitors(conn, client_id)
    if not comps: return "no tracked competitors: tick some on the Competitors screen first (0 units)"
    sm = Semrush(key, conn, client_id, max_units, session, feature="gap")
    try:
        doms = comps + [me]
        gap = sr.parse_gap(sm.call("domain_domains", lines, domains=sr.gap_domains_param(me, comps), database=database,
                                   export_columns="Ph," + ",".join(f"P{i}" for i in range(len(doms))) + ",Nq,Cp,Co", display_sort="nq_desc"), doms)
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO keyword_gaps (client_id, database, pulled_on, keyword, search_volume, cpc, competition, positions)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, database, pulled_on, keyword) DO UPDATE SET positions=EXCLUDED.positions""",
                            [(client_id, database, day, g["keyword"], g["search_volume"], g["cpc"], g["competition"], json.dumps(g["positions"])) for g in gap])
        msg = f"{len(gap)} untapped keywords vs {len(comps)} competitors"
    except BudgetExceeded as e:
        msg = f"stopped: {e}"
    return msg + f" ({sm.spent} units)"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("feature", choices=["ideas", "backlinks", "gap"]); ap.add_argument("--client-id", required=True)
    ap.add_argument("--max-units", type=int, required=True)      # always explicit: no default spend
    ap.add_argument("--seed", action="append"); ap.add_argument("--per-seed", type=int, default=10)
    ap.add_argument("--lines", type=int, default=30)
    a = ap.parse_args()
    key = os.environ["SEMRUSH_API_KEY"]
    with connect() as conn:
        if a.feature == "ideas": print(sync_ideas(conn, a.client_id, key, a.seed, a.per_seed, max_units=a.max_units))
        elif a.feature == "backlinks": print(sync_backlinks(conn, a.client_id, key, gap=a.lines, max_units=a.max_units))
        else: print(sync_gap(conn, a.client_id, key, a.lines, max_units=a.max_units))
