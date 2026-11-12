#!/usr/bin/env python3
"""Run before every deploy (Render preDeployCommand): apply new migrations as the database owner, then load the
brand seed files (new brands and draft prompts only; nothing existing is changed or removed)."""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, ".."), HERE]
import psycopg
from start_local import migrate

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:   # owner login, before switching to the app role
    migrate(c)
import setup_brand
from common import connect
with connect() as conn:
    for f in sorted(glob.glob(os.path.join(HERE, "..", "seeds", "*.json"))):
        cid, n = setup_brand.setup(conn, json.load(open(f)))
        print(f"{os.path.basename(f)}: client {cid}, {n} new draft prompts")
