#!/usr/bin/env python3
"""One-time copy of everything in the Mac's private database to the cloud database (Neon), so the online copy starts
with the same brands, Search Console and GA4 history, prompts, work logs and Semrush credit usage (the lifetime cap
keeps counting from where it is).

Put the Neon connection string in .env as CLOUD_DATABASE_URL=... (never in chat or on GitHub), then double-click
"Copy data to cloud.command". It refuses to run if the cloud copy is already in use (pulls or Google sign-ins), unless --replace is given,
which empties the cloud tables first. A cloud copy that only has the brands a first deploy set up is overwritten. The Mac's data is only read, never changed."""
import os, subprocess, sys, shutil
from urllib.parse import urlparse
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.join(HERE, "..")
sys.path[:0] = [ROOT]
import psycopg, start_local

start_local.load_env()
cloud = os.environ.get("CLOUD_DATABASE_URL", "").strip()
if not cloud.startswith("postgres"): sys.exit("Add CLOUD_DATABASE_URL=<Neon connection string> to the .env file first.")
if "sslmode" not in cloud: cloud += ("&" if "?" in cloud else "?") + "sslmode=require"

import pgserver
bindir = os.path.join(os.path.dirname(pgserver.__file__), "pginstall", "bin")
pg_dump, psql = shutil.which("pg_dump", path=bindir), shutil.which("psql", path=bindir)
srv = start_local.start_db()                                   # the Mac database (starts it if the app is closed)
local = urlparse(srv.get_uri())._replace(path="/aeo").geturl()

with psycopg.connect(cloud, autocommit=True) as c:
    have = c.execute("SELECT to_regclass('public.clients') IS NOT NULL").fetchone()[0]
    if have:
        c.execute("SET app.is_staff = 'true'")
        n = c.execute("SELECT count(*) FROM clients").fetchone()[0]
        used = c.execute("SELECT (SELECT count(*) FROM collector_runs) + (SELECT count(*) FROM google_connections)").fetchone()[0]
        if used and "--replace" not in sys.argv:       # brands seeded by a first deploy alone are fine to overwrite
            sys.exit(f"The cloud copy is already in use ({n} brand(s), with pulls or Google sign-ins). Nothing copied. "
                     "Run with --replace to overwrite it with this Mac's data.")
        if n:
            tabs = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'schema_migrations'")]
            c.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tabs) + " CASCADE"); print("Emptied the cloud tables.")
    print("Setting up the cloud tables..."); start_local.migrate(c)
    # COPY cannot load tables whose row-level security is forced, so the owner lifts it for the copy (inside the same
    # transaction as the data) and puts it back before committing.
    forced = [r[0] for r in c.execute("SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace AND relforcerowsecurity")]

print("Copying data (this can take a few minutes)...")
dump = subprocess.Popen([pg_dump, "--data-only", "--no-owner", "--no-acl",
                         "--exclude-table=schema_migrations", "-d", local], stdout=subprocess.PIPE)
lift = ";".join(f'ALTER TABLE public."{t}" NO FORCE ROW LEVEL SECURITY' for t in forced)
back = ";".join(f'ALTER TABLE public."{t}" FORCE ROW LEVEL SECURITY' for t in forced)
load = subprocess.run([psql, cloud, "-q", "-v", "ON_ERROR_STOP=1", "--single-transaction",
                       "-c", lift, "-f", "-", "-c", back], stdin=dump.stdout, stdout=subprocess.DEVNULL)
dump.stdout.close(); dump.wait()
if dump.returncode or load.returncode: sys.exit("Copy failed; the cloud database was left unchanged. Send the message above to Claude.")

with psycopg.connect(cloud) as c, psycopg.connect(local) as l:
    c.execute("SET app.is_staff = 'true'")
    for t in ("clients", "gsc_search_daily", "ga4_daily", "prompts", "semrush_usage"):
        a = l.execute(f"SELECT count(*) FROM {t}").fetchone()[0]; b = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t}: Mac {a:,} rows, cloud {b:,} rows {'OK' if a == b else 'MISMATCH'}")
    print("Semrush units used so far (counts towards the cap online):", c.execute("SELECT coalesce(sum(units),0) FROM semrush_usage").fetchone()[0])
env = os.path.join(ROOT, ".env")
if "USE_CLOUD_DB=1" not in open(env).read():
    with open(env, "a") as f: f.write("\n# The Mac app now reads and writes the online database (set to 0 to go back to the private one)\nUSE_CLOUD_DB=1\n")
print("Done. The online copy now has the same data as this Mac. From the next start, the Mac app uses the online database")
print("and runs the daily pull, plus any pull asked for on the online copy, while it is open.")
