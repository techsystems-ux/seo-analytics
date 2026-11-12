import json, os, sys, psycopg
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collectors"))
import pagespeed as p

sample = json.load(open(os.path.join(os.path.dirname(__file__), "psi_sample.json")))
row = p.parse(sample)
assert row["perf_score"] == 62 and row["lcp_ms"] == 3120.5 and row["field_cls"] == 0.12 and row["field_inp_ms"] == 230, row
empty = p.parse({})                      # a page with no CrUX data / failed audit must not crash
assert all(v is None for v in empty.values()), empty
print("parse OK")

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("SET app.is_staff = 'true'")
    cid = c.execute("SELECT client_id FROM clients ORDER BY name LIMIT 1").fetchone()[0]
    c.execute("DELETE FROM pagespeed_results WHERE client_id=%s", [cid])          # history table: start each test run clean
    c.execute("INSERT INTO client_urls (client_id,url,page_type) VALUES (%s,'https://a.example/','home') ON CONFLICT DO NOTHING", [cid])
    p.save(c, cid, "https://a.example/", "mobile", row)
    p.save(c, cid, "https://a.example/", "desktop", error="HTTP 429")
    n = c.execute("SELECT count(*) FROM pagespeed_results WHERE client_id=%s", [cid]).fetchone()[0]
    assert n == 2, n
    c.execute("RESET app.is_staff")
    c.execute("SET app.client_id = '22222222-2222-2222-2222-222222222222'")   # a different tenant
    assert c.execute("SELECT count(*) FROM pagespeed_results").fetchone()[0] == 0
print("save + tenant isolation OK")
