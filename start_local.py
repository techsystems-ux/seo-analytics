"""Run everything on one computer with no installs beyond Python packages.
Starts a private Postgres (pgserver) in ~/.hovers-connect/pgdata, applies the migrations once, then opens the Google connect page.

  python start_local.py            -> connect page at http://localhost:8080 (opens the browser)
  python start_local.py collect    -> pull Search Console, GA4 and ChatGPT for every brand, and check pages
Optional keys in .env (they stay on this computer): OPENAI_API_KEY, SEMRUSH_API_KEY
"""
import os, sys, glob, threading, webbrowser, subprocess
from urllib.parse import urlparse, parse_qs, quote
import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS = ["db/schema.sql", "db/002_pagespeed.sql", "db/003_gsc_ga4.sql", "db/004_google_oauth.sql",
              "db/005_brand_rules_pages.sql", "db/006_analytics.sql", "db/007_semrush_suite.sql", "db/008_own_brands.sql"]

def migrate(conn):
    """Apply each migration once, in order. Databases set up before tracking existed already have 001-004."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    if not done and conn.execute("SELECT to_regclass('public.google_connections')").fetchone()[0]:
        for m in MIGRATIONS[:4]: conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", [m]); done.add(m)
    for m in MIGRATIONS:
        if m not in done:
            print("Applying", m)
            with conn.transaction():
                conn.execute(open(os.path.join(HERE, m)).read())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", [m])
    # Let the connecting owner switch to the app role (SET ROLE aeo_app). Harmless if it already can.
    if conn.execute("SELECT NOT rolsuper AND NOT pg_has_role(current_user, 'aeo_app', 'USAGE') FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
        try:
            conn.execute("GRANT aeo_app TO CURRENT_USER")
        except psycopg.errors.InsufficientPrivilege:
            sys.exit("The database user cannot switch to aeo_app. Ask a database admin to run: GRANT aeo_app TO <this user>;")

def load_env():
    for line in open(os.path.join(HERE, ".env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

def start_db():
    import pgserver   # only needed when running the private local database
    # Postgres cannot take a data path containing spaces (e.g. "Hovers AI"), so keep the data in ~/.hovers-connect
    data = os.environ.get("HOVERS_PGDATA") or os.path.join(os.path.expanduser("~"), ".hovers-connect", "pgdata")
    if " " in data: sys.exit(f"Database folder must not contain spaces: {data}")
    os.makedirs(os.path.dirname(data), exist_ok=True)
    srv = pgserver.get_server(data, cleanup_mode="stop")
    admin = srv.get_uri()
    with psycopg.connect(admin, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname='aeo'").fetchone():
            c.execute("CREATE DATABASE aeo")
    u = urlparse(admin); q = parse_qs(u.query)
    host = q.get("host", [""])[0]
    db_admin = f"postgresql://{u.username or 'postgres'}@/aeo?host={quote(host)}" if host else admin.rsplit("/", 1)[0] + "/aeo"
    with psycopg.connect(db_admin, autocommit=True) as c:
        migrate(c)
        c.execute("ALTER ROLE aeo_app LOGIN PASSWORD 'change-me'")   # local trial only: the private database never leaves this computer
    os.environ["DATABASE_URL"] = f"postgresql://aeo_app:change-me@/aeo?host={quote(host)}" if host else db_admin.replace("//postgres", "//aeo_app:change-me", 1)
    return srv

def load_seeds():
    """Brands in seeds/*.json (e.g. Gargi): added once, with draft prompts. Existing data is never changed."""
    import json
    sys.path[:0] = [os.path.join(HERE, "collectors"), os.path.join(HERE, "seo"), os.path.join(HERE, "scripts")]
    import setup_brand
    from common import connect
    with connect() as conn:
        for f in sorted(glob.glob(os.path.join(HERE, "seeds", "*.json"))):
            cid, n = setup_brand.setup(conn, json.load(open(f)))
            if n: print(f"Set up {os.path.basename(f)}: {n} draft prompts to approve in Settings")

def main():
    load_env()
    srv = start_db()
    load_seeds()
    if len(sys.argv) > 1 and sys.argv[1] == "collect":
        # Search Console, GA4, ChatGPT prompts (if OPENAI_API_KEY is in .env) and page checks. Never Semrush: that is a button in Settings.
        subprocess.run([sys.executable, "daily.py", "--only", "gsc,ga4,rank,ai,pages,crawl"], cwd=os.path.join(HERE, "collectors"), env=dict(os.environ))
        return
    sys.path.insert(0, os.path.join(HERE, "webapp"))
    from app import app
    threading.Timer(2, lambda: webbrowser.open("http://localhost:8080")).start()
    print("\nHovers AI Search is running at http://localhost:8080  (leave this window open; close it to stop)\n")
    app.run(host="127.0.0.1", port=8080, threaded=True)

if __name__ == "__main__":
    main()
