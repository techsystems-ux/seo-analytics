"""Run everything on one computer with no installs beyond Python packages.
Starts a private Postgres (pgserver) in ~/.hovers-connect/pgdata, applies the migrations once, then opens the Google connect page.

  python start_local.py            -> connect page at http://localhost:8080 (opens the browser)
  python start_local.py collect    -> pull Search Console, GA4 and ChatGPT for every brand, and check pages
Optional keys in .env (they stay on this computer): OPENAI_API_KEY_2, SEMRUSH_API_KEY
"""
import os, sys, glob, threading, webbrowser, subprocess
from urllib.parse import urlparse, parse_qs, quote
import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS = ["db/schema.sql", "db/002_pagespeed.sql", "db/003_gsc_ga4.sql", "db/004_google_oauth.sql",
              "db/005_brand_rules_pages.sql", "db/006_analytics.sql", "db/007_semrush_suite.sql", "db/008_own_brands.sql", "db/009_sample_data.sql", "db/010_research_community.sql", "db/011_reddit_communities.sql", "db/012_monthly_report.sql", "db/013_pull_queue.sql", "db/014_client_access.sql", "db/015_sources_modes.sql", "db/016_self_serve.sql", "db/017_execution.sql", "db/018_lean_audit.sql", "db/019_cloud_worker.sql", "db/020_paid_runs.sql", "db/021_gsc_index.sql", "db/022_chatgpt_plan.sql", "db/023_more_engines.sql", "db/024_api_spend.sql", "db/025_score_versions.sql"]

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
    # Postgres 16+ (Neon, Render) separates "may switch to" (SET) from "inherits" (USAGE), so check and grant SET there.
    pg16 = conn.execute("SELECT current_setting('server_version_num')::int >= 160000").fetchone()[0]
    if conn.execute(f"SELECT NOT rolsuper AND NOT pg_has_role(current_user, 'aeo_app', '{'SET' if pg16 else 'USAGE'}') FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
        try:
            conn.execute("GRANT aeo_app TO CURRENT_USER WITH SET TRUE" if pg16 else "GRANT aeo_app TO CURRENT_USER")
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

def use_cloud(url):
    """After "Copy data to cloud" (USE_CLOUD_DB=1 in .env) this Mac reads and writes the online database, so the Mac app
    and the Vercel copy show the same data. The private local database is left as it was, as a backup."""
    if "sslmode" not in url: url += ("&" if "?" in url else "?") + "sslmode=require"
    with psycopg.connect(url, autocommit=True) as c:
        migrate(c)
    os.environ["DATABASE_URL"] = url
    os.environ["DB_SET_ROLE"] = "aeo_app"
    print("Using the online database (same data as the Vercel copy).")

DAILY = ["--only", "gsc,ga4,rank,ai,pages,blogs,crawl,youtube,reddit"]     # never Semrush: that is a button in Settings

def cloud_worker():
    """Retired: pulls now run on the Orbit server (collectors/worker.py). Kept only for MAC_WORKER=1.
    While the app is open on this Mac: run pulls queued from the online copy, one at a time, and the daily pull of
    every brand once a day. Each pull is collectors/daily.py, with the same credit caps as a pull from the screen."""
    import json, time
    sys.path[:0] = [os.path.join(HERE, "collectors")]
    from common import connect
    with connect() as conn:                         # pulls cut off when this Mac was last closed
        conn.execute("UPDATE pull_queue SET finished_at=now(), exit_code=-1 WHERE started_at IS NOT NULL AND finished_at IS NULL")
    while True:
        try:
            with connect() as conn:
                job = conn.execute("""UPDATE pull_queue SET started_at=now() WHERE pull_id = (SELECT pull_id FROM pull_queue
                                      WHERE started_at IS NULL ORDER BY pull_id LIMIT 1 FOR UPDATE SKIP LOCKED)
                                      RETURNING pull_id, label, args""").fetchone()
                if not job and conn.execute("""SELECT coalesce(max(requested_at) < now() - interval '20 hours', true)
                                               FROM pull_queue WHERE label='nightly' AND client_id IS NULL""").fetchone()[0]:
                    job = conn.execute("""INSERT INTO pull_queue (label, args, requested_by, started_at) VALUES ('nightly', %s, 'this Mac', now())
                                          RETURNING pull_id, label, args""", [json.dumps(DAILY)]).fetchone()
            if not job:
                time.sleep(60); continue
            print(f"Running {job[1]} pull...")
            with open(os.path.join(HERE, "logs", "jobs.log"), "a") as log:
                rc = subprocess.run([sys.executable, "daily.py"] + list(job[2]), cwd=os.path.join(HERE, "collectors"),
                                    env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT).returncode
            with connect() as conn:
                conn.execute("UPDATE pull_queue SET finished_at=now(), exit_code=%s WHERE pull_id=%s", [rc, job[0]])
            print(f"{job[1]} pull finished" + ("" if rc == 0 else f" with errors (see logs/jobs.log)"))
        except Exception as e:
            print("Pull worker:", type(e).__name__, e); time.sleep(120)

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

def load_sample_once():
    """If load-sample.flag sits next to this file, fill every brand's Semrush screens with marked sample rows, then remove the flag."""
    flag = os.path.join(HERE, "load-sample.flag")
    if not os.path.exists(flag): return
    sys.path.insert(0, os.path.join(HERE, "scripts"))
    import sample_semrush
    from common import connect
    with connect() as conn:
        for (cid,) in conn.execute("SELECT client_id FROM clients").fetchall():
            sample_semrush.load(conn, cid)
    print("Sample Semrush data loaded (remove it any time in Settings → Data)")
    try: os.remove(flag)
    except OSError: pass

def ensure_packages():
    """Packages added after the first install (python-pptx for the monthly report): installed once, on this computer."""
    try:
        import pptx  # noqa: F401
    except ImportError:
        print("Installing python-pptx for the monthly report (one time)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "python-pptx==1.0.*"])

def main():
    ensure_packages()
    load_env()
    cloud = os.environ.get("CLOUD_DATABASE_URL", "").strip() if os.environ.get("USE_CLOUD_DB") == "1" else ""
    srv = use_cloud(cloud) if cloud else start_db()
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    load_seeds()
    load_sample_once()
    if len(sys.argv) > 1 and sys.argv[1] == "collect":
        # Search Console, GA4, ChatGPT prompts (if OPENAI_API_KEY_2 is in .env) and page checks. Never Semrush: that is a button in Settings.
        subprocess.run([sys.executable, "daily.py", "--only", "gsc,ga4,rank,ai,pages,crawl,youtube,reddit"], cwd=os.path.join(HERE, "collectors"), env=dict(os.environ))
        return
    # Reddit and YouTube fill themselves: if a key is set and the data is missing or a week old, pull now in the background
    if not cloud and (os.environ.get("YOUTUBE_API_KEY") or os.environ.get("APIFY_TOKEN")):
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        subprocess.Popen([sys.executable, "daily.py", "--only", "youtube,reddit"], cwd=os.path.join(HERE, "collectors"), env=dict(os.environ),
                         stdout=open(os.path.join(HERE, "logs", "jobs.log"), "a"), stderr=subprocess.STDOUT, start_new_session=True)
    if cloud:                                       # the daily pull (and the YouTube and Reddit refresh) runs from here
        if os.environ.get("MAC_WORKER") == "1":           # pulls run on the Orbit server now; the Mac does not
            threading.Thread(target=cloud_worker, daemon=True).start()
    sys.path.insert(0, os.path.join(HERE, "webapp"))
    from app import app
    threading.Timer(2, lambda: webbrowser.open("http://localhost:8080")).start()
    print("\nOrbit is running at http://localhost:8080  (leave this window open; close it to stop)\n")
    app.run(host="127.0.0.1", port=8080, threaded=True)

if __name__ == "__main__":
    main()
