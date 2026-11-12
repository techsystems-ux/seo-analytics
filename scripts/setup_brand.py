#!/usr/bin/env python3
"""Create or update a brand from a seed file: the client row, a draft brand rules card and draft prompts.
Prompts are added unapproved: a person approves them in Settings before anything runs. Existing data is never removed.
Usage: python scripts/setup_brand.py seeds/gargi.json"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "collectors"), os.path.join(HERE, "..", "seo")]
from common import connect
import run_checks

def setup(conn, seed):
    row = conn.execute("""SELECT client_id FROM clients WHERE lower(replace(replace(replace(domain,'https://',''),'http://',''),'www.',''))
                          LIKE %s || '%%' OR lower(name)=lower(%s) ORDER BY created_at LIMIT 1""", [seed["domain"].lower(), seed["name"]]).fetchone()
    if row:
        cid = row[0]
        conn.execute("UPDATE clients SET aliases=(SELECT array(SELECT DISTINCT unnest(aliases || %s::text[]))) WHERE client_id=%s", [seed.get("aliases", []), cid])
    else:
        cid = conn.execute("INSERT INTO clients (name, domain, aliases, regions) VALUES (%s,%s,%s,%s) RETURNING client_id",
                           [seed["name"], seed["domain"], seed.get("aliases", []), seed.get("regions", ["IN"])]).fetchone()[0]
    if not run_checks.load_brand(conn, cid):
        run_checks.save_brand(conn, cid, seed.get("brand_rules", {}))
    sm = seed.get("semrush")
    if sm and not conn.execute("SELECT 1 FROM semrush_keywords WHERE client_id=%s AND pulled_on=%s", [cid, sm["pulled_on"]]).fetchone():
        import semrush
        semrush.store_keywords(conn, cid, sm["domain"], sm["database"], sm["pulled_on"], sm["keywords"])
        for c in sm["competitors"]:
            conn.execute("""INSERT INTO competitors (client_id, name, domain, source, tracked, relevance, common_keywords, organic_keywords, organic_traffic, pulled_on)
                            VALUES (%s,%s,%s,'semrush',false,%s,%s,%s,%s,%s) ON CONFLICT (client_id, name) DO NOTHING""",
                         [cid, c["domain"], c["domain"], c["relevance"], c["common_keywords"], c["organic_keywords"], c["organic_traffic"], sm["pulled_on"]])
        for u in sm["usage"]:                     # the credits these rows cost count against the lifetime Semrush cap
            conn.execute("INSERT INTO semrush_usage (client_id, report, lines, units, called_at) VALUES (%s,%s,%s,%s,%s)",
                         [cid, u["report"] + " (connector)", u["lines"], u["units"], sm["pulled_on"]])
    n = 0
    for stage, texts in seed.get("prompts", {}).items():
        for t in texts:
            n += conn.execute("INSERT INTO prompts (client_id, text, funnel_stage, approved) VALUES (%s,%s,%s,false) ON CONFLICT DO NOTHING",
                              [cid, t, stage]).rowcount
    return cid, n

if __name__ == "__main__":
    with connect() as conn:
        cid, n = setup(conn, json.load(open(sys.argv[1])))
        print(f"Client {cid}: {n} new draft prompts. Approve them in Settings, Prompts.")
