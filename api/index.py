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

NEEDED = ["DATABASE_URL", "FLASK_SECRET", "TOKEN_KEY", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_REDIRECT_URI"]

def _setup_pending(missing):
    """Until every setting is in place, show which are missing (names only) instead of a server error."""
    from flask import Flask
    pending = Flask(__name__)
    @pending.route("/", defaults={"path": ""})
    @pending.route("/<path:path>")
    def page(path):
        items = "".join(f"<li><code>{k}</code></li>" for k in missing)
        return (f"<!doctype html><meta name=viewport content='width=device-width'><title>Setup | Hovers AI Search</title>"
                f"<body style='font:15px/1.6 Arial;max-width:640px;margin:60px auto;padding:0 16px;color:#0F0F0F'>"
                f"<p style='letter-spacing:.18em;font-size:11px;color:#6E6E6E'>HOVERS AI SEARCH · SETUP</p><h1>Almost there</h1>"
                f"<p>Add these in Vercel, Project Settings, Environment Variables, then redeploy:</p><ul>{items}</ul></body>", 503)
    return pending

def _load():
    missing = [k for k in NEEDED if not os.environ.get(k)]
    if missing: return _setup_pending(missing)
    _migrate()
    from app import app as flask_app
    return flask_app

app = _load()
