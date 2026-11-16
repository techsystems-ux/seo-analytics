"""Vercel entry point: the same Flask app as on the Mac, served as one Python function (see vercel.json).
On a cold start it applies any new database migrations and seed brands first (a quick check when there are none)."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "webapp"), os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "scripts")]

def _migrate():
    if not os.environ.get("DATABASE_URL"): return
    import json, glob, psycopg, start_local
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        have = c.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]
        done = c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] if have else 0
        if done >= len(start_local.MIGRATIONS): return
        start_local.migrate(c)
    import setup_brand
    from common import connect
    with connect() as conn:
        for f in sorted(glob.glob(os.path.join(ROOT, "seeds", "*.json"))):
            setup_brand.setup(conn, json.load(open(f)))

_migrate()
from app import app  # noqa: E402
