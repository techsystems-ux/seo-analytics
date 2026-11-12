#!/usr/bin/env python3
"""On-demand Semrush research for the Research screens: keyword research, domain and organic research, backlink
analytics, paid and shopping research. Each lookup is one button press: the screen shows the most it can cost first,
every request goes through semrush.Semrush (run budget, lifetime cap SEMRUSH_TOTAL_CAP, feature cap
SEMRUSH_CAP_RESEARCH_<TOOL>), and the whole result is stored so looking at it again costs nothing.
"""
import datetime as dt, json, os
from semrush import Semrush, BudgetExceeded, UNIT_COST, PER_REQUEST, INTENTS

# One step = (section name, report, lines, extra params, columns). {q} is the keyword or domain looked up.
def steps(tool, n=20):
    half = max(5, n // 2)
    if tool == "keyword":
        return [("overview", "phrase_this", 1, {"phrase": "{q}"}, "Ph,Nq,Cp,Co,Nr,Td,In,Kd,Fk"),
                ("related", "phrase_related", n, {"phrase": "{q}", "display_sort": "nq_desc"}, "Ph,Nq,Kd,In,Cp,Fk,Rr"),
                ("broad", "phrase_fullsearch", n, {"phrase": "{q}", "display_sort": "nq_desc"}, "Ph,Nq,Kd,In,Cp,Fk"),
                ("questions", "phrase_questions", n, {"phrase": "{q}", "display_sort": "nq_desc"}, "Ph,Nq,Kd,In,Fk"),
                ("serp", "phrase_organic", 10, {"phrase": "{q}"}, "Dn,Ur,Fp")]
    if tool == "domain":
        return [("overview", "domain_ranks", 1, {"domain": "{q}"}, "Dn,Rk,Or,Ot,Oc,Ad,At,Ac"),
                ("history", "domain_rank_history", 12, {"domain": "{q}", "display_sort": "dt_desc"}, "Dt,Rk,Or,Ot,Oc,Ad,At"),
                ("keywords", "domain_organic", n, {"domain": "{q}", "display_sort": "tr_desc"}, "Ph,Po,Pp,Nq,Kd,Cp,Ur,Tr,In,Fk,Fp"),
                ("pages", "domain_organic_unique", n, {"domain": "{q}", "display_sort": "tr_desc"}, "Ur,Pc,Tg,Tr"),
                ("rising", "domain_organic", half, {"domain": "{q}", "display_positions": "rise", "display_sort": "nq_desc"}, "Ph,Po,Pp,Nq,Ur"),
                ("falling", "domain_organic", half, {"domain": "{q}", "display_positions": "fall", "display_sort": "nq_desc"}, "Ph,Po,Pp,Nq,Ur"),
                ("competitors", "domain_organic_organic", 10, {"domain": "{q}"}, "Dn,Cr,Np,Or,Ot")]
    if tool == "backlinks":
        t = {"target": "{q}", "target_type": "root_domain"}
        return [("overview", "backlinks_overview", 1, t, "ascore,total,domains_num,urls_num,ips_num,follows_num,nofollows_num,texts_num,images_num"),
                ("anchors", "backlinks_anchors", n, {**t, "display_sort": "domains_num_desc"}, "anchor,domains_num,backlinks_num,first_seen,last_seen"),
                ("refdomains", "backlinks_refdomains", n, {**t, "display_sort": "domain_ascore_desc"}, "domain_ascore,domain,backlinks_num,country,first_seen,last_seen"),
                ("new", "backlinks", half, {**t, "display_filter": "+|newlink|Eq|true", "display_sort": "page_ascore_desc"},
                 "page_ascore,source_title,source_url,target_url,anchor,nofollow,first_seen"),
                ("lost", "backlinks", half, {**t, "display_filter": "+|lostlink|Eq|true", "display_sort": "page_ascore_desc"},
                 "page_ascore,source_title,source_url,target_url,anchor,nofollow,last_seen")]
    if tool == "paid":
        return [("overview", "domain_ranks", 1, {"domain": "{q}"}, "Dn,Rk,Or,Ot,Ad,At,Ac"),
                ("ads", "domain_adwords", n, {"domain": "{q}", "display_sort": "tr_desc"}, "Ph,Po,Nq,Cp,Ur,Tr,Tc,Tt,Ds"),
                ("copies", "domain_adwords_unique", half, {"domain": "{q}"}, "Tt,Ds,Vu,Ur,Pc"),
                ("shopping", "domain_shopping", n, {"domain": "{q}", "display_sort": "nq_desc"}, "Ph,Po,Nq,Sn,Tt,Pr,Ur")]
    raise ValueError(tool)

LABELS = {"keyword": "Keyword research", "domain": "Domain research", "backlinks": "Backlink analytics", "paid": "Paid and shopping"}

def estimate(tool, n=20):
    return sum(UNIT_COST[r] * (1 if r in PER_REQUEST else lines) for _, r, lines, _, _ in steps(tool, n))

# Semrush returns long column names for the analytics reports and snake_case for backlinks; both become one set of keys.
NAMES = {"Keyword": "keyword", "Ph": "keyword", "Search Volume": "volume", "Nq": "volume", "CPC": "cpc", "Cp": "cpc",
         "Competition": "competition", "Co": "competition", "Number of Results": "results", "Nr": "results", "Trends": "trend", "Td": "trend",
         "Intent": "intent", "Intents": "intent", "In": "intent", "Keyword Difficulty": "kd", "Keyword Difficulty Index": "kd", "Kd": "kd",
         "SERP Features": "fk", "Keywords SERP Features": "fk", "Fk": "fk", "SERP Features by Position": "fp", "Fp": "fp",
         "Position": "position", "Po": "position", "Previous Position": "prev", "Pp": "prev", "Url": "url", "Ur": "url",
         "Traffic (%)": "traffic_pct", "Tr": "traffic_pct", "Domain": "domain", "Dn": "domain", "Rank": "rank", "Rk": "rank",
         "Organic Keywords": "org_keywords", "Or": "org_keywords", "Organic Traffic": "org_traffic", "Ot": "org_traffic",
         "Organic Cost": "org_cost", "Oc": "org_cost", "Adwords Keywords": "ad_keywords", "Ad": "ad_keywords", "Adwords Traffic": "ad_traffic",
         "At": "ad_traffic", "Adwords Cost": "ad_cost", "Ac": "ad_cost", "Date": "date", "Dt": "date", "Number of Keywords": "keywords",
         "Pc": "keywords", "Traffic": "traffic", "Tg": "traffic", "Competitor Relevance": "relevance", "Cr": "relevance",
         "Common Keywords": "common", "Np": "common", "Title": "title", "Tt": "title", "Description": "description", "Ds": "description",
         "Visible Url": "visible_url", "Vu": "visible_url", "Traffic Cost (%)": "traffic_cost_pct", "Tc": "traffic_cost_pct",
         "Shop Name": "shop", "Sn": "shop", "Product Price": "price", "Pr": "price", "Related Relevance": "relevance", "Rr": "relevance"}
TEXT = {"keyword", "url", "domain", "title", "description", "visible_url", "shop", "fk", "fp", "intent", "trend", "date", "anchor",
        "source_title", "source_url", "target_url", "country"}

def clean(row, report):
    out = {}
    for k, v in row.items():
        key = NAMES.get(k, k)
        if report == "domain_organic" and k == "SERP Features": key = "fp"      # here Semrush names Fp "SERP Features"
        if key == "intent" and v:
            v = ",".join(INTENTS.get(i.strip(), i.strip()) for i in str(v).split(",") if i.strip())
        elif key == "trend" and v:
            v = [float(x) for x in str(v).split(",") if x.strip()]
        elif key in ("first_seen", "last_seen") and str(v).isdigit():
            v = dt.datetime.utcfromtimestamp(int(v)).date().isoformat()
        elif key == "date" and str(v).isdigit() and len(str(v)) == 8:
            v = f"{str(v)[:4]}-{str(v)[4:6]}-{str(v)[6:]}"
        elif key not in TEXT:
            try: v = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError): pass
        out[key] = v
    return out

def run(conn, client_id, key, tool, query, database="in", n=20, max_units=None, session=None):
    q = query.strip().lower()
    if tool != "keyword": q = q.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
    cap = max_units if max_units is not None else estimate(tool, n)
    sm = Semrush(key, conn, client_id, cap, session, feature=f"research_{tool}")
    result, stopped = {}, None
    for section, report, lines, params, cols in steps(tool, n):
        p = {k: (v.replace("{q}", q) if isinstance(v, str) else v) for k, v in params.items()}
        if not report.startswith("backlinks"): p["database"] = database
        try:
            rows = [clean(r, report) for r in sm.call(report, lines, export_columns=cols, **p)]
        except BudgetExceeded as e:
            stopped = str(e); break
        result[section] = rows[0] if section == "overview" and rows else ({} if section == "overview" else rows)
    if stopped: result["stopped"] = stopped
    lid = conn.execute("""INSERT INTO research_lookups (client_id, tool, query, database, units, result) VALUES (%s,%s,%s,%s,%s,%s)
                          RETURNING lookup_id""", [client_id, tool, q, database, sm.spent, json.dumps(result)]).fetchone()[0]
    return lid, sm.spent

if __name__ == "__main__":
    import argparse
    from common import connect
    ap = argparse.ArgumentParser()
    ap.add_argument("tool", choices=list(LABELS)); ap.add_argument("query"); ap.add_argument("--client-id", required=True)
    ap.add_argument("--database", default="in"); ap.add_argument("--lines", type=int, default=20); ap.add_argument("--max-units", type=int, required=True)
    a = ap.parse_args()
    with connect() as conn:
        lid, spent = run(conn, a.client_id, os.environ["SEMRUSH_API_KEY"], a.tool, a.query, a.database, a.lines, a.max_units)
        print(f"{LABELS[a.tool]} for {a.query}: {spent} units (lookup {lid})")
