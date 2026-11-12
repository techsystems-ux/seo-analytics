#!/usr/bin/env python3
"""Semrush collector: the client's organic keywords, its organic competitors, and untapped keywords (competitors rank, client does not).

Every call is logged with the API units it cost (semrush_usage). A run stops before it would pass --max-units,
and the lifetime total can never pass SEMRUSH_TOTAL_CAP. Unit prices (per line returned, current data):
domain_organic 10, domain_organic_organic 40, domain_domains 80.

Hard limit: SEMRUSH_TOTAL_CAP (default 500) units across all calls ever made, including any loaded from seed files.
Usage: python semrush.py --client-id UUID [--database in] [--keywords 20] [--competitors 5] [--gap 0] [--max-units 500]
Env: SEMRUSH_API_KEY
"""
import argparse, csv, datetime as dt, io, json, os, re, sys
from urllib.parse import urlencode
import requests
from common import connect

API = "https://api.semrush.com/"
# Prices from Semrush's API docs. Per line unless listed in PER_REQUEST.
UNIT_COST = {"domain_organic": 10, "domain_organic_organic": 40, "domain_domains": 80,
             "phrase_questions": 40, "phrase_related": 40,
             "backlinks_overview": 40, "backlinks_refdomains": 40, "backlinks_matrix": 40}
PER_REQUEST = {"backlinks_overview"}
BACKLINKS_API = "https://api.semrush.com/analytics/v1/"
ORGANIC_COLS = "Ph,Po,Pp,Nq,Cp,Ur,Tr,Co,Kd,In,Fk,Fp"
COMPETITOR_COLS = "Dn,Cr,Np,Or,Ot,Oc,Ad"
INTENTS = {"0": "commercial", "1": "informational", "2": "navigational", "3": "transactional"}

class BudgetExceeded(Exception): pass

TOTAL_CAP = int(os.environ.get("SEMRUSH_TOTAL_CAP", "500"))   # lifetime limit for the whole product, every brand, every run

def lifetime_units(conn):
    return conn.execute("SELECT coalesce(sum(units),0) FROM semrush_usage").fetchone()[0]

def feature_units(conn, feature):
    return conn.execute("SELECT coalesce(sum(units),0) FROM semrush_usage WHERE feature=%s", [feature]).fetchone()[0]

class Semrush:
    """Every Semrush call goes through here. A call is refused before it is made if its worst-case cost would pass
    this run's budget, the lifetime cap (SEMRUSH_TOTAL_CAP) or the feature's own cap (SEMRUSH_CAP_<FEATURE>)."""
    def __init__(self, key, conn, client_id, max_units, session=None, feature="keywords"):
        self.key, self.conn, self.client_id, self.feature = key, conn, client_id, feature
        left = TOTAL_CAP - lifetime_units(conn)
        cap = os.environ.get(f"SEMRUSH_CAP_{feature.upper()}")
        if cap is not None: left = min(left, int(cap) - feature_units(conn, feature))
        self.max_units = max(0, min(max_units, left))
        self.s, self.spent = session or requests.Session(), 0

    def _guard(self, label, cost):
        if self.spent + cost > self.max_units:
            raise BudgetExceeded(f"{label} could cost {cost} units; {self.max_units - self.spent} left (lifetime cap {TOTAL_CAP})")

    def _log(self, report, lines, units):
        self.spent += units
        self.conn.execute("INSERT INTO semrush_usage (client_id, report, lines, units, feature) VALUES (%s,%s,%s,%s,%s)",
                          [self.client_id, report, lines, units, self.feature])

    def call(self, report, limit, **params):
        per_request = report in PER_REQUEST
        cost = UNIT_COST[report] * (1 if per_request else limit)           # worst case: every requested line comes back
        self._guard(report, cost)
        base = BACKLINKS_API if report.startswith("backlinks") else API
        q = {"type": report, "key": self.key, **({} if per_request else {"display_limit": limit}), **params}
        r = self.s.get(base + "?" + urlencode(q, doseq=True), timeout=120)
        text = r.text.strip()
        if r.status_code != 200 or text.startswith("ERROR"):
            if "NOTHING FOUND" in text: rows = []
            else: raise RuntimeError(f"Semrush {report}: {text[:200]}")    # the key is never echoed back in errors
        else:
            rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
        self._log(report, len(rows), UNIT_COST[report] * (1 if per_request else len(rows)))
        return rows

    def project_call(self, label, path, cost, method="GET", params=None, body=None, per_item=None):
        """Projects API (Site Audit, Position Tracking): JSON. `cost` is the worst case checked before the call.
        With per_item=(units, count_fn) the units logged are units x items returned, otherwise `cost`."""
        self._guard(label, cost)
        r = self.s.request(method, "https://api.semrush.com/" + path.lstrip("/") + "?" + urlencode({"key": self.key, **(params or {})}),
                           json=body, timeout=180)
        if r.status_code >= 400: raise RuntimeError(f"Semrush {label}: HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        n = per_item[1](data) if per_item else 1
        self._log(label, n, min(cost, per_item[0] * n) if per_item else cost)
        return data

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
                    "intent": ",".join(INTENTS.get(i.strip(), i.strip()) for i in intents if i.strip()) or None,
                    "serp_features": col(r, "Keywords SERP Features", "SERP Features by Keyword", "Fk") or None,
                    "serp_features_owned": col(r, "SERP Features", "SERP Features by Position", "Fp") or None})
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
                             cpc, url, traffic_pct, competition, kd, intent, serp_features, serp_features_owned)
                           VALUES (%(c)s,%(d)s,%(db)s,%(day)s,%(keyword)s,%(position)s,%(prev_position)s,%(search_volume)s,%(cpc)s,%(url)s,
                             %(traffic_pct)s,%(competition)s,%(kd)s,%(intent)s,%(serp_features)s,%(serp_features_owned)s)
                           ON CONFLICT (client_id, domain, database, pulled_on, keyword) DO UPDATE SET position=EXCLUDED.position,
                             search_volume=EXCLUDED.search_volume, url=EXCLUDED.url, traffic_pct=EXCLUDED.traffic_pct,
                             serp_features=EXCLUDED.serp_features, serp_features_owned=EXCLUDED.serp_features_owned""",
                        [{"serp_features": None, "serp_features_owned": None, **k, "c": client_id, "d": domain, "db": database, "day": day} for k in kws])

STOP = {"by", "and", "the", "of", "for", "with", "shop", "store", "online", "india"}

def brand_words(conn, client_id):
    """Words that make a search branded (name, aliases, brand spellings): 'Gargi by PNG' -> gargi, png."""
    name, aliases = conn.execute("SELECT name, aliases FROM clients WHERE client_id=%s", [client_id]).fetchone()
    br = conn.execute("SELECT brand_spellings FROM brand_rules WHERE client_id=%s", [client_id]).fetchone()
    words = re.findall(r"[a-z0-9]+", " ".join([name, *(aliases or []), *((br or [[]])[0] or [])]).lower())
    return sorted({w for w in words if len(w) >= 3 and w not in STOP})[:20]

def nonbranded_filter(words):
    """Semrush display_filter excluding every keyword that contains a brand word: -|Ph|Co|gargi|-|Ph|Co|png"""
    return "|".join(f"-|Ph|Co|{w}" for w in words)

def sync_client(conn, client_id, key, database="in", n_keywords=20, n_competitors=5, n_gap=0, comp_keywords=0,
                max_units=500, session=None, today=None, nonbranded=True, competitor_refresh_days=30):
    day = today or dt.date.today()
    domain = conn.execute("SELECT domain FROM clients WHERE client_id=%s", [client_id]).fetchone()[0]
    domain = domain.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    sm = Semrush(key, conn, client_id, max_units, session)
    done = []
    try:
        extra = {}
        words = brand_words(conn, client_id) if nonbranded else []
        if words: extra["display_filter"] = nonbranded_filter(words)
        kws = parse_organic(sm.call("domain_organic", n_keywords, domain=domain, database=database,
                                    export_columns=ORGANIC_COLS, display_sort="tr_desc", **extra))
        store_keywords(conn, client_id, domain, database, day, kws)
        done.append(f"{len(kws)} {'non-branded ' if words else ''}keywords")

        recent = conn.execute("""SELECT count(*) FROM competitors WHERE client_id=%s AND source='semrush' AND pulled_on >= %s""",
                              [client_id, day - dt.timedelta(days=competitor_refresh_days)]).fetchone()[0]
        if recent and competitor_refresh_days:
            n_competitors = 0; done.append("competitors already pulled this month")
        comps = parse_competitors(sm.call("domain_organic_organic", n_competitors, domain=domain, database=database,
                                          export_columns=COMPETITOR_COLS)) if n_competitors else []
        for c in comps:
            conn.execute("""INSERT INTO competitors (client_id, name, domain, source, tracked, relevance, common_keywords, organic_keywords, organic_traffic, pulled_on)
                            VALUES (%s,%s,%s,'semrush',false,%s,%s,%s,%s,%s)
                            ON CONFLICT (client_id, name) DO UPDATE SET relevance=EXCLUDED.relevance, common_keywords=EXCLUDED.common_keywords,
                              organic_keywords=EXCLUDED.organic_keywords, organic_traffic=EXCLUDED.organic_traffic, pulled_on=EXCLUDED.pulled_on""",
                         [client_id, c["domain"], c["domain"], c["relevance"], c["common_keywords"], c["organic_keywords"], c["organic_traffic"], day])
        if n_competitors: done.append(f"{len(comps)} competitors")

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
