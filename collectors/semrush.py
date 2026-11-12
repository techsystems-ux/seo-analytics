#!/usr/bin/env python3
"""Semrush collector: the client's organic keywords, its organic competitors, and untapped keywords (competitors rank, client does not).

Every call is logged with the API units it cost (semrush_usage). A run stops before it would pass --max-units,
and the lifetime total can never pass SEMRUSH_TOTAL_CAP. Unit prices (per line returned, current data):
domain_organic 10, domain_organic_organic 40, domain_domains 80.

Hard limit: SEMRUSH_TOTAL_CAP (default 500) units across all calls ever made, including any loaded from seed files.
Usage: python semrush.py --client-id UUID [--database in] [--keywords 20] [--competitors 5] [--gap 0] [--max-units 500]
Env: SEMRUSH_API_KEY
"""
import argparse, csv, datetime as dt, io, json, os, sys
from urllib.parse import urlencode
import requests
from common import connect

API = "https://api.semrush.com/"
UNIT_COST = {"domain_organic": 10, "domain_organic_organic": 40, "domain_domains": 80}
ORGANIC_COLS = "Ph,Po,Pp,Nq,Cp,Ur,Tr,Co,Kd,In"
COMPETITOR_COLS = "Dn,Cr,Np,Or,Ot,Oc,Ad"
INTENTS = {"0": "commercial", "1": "informational", "2": "navigational", "3": "transactional"}

class BudgetExceeded(Exception): pass

TOTAL_CAP = int(os.environ.get("SEMRUSH_TOTAL_CAP", "500"))   # lifetime limit for the whole product, every brand, every run

def lifetime_units(conn):
    return conn.execute("SELECT coalesce(sum(units),0) FROM semrush_usage").fetchone()[0]

class Semrush:
    def __init__(self, key, conn, client_id, max_units, session=None):
        self.key, self.conn, self.client_id = key, conn, client_id
        self.max_units = max(0, min(max_units, TOTAL_CAP - lifetime_units(conn)))   # never past the lifetime cap
        self.s, self.spent = session or requests.Session(), 0

    def call(self, report, limit, **params):
        cost = UNIT_COST[report] * limit                       # worst case: every requested line comes back
        if self.spent + cost > self.max_units:
            raise BudgetExceeded(f"{report} could cost {cost} units; {self.max_units - self.spent} left (lifetime cap {TOTAL_CAP})")
        q = {"type": report, "key": self.key, "display_limit": limit, **params}
        r = self.s.get(API + "?" + urlencode(q), timeout=120)
        text = r.text.strip()
        if r.status_code != 200 or text.startswith("ERROR"):
            if "NOTHING FOUND" in text: rows = []
            else: raise RuntimeError(f"Semrush {report}: {text[:200]}")    # the key is never echoed back in errors
        else:
            rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
        units = UNIT_COST[report] * len(rows)
        self.spent += units
        self.conn.execute("INSERT INTO semrush_usage (client_id, report, lines, units) VALUES (%s,%s,%s,%s)",
                          [self.client_id, report, len(rows), units])
        return rows

def num(v, cast=float):
    try: return cast(v) if v not in (None, "", "n/a") else None
    except ValueError: return None

def col(row, *names):
    for n in names:
        if n in row: return row[n]
    return None

def parse_organic(rows):
    out = []
    for r in rows:
        intents = (col(r, "Intents", "In") or "").split(",")
        out.append({"keyword": col(r, "Keyword", "Ph"), "position": num(col(r, "Position", "Po"), int),
                    "prev_position": num(col(r, "Previous Position", "Pp"), int), "search_volume": num(col(r, "Search Volume", "Nq"), int),
                    "cpc": num(col(r, "CPC", "Cp")), "url": col(r, "Url", "Ur"), "traffic_pct": num(col(r, "Traffic (%)", "Tr")),
                    "competition": num(col(r, "Competition", "Co")), "kd": num(col(r, "Keyword Difficulty", "Kd")),
                    "intent": ",".join(INTENTS.get(i.strip(), i.strip()) for i in intents if i.strip()) or None})
    return [o for o in out if o["keyword"]]

def parse_competitors(rows):
    return [{"domain": col(r, "Domain", "Dn"), "relevance": num(col(r, "Competitor Relevance", "Cr")),
             "common_keywords": num(col(r, "Common Keywords", "Np"), int), "organic_keywords": num(col(r, "Organic Keywords", "Or"), int),
             "organic_traffic": num(col(r, "Organic Traffic", "Ot"), int)} for r in rows if col(r, "Domain", "Dn")]

def parse_gap(rows, domains):
    out = []
    for r in rows:
        kw = col(r, "Keyword", "Ph")
        if not kw: continue
        pos = {}
        for i, d in enumerate(domains):                          # columns come back named by domain, or as P0..P4
            v = r.get(d, r.get(f"P{i}"))
            pos[d] = num(v, int) or None                         # 0 means "does not rank"
        out.append({"keyword": kw, "search_volume": num(col(r, "Search Volume", "Nq"), int), "cpc": num(col(r, "CPC", "Cp")),
                    "competition": num(col(r, "Competition", "Co")), "positions": pos})
    return out

def gap_domains_param(client_domain, competitors):
    """Semrush "untapped keywords": every keyword any tracked competitor ranks for, minus the ones the client ranks for.
    Syntax is sign|type|domain repeated; the first sign is '*', '+' adds a domain's keywords, '-' removes them."""
    parts = []
    for i, d in enumerate(competitors): parts += ["*" if i == 0 else "+", "or", d]
    return "|".join(parts + ["-", "or", client_domain])

def store_keywords(conn, client_id, domain, database, day, kws):
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO semrush_keywords (client_id, domain, database, pulled_on, keyword, position, prev_position, search_volume,
                             cpc, url, traffic_pct, competition, kd, intent)
                           VALUES (%(c)s,%(d)s,%(db)s,%(day)s,%(keyword)s,%(position)s,%(prev_position)s,%(search_volume)s,%(cpc)s,%(url)s,
                             %(traffic_pct)s,%(competition)s,%(kd)s,%(intent)s)
                           ON CONFLICT (client_id, domain, database, pulled_on, keyword) DO UPDATE SET position=EXCLUDED.position,
                             search_volume=EXCLUDED.search_volume, url=EXCLUDED.url, traffic_pct=EXCLUDED.traffic_pct""",
                        [{**k, "c": client_id, "d": domain, "db": database, "day": day} for k in kws])

def sync_client(conn, client_id, key, database="in", n_keywords=20, n_competitors=5, n_gap=0, comp_keywords=0,
                max_units=500, session=None, today=None):
    day = today or dt.date.today()
    domain = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    domain = domain.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    sm = Semrush(key, conn, client_id, max_units, session)
    done = []
    try:
        kws = parse_organic(sm.call("domain_organic", n_keywords, domain=domain, database=database,
                                    export_columns=ORGANIC_COLS, display_sort="tr_desc"))
        store_keywords(conn, client_id, domain, database, day, kws); done.append(f"{len(kws)} keywords")

        comps = parse_competitors(sm.call("domain_organic_organic", n_competitors, domain=domain, database=database, export_columns=COMPETITOR_COLS))
        for c in comps:
            conn.execute("""INSERT INTO competitors (client_id, name, domain, source, tracked, relevance, common_keywords, organic_keywords, organic_traffic, pulled_on)
                            VALUES (%s,%s,%s,'semrush',false,%s,%s,%s,%s,%s)
                            ON CONFLICT (client_id, name) DO UPDATE SET relevance=EXCLUDED.relevance, common_keywords=EXCLUDED.common_keywords,
                              organic_keywords=EXCLUDED.organic_keywords, organic_traffic=EXCLUDED.organic_traffic, pulled_on=EXCLUDED.pulled_on""",
                         [client_id, c["domain"], c["domain"], c["relevance"], c["common_keywords"], c["organic_keywords"], c["organic_traffic"], day])
        done.append(f"{len(comps)} competitors")

        tracked = [r[0] for r in conn.execute("""SELECT domain FROM competitors WHERE client_id=%s AND tracked AND domain IS NOT NULL
                                                 ORDER BY common_keywords DESC NULLS LAST LIMIT 4""", [client_id])]
        if tracked and n_gap:
            doms = tracked + [domain]                           # response columns follow this order
            gap = parse_gap(sm.call("domain_domains", n_gap, domains=gap_domains_param(domain, tracked), database=database,
                                    export_columns="Ph," + ",".join(f"P{i}" for i in range(len(doms))) + ",Nq,Cp,Co", display_sort="nq_desc"), doms)
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO keyword_gaps (client_id, database, pulled_on, keyword, search_volume, cpc, competition, positions)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (client_id, database, pulled_on, keyword) DO UPDATE SET positions=EXCLUDED.positions""",
                                [(client_id, database, day, g["keyword"], g["search_volume"], g["cpc"], g["competition"], json.dumps(g["positions"])) for g in gap])
            done.append(f"{len(gap)} untapped keywords vs {len(tracked)} competitors")
        for d in tracked[:comp_keywords and 4]:                  # optional: competitors' own top keywords
            ck = parse_organic(sm.call("domain_organic", comp_keywords, domain=d, database=database, export_columns=ORGANIC_COLS, display_sort="tr_desc"))
            store_keywords(conn, client_id, d, database, day, ck); done.append(f"{len(ck)} keywords for {d}")
    except BudgetExceeded as e:
        done.append(f"stopped: {e}")
    return ", ".join(done) + f" ({sm.spent} units)"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True); ap.add_argument("--database", default="in")
    ap.add_argument("--keywords", type=int, default=20); ap.add_argument("--competitors", type=int, default=5)
    ap.add_argument("--gap", type=int, default=0); ap.add_argument("--competitor-keywords", type=int, default=0)
    ap.add_argument("--max-units", type=int, default=500)
    a = ap.parse_args()
    with connect() as conn:
        print(sync_client(conn, a.client_id, os.environ["SEMRUSH_API_KEY"], a.database, a.keywords, a.competitors, a.gap,
                          a.competitor_keywords, a.max_units))

if __name__ == "__main__":
    sys.exit(main())
