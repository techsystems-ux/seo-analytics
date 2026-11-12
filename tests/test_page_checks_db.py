"""Database round trip for module 1: migrations, brand rules, page import, live check with a fake fetch,
draft check, site-wide pass, and tenant isolation. Needs ADMIN_DATABASE_URL (a superuser on a throwaway database)."""
import os, sys, io, csv
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "seo"), os.path.join(ROOT, "collectors")]
import psycopg
from start_local import migrate

admin = os.environ["ADMIN_DATABASE_URL"]
with psycopg.connect(admin, autocommit=True) as c:
    migrate(c); migrate(c)                                         # second run must be a no-op
    c.execute("ALTER ROLE aeo_app LOGIN PASSWORD 'change-me'")        # as start_local.py does for a local trial
    assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 9
print("migrations apply once")

os.environ["DATABASE_URL"] = admin.replace("postgres@", "aeo_app:change-me@", 1)
from common import connect
import run_checks as rc

class Resp:
    def __init__(s, code, text): s.status_code, s.text = code, text
class Fake:
    def __init__(s, pages): s.pages, s.calls = pages, []
    def get(s, url, headers=None, timeout=None, allow_redirects=True):
        s.calls.append((url, headers["User-Agent"])); return s.pages[url]

A, B = "11111111-0000-0000-0000-00000000000a", "11111111-0000-0000-0000-00000000000b"
PAGE = lambda title, h1: f"<html><head><title>{title}</title><meta name='description' content='Shop a memory foam mattress in the UAE.'></head><body><h1>{h1}</h1><p>Text — with a dash.</p></body></html>"
with connect() as conn:
    for cid, name, dom in ((A, "Easeliving", "easelivinguae.com"), (B, "Total Comfort", "totalcomfortuae.com")):
        conn.execute("DELETE FROM clients WHERE client_id=%s", [cid])
        conn.execute("INSERT INTO clients (client_id, name, domain) VALUES (%s,%s,%s)", [cid, name, dom])
    rc.save_brand(conn, A, {"brand_spellings": ["Easeliving"], "usp_markers": ["trial"], "region_words": ["UAE"],
                            "term_fixes": {"natural latex": "natural organic latex"}, "wrong_domains": ["easeliving.in"]})
    rc.save_brand(conn, A, {"usps": ["15-night trial"]})               # partial update keeps the other fields
    assert rc.load_brand(conn, A)["region_words"] == ["UAE"] and rc.load_brand(conn, A)["usps"] == ["15-night trial"]
    sheet = "url,page_type,primary_keyword,secondary_keywords,search_volume,intent\n" \
            "https://easelivinguae.com/products/memory-foam-mattress,product,memory foam mattress,soft mattress; foam bed,880.0,Commercial\n" \
            "https://easelivinguae.com/collections/memory-foam,collection,memory foam mattress,,20,\n" \
            "https://easelivinguae.com/pages/gone,static,,,,\n"
    assert rc.import_pages(conn, A, csv.DictReader(io.StringIO(sheet))) == 3
    u1, u2, u3 = [r[0] for r in csv.reader(io.StringIO(sheet))][1:]
    fake = Fake({u1: Resp(200, PAGE("Memory Foam Mattress | Easeliving", "Memory Foam Mattress | Easeliving")),
                 u2: Resp(200, PAGE("Memory Foam Mattress | Easeliving", "Memory Foam Mattresses in UAE")),
                 u3: Resp(404, "")})
    summary = rc.check_live(conn, A, session=fake, pause=0)
    assert [s[2] for s in summary if s[0] == u3] == ["HTTP 404"] and all(h == rc.UA for _, h in fake.calls)
    pid = conn.execute("SELECT page_id FROM pages WHERE url=%s", [u1]).fetchone()[0]
    latest = dict(conn.execute("SELECT check_id, result FROM page_latest_checks WHERE page_id=%s", [pid]).fetchall())
    assert latest["META-04"] == "fail" and latest["META-08"] == "fail" and latest["KW-03"] == "fail" and latest["META-03"] == "fail", latest
    print("live check stored:", sum(v == "fail" for v in latest.values()), "to fix on", u1.rsplit('/', 1)[-1])

    res = {r["check_id"]: r for r in rc.check_draft(conn, A, pid, title="Memory Foam Mattress | Pressure Relief | Easeliving",
                                                   description="Shop a memory foam mattress in the UAE with a 15-night trial, custom sizes and free delivery. Pressure relief and support for every sleeper.",
                                                   h1="Memory Foam Mattress for Pressure Relief", body="Made with natural latex. " * 5)}
    assert res["META-04"]["result"] == "pass" and res["BR-02"]["result"] == "fail"
    latest = dict(conn.execute("SELECT check_id, result FROM page_latest_checks WHERE page_id=%s", [pid]).fetchall())
    assert latest["META-04"] == "pass" and "META-08" not in latest, latest   # the view shows the newest snapshot only
    rc.site_pass(conn, A)
    latest = dict(conn.execute("SELECT check_id, result FROM page_latest_checks WHERE page_id=%s", [pid]).fetchall())
    assert latest["META-08"] == "pass" and latest["KW-03"] == "fail", latest  # titles now differ; the keyword is still shared
    print("draft check replaces the live result; site pass re-runs on the newest snapshots")

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:      # a client login sees only its own rows
    c.execute("SELECT set_config('app.client_id', %s, false)", [B])
    assert c.execute("SELECT count(*) FROM pages").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM page_latest_checks").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM brand_rules").fetchone()[0] == 0
    c.execute("SELECT set_config('app.client_id', %s, false)", [A])
    assert c.execute("SELECT count(*) FROM pages").fetchone()[0] == 3
print("tenant isolation holds for pages, checks and brand rules")
