#!/usr/bin/env python3
"""Google sign-in without a web server: for machines where the browser cannot reach the app.

  python connect_cli.py start                 -> prints the Google sign-in link
  python connect_cli.py finish "<address>"    -> paste the localhost address Google sent you to (the page will not load; that is expected)
  python connect_cli.py plan                  -> list discovered sites with proposed client names and GA4 matches
  python connect_cli.py import all | 1,3,5    -> import those rows as clients
  python connect_cli.py rename 3 "New name"   -> change a proposed client name before importing

Uses the same database tables and rules as the web connect page (webapp/app.py).
"""
import os, sys, json, secrets
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collectors"))
from common import connect
import google_auth as ga

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".connect_state.json")
ALLOWED = [d.strip().lower() for d in os.environ.get("ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()]

def load():
    try: return json.load(open(STATE_FILE))
    except (OSError, ValueError): return {}
def save(s): json.dump(s, open(STATE_FILE, "w"), indent=1)

def start():
    s = load(); s["state"] = secrets.token_urlsafe(24); save(s)
    print(ga.auth_url(s["state"]))

def save_discovery(conn, cid, sites, props):
    with conn.transaction():
        conn.execute("DELETE FROM google_gsc_sites WHERE connection_id=%s", [cid])
        conn.execute("DELETE FROM google_ga4_properties WHERE connection_id=%s", [cid])
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO google_gsc_sites (connection_id, site_url, permission_level) VALUES (%s,%s,%s)", [(cid, a, b) for a, b in sites])
            cur.executemany("INSERT INTO google_ga4_properties (connection_id, property_id, display_name, account_name, web_uris) VALUES (%s,%s,%s,%s,%s)",
                            [(cid, p["property_id"], p["display_name"], p["account_name"], p["web_uris"]) for p in props])
        conn.execute("UPDATE google_connections SET last_discovered_at=now(), status='active', last_error=NULL WHERE connection_id=%s", [cid])

def finish(address):
    s = load(); q = parse_qs(urlparse(address.strip()).query)
    if q.get("error"): sys.exit("Google sign-in was cancelled: " + q["error"][0])
    if not q.get("code"): sys.exit("That address has no sign-in code. Copy the full address from the browser bar after signing in.")
    if q.get("state", [""])[0] != s.get("state"): sys.exit("This address is from an older sign-in. Run 'start' again and use the new link.")
    tok = ga.exchange_code(q["code"][0])
    email = ga.userinfo(tok["access_token"]).get("email", "").lower()
    if ALLOWED and email.split("@")[-1] not in ALLOWED: sys.exit(f"{email} is not allowed to connect.")
    with connect() as conn:
        cid = conn.execute("""INSERT INTO google_connections (google_email, refresh_token_enc, scopes) VALUES (%s,%s,%s)
            ON CONFLICT (google_email) DO UPDATE SET refresh_token_enc=EXCLUDED.refresh_token_enc, scopes=EXCLUDED.scopes, status='active', last_error=NULL
            RETURNING connection_id""", [email, ga.encrypt(tok["refresh_token"]), tok.get("scope", "")]).fetchone()[0]
        sites, props = ga.list_gsc_sites(tok["access_token"]), ga.list_ga4_properties(tok["access_token"])
        save_discovery(conn, cid, sites, props)
    s.pop("state", None); s["connection_id"] = str(cid); s["names"] = {}; save(s)
    print(f"Connected {email}: {len(sites)} Search Console sites, {len(props)} GA4 properties.")
    plan()

def build_plan(conn, cid):
    sites = conn.execute("SELECT site_url, permission_level FROM google_gsc_sites WHERE connection_id=%s", [cid]).fetchall()
    props = [{"property_id": p, "display_name": n, "account_name": a, "web_uris": u} for p, n, a, u in
             conn.execute("SELECT property_id, display_name, account_name, web_uris FROM google_ga4_properties WHERE connection_id=%s", [cid]).fetchall()]
    rows = conn.execute("SELECT c.client_id, c.name, c.domain, i.gsc_site_url FROM clients c LEFT JOIN client_integrations i USING (client_id)").fetchall()
    existing = {ga.domain_of(d): (c, n, site) for c, n, d, site in rows}
    names = {p["property_id"]: p["display_name"] for p in props}
    return ga.import_plan(sites, props, existing), names

def plan():
    s = load(); cid = s.get("connection_id")
    if not cid: sys.exit("No sign-in yet. Run 'start' first.")
    with connect() as conn: rows, names = build_plan(conn, cid)
    perm = {"siteOwner": "Owner", "siteFullUser": "Full", "siteRestrictedUser": "Restricted"}
    for i, r in enumerate(rows, 1):
        name = s.get("names", {}).get(r["site_url"], r["name"])
        status = "already imported" if r["already_linked"] else ("links to existing client" if r["existing_client_id"] else "new client")
        ga4 = f'{names.get(r["ga4"], "")} ({r["ga4"]})' if r["ga4"] else "no GA4 match"
        print(f'{i:>3}. {r["site_url"]:<45} {perm.get(r["permission"], r["permission"]):<10} -> {name:<28} | GA4: {ga4:<40} | {status}')

def rename(n, name):
    s = load()
    with connect() as conn: rows, _ = build_plan(conn, s["connection_id"])
    s.setdefault("names", {})[rows[int(n) - 1]["site_url"]] = name.strip()[:120]; save(s); plan()

def do_import(sel):
    s = load(); cid = s["connection_id"]; created = linked = 0
    with connect() as conn:
        rows, _ = build_plan(conn, cid)
        pick = range(1, len(rows) + 1) if sel == "all" else [int(x) for x in sel.split(",") if x.strip()]
        with conn.transaction():
            for n in pick:
                r = rows[n - 1]
                if r["already_linked"]: continue
                name = s.get("names", {}).get(r["site_url"], r["name"])
                if r["existing_client_id"]: client_id = r["existing_client_id"]; linked += 1
                else:
                    client_id = conn.execute("INSERT INTO clients (name, domain) VALUES (%s,%s) RETURNING client_id", [name, r["domain"]]).fetchone()[0]; created += 1
                conn.execute("""INSERT INTO client_integrations (client_id, gsc_site_url, ga4_property_id, google_connection_id) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (client_id) DO UPDATE SET gsc_site_url=EXCLUDED.gsc_site_url,
                      ga4_property_id=coalesce(EXCLUDED.ga4_property_id, client_integrations.ga4_property_id), google_connection_id=EXCLUDED.google_connection_id""",
                             [client_id, r["site_url"], r["ga4"], cid])
    print(f"Imported {created} new clients and linked {linked} existing ones.")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    {"start": lambda: start(), "finish": lambda: finish(a[1]), "plan": lambda: plan(),
     "import": lambda: do_import(a[1] if len(a) > 1 else ""), "rename": lambda: rename(a[1], a[2])}.get(a[0], lambda: sys.exit(__doc__))()
