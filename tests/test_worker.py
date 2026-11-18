"""The server worker: pulls split into short tasks, run in order per brand, long work carried on in follow-on tasks,
failures retried, cut-off runs recovered, the nightly pull queued once a day, and the endpoints locked. Google, OpenAI and
the crawl are faked. Needs ADMIN_DATABASE_URL."""
import os, sys, re, json, time, datetime as dt
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "webapp"), os.path.join(ROOT, "scripts")]
import psycopg
admin = os.environ["ADMIN_DATABASE_URL"]
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS aeo_worker"); c.execute("CREATE DATABASE aeo_worker OWNER site_owner")
owner = re.sub(r"postgresql://[^@]*@/[^?]*", "postgresql://site_owner:owner-pw@/aeo_worker", admin)
from start_local import migrate
with psycopg.connect(owner, autocommit=True) as c: migrate(c)
from cryptography.fernet import Fernet
os.environ.update(DATABASE_URL=owner, DB_SET_ROLE="aeo_app", FLASK_SECRET="t", TOKEN_KEY=Fernet.generate_key().decode(),
                  GOOGLE_OAUTH_CLIENT_ID="x", GOOGLE_OAUTH_CLIENT_SECRET="x", OAUTH_REDIRECT_URI="http://localhost/cb",
                  CRON_SECRET="s3cret-for-tests", OPENAI_API_KEY="sk-test")
from common import connect
import worker, gsc, daily, google_auth, ai_visibility, blog_writer, crawler

A, B = "33333333-0000-0000-0000-00000000000a", "33333333-0000-0000-0000-00000000000b"
with connect() as conn:
    gid = conn.execute("INSERT INTO google_connections (google_email, refresh_token_enc, scopes) VALUES ('seo@hovers.in', %s, 'x') RETURNING connection_id",
                       [google_auth.encrypt("r")]).fetchone()[0]
    conn.execute("INSERT INTO clients (client_id, name, domain) VALUES (%s,'Alpha','alpha.shop'), (%s,'Beta','beta.shop')", [A, B])
    conn.execute("INSERT INTO client_integrations (client_id, gsc_site_url, google_connection_id) VALUES (%s,'sc-domain:alpha.shop',%s)", [A, gid])

# fakes: Google, the crawl, OpenAI prompts, the blog writer, and the steps that call daily.py
windows, ai_calls, drafts, daily_calls, kicks = [], [], [], [], []
class FakePool:
    def __init__(s, conn, scope): pass
    def get(s, gconn): return object()
google_auth.SessionPool = FakePool
gsc.sync_window = lambda conn, sess, c, site, n, d, al, s, e, row_limit=25000: windows.append((str(s), str(e))) or 10
def fake_ai(conn, cid, key, samples=1, cycle=None, session=None, sentiment=True, deadline=None):
    ai_calls.append(deadline); return ({"ok": 5, "more": 1} if len(ai_calls) == 1 else {"ok": 3}), {}
ai_visibility.sync_client = fake_ai
blog_writer.research = lambda conn, cid: 4
blog_writer.write = lambda conn, cid, key, n=None, session=None: drafts.append(cid) or ["Draft title"]
crawler.run = lambda conn, cid, limit=500, pause=0.5, deadline=None: {"pages": 12, "health": 90, "error": 1, "warning": 0, "notice": 0}
fail_once = {"rank": 1}
def fake_daily(argv):
    step = argv[argv.index("--only") + 1]; daily_calls.append(step)
    if fail_once.get(step): fail_once[step] -= 1; raise RuntimeError("temporary failure")
    return 0
daily.main = fake_daily
worker.kick = lambda: kicks.append(1) or True

# 1. a button pull for one brand becomes ordered tasks
with connect() as conn:
    pid = worker.enqueue(conn, A, "gsc,rank,ai,blogs,crawl", ["--client-id", A, "--only", "gsc,rank,ai,blogs,crawl", "--force-weekly"], "seo@hovers.in")
    steps = [r[0] for r in conn.execute("SELECT step FROM pull_tasks WHERE pull_id=%s ORDER BY seq", [pid])]
    started = conn.execute("SELECT started_at IS NOT NULL FROM pull_queue WHERE pull_id=%s", [pid]).fetchone()[0]
assert steps == ["gsc", "rank", "ai", "blogs", "crawl"] and started, steps          # marked started: an old Mac app never takes it
res = worker.work(budget=60, kick_after=False)
with connect() as conn:
    rows = conn.execute("SELECT step, status, seq, args, note, attempts FROM pull_tasks WHERE pull_id=%s ORDER BY seq, task_id", [pid]).fetchall()
    fin = conn.execute("SELECT finished_at IS NOT NULL, exit_code FROM pull_queue WHERE pull_id=%s", [pid]).fetchone()
    logged = {r[0] for r in conn.execute("SELECT DISTINCT collector FROM collector_runs WHERE client_id=%s", [A])}
by = [(r[0], r[1]) for r in rows]
assert len(windows) == 9 + 13 and windows[9][1] < windows[0][0], windows                # 90 days, then the 125 days before them, in 10-day windows
assert all((dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days < 10 for s, e in windows)
assert [s for s, st in by].count("gsc") == 10 and by[-1][0] == "gsc_history" and rows[-1][2] > 9000      # history last
assert [s for s, _ in by if s != "gsc" and s != "gsc_history"] == ["rank", "ai", "ai", "blogs", "blogs", "blogs", "crawl"], by
assert all(st == "done" for _, st in by) and fin == (True, 0), (by, fin)
assert next(r for r in rows if r[0] == "rank")[5] == 2                                    # failed once, retried, done
assert len(ai_calls) == 2 and len(drafts) == 3                                           # prompts continued; three drafts, one per task
assert {"gsc", "gsc_history", "ai", "blogs"} <= logged and "crawl" in logged
order = [r[0] for r in rows]
assert order.index("rank") > max(i for i, s in enumerate(order) if s == "gsc")           # rank only after Search Console finished
print(f"worker: one pull, {len(rows)} tasks: Search Console in 10-day windows (history last), prompts and drafts carried on, a failure retried, in order")

# 2. a run cut off by the platform is picked up again; three cut-offs give up
with connect() as conn:
    pid2 = worker.enqueue(conn, B, "rank", ["--client-id", B, "--only", "rank"], "seo@hovers.in")
    conn.execute("UPDATE pull_tasks SET status='running', started_at=now() - interval '10 minutes', attempts=1 WHERE pull_id=%s", [pid2])
worker.work(budget=30, kick_after=False)
with connect() as conn:
    assert conn.execute("SELECT status, attempts FROM pull_tasks WHERE pull_id=%s", [pid2]).fetchone() == ("done", 2)
    pid3 = worker.enqueue(conn, B, "rank", ["--client-id", B, "--only", "rank"], "seo@hovers.in")
    conn.execute("UPDATE pull_tasks SET status='running', started_at=now() - interval '10 minutes', attempts=3 WHERE pull_id=%s", [pid3])
worker.work(budget=30, kick_after=False)
with connect() as conn:
    st, note = conn.execute("SELECT status, note FROM pull_tasks WHERE pull_id=%s", [pid3]).fetchone()
    assert st == "error" and "gave up" in note and conn.execute("SELECT exit_code FROM pull_queue WHERE pull_id=%s", [pid3]).fetchone()[0] == 1
print("recovery: a cut-off task is retried; after three cut-offs it is marked failed and the pull shows an error")

# 3. the budget: a run stops starting tasks when its time is up and hands over to a follow-on run
with connect() as conn: pid4 = worker.enqueue(conn, B, "rank,pages", ["--client-id", B, "--only", "rank,pages"], "x")
slow = daily.main
daily.main = lambda argv: (time.sleep(1.2), slow(argv))[1]
kicks.clear(); r = worker.work(budget=1)
assert r["ran"] == 1 and r["queued"] == 1 and kicks == [1], r                            # one task, then a follow-on run
worker.work(budget=30, kick_after=False); daily.main = slow
print("budget: a run starts tasks only within its budget, then starts the next run itself")

# 4. nightly: all brands, once a day; Semrush never in it
with connect() as conn:
    n1 = worker.nightly(conn); n2 = worker.nightly(conn)
    steps = conn.execute("SELECT client_id::text, array_agg(step ORDER BY seq) FROM pull_tasks WHERE pull_id=%s GROUP BY 1", [n1]).fetchall()
assert n1 and n2 is None and {s[0] for s in steps} == {A, B}
assert steps[0][1] == ["gsc", "ga4", "rank", "semrush", "ai", "pages", "index", "cwv", "reddit", "youtube", "blogs", "crawl"], steps
with connect() as conn: assert worker.nightly(conn, "midday") is None                                     # once a day: no midday run
print("nightly: every brand once a day, Search Console to crawl, Semrush included; no midday run at one paid run a day")

# 4b. a pull queued the old way (for the Mac) is taken over
with connect() as conn:
    old = conn.execute("INSERT INTO pull_queue (client_id, label, args, requested_by) VALUES (%s,'youtube',%s,'x') RETURNING pull_id",
                       [B, json.dumps(["--client-id", B, "--only", "youtube"])]).fetchone()[0]
worker.work(budget=30, kick_after=False)
with connect() as conn:
    assert conn.execute("SELECT started_at IS NOT NULL FROM pull_queue WHERE pull_id=%s", [old]).fetchone()[0]
    assert conn.execute("SELECT status FROM pull_tasks t JOIN pull_queue q USING (pull_id) WHERE q.label='youtube' AND t.client_id=%s ORDER BY task_id DESC LIMIT 1", [B]).fetchone()[0] == "done"
print("legacy: a pull queued for the Mac before this change is run by the server")

# 6. the paid guardrail: whatever asks, OpenAI and Semrush run at most PAID_RUNS_PER_DAY per brand
with connect() as conn:
    conn.execute("DELETE FROM pull_tasks"); conn.execute("DELETE FROM paid_runs")
    p1 = worker.enqueue(conn, B, "ai,blogs", ["--client-id", B, "--only", "ai,blogs"], "seo@hovers.in")
ai_calls.clear(); drafts.clear(); worker.work(budget=30, kick_after=False)
first = (len(ai_calls), len(drafts))
with connect() as conn: p2 = worker.enqueue(conn, B, "ai,blogs", ["--client-id", B, "--only", "ai,blogs"], "owner@beta.shop")
worker.work(budget=30, kick_after=False)
with connect() as conn:
    notes = [r[0] for r in conn.execute("SELECT note FROM pull_tasks WHERE pull_id=%s ORDER BY seq", [p2])]
    ok_now, nxt = worker.paid_next(conn, B, "ai")
assert first[0] >= 1 and first[1] >= 1 and (len(ai_calls), len(drafts)) == first, (first, len(ai_calls), len(drafts))  # nothing more spent
assert all(n.startswith("skipped: already ran 1 time in 24 hours") for n in notes) and not ok_now and nxt, notes
os.environ.update(PAID_RUNS_PER_DAY="2")
with connect() as conn:
    p3 = worker.enqueue(conn, B, "ai", ["--client-id", B, "--only", "ai"], "x")
worker.work(budget=30, kick_after=False)
with connect() as conn:
    note = conn.execute("SELECT note FROM pull_tasks WHERE pull_id=%s", [p3]).fetchone()[0]
    assert note.startswith("skipped: last run under 10 hours ago"), note                                  # twice a day, spaced
    conn.execute("UPDATE paid_runs SET started_at = now() - interval '11 hours'")
    p4 = worker.enqueue(conn, B, "ai", ["--client-id", B, "--only", "ai"], "x")
    mid = worker.nightly(conn, "midday")
worker.work(budget=30, kick_after=False)
with connect() as conn:
    assert not conn.execute("SELECT note FROM pull_tasks WHERE pull_id=%s", [p4]).fetchone()[0].startswith("skipped")   # second run allowed
    assert mid and [r[0] for r in conn.execute("SELECT DISTINCT step FROM pull_tasks WHERE pull_id=%s ORDER BY 1", [mid])] == ["ai", "blogs", "semrush"]
os.environ.update(PAID_RUNS_PER_DAY="0")
with connect() as conn: assert worker.paid_ok(conn, A, "semrush")[0] is False
os.environ.update(PAID_RUNS_PER_DAY="1")
print("guardrail: a second paid run in the day is skipped whoever asks; twice a day needs the gap; the midday slot runs only paid sources; 0 switches them off")

# 7. data ready on open: a linked brand with no data starts its first pull; stale free data refreshes; nothing paid on revisits
with connect() as conn:
    conn.execute("DELETE FROM pull_tasks"); conn.execute("DELETE FROM pull_queue WHERE label='auto'")
    conn.execute("INSERT INTO client_integrations (client_id, gsc_site_url, google_connection_id) VALUES (%s,'sc-domain:beta.shop',%s) ON CONFLICT (client_id) DO UPDATE SET gsc_site_url=EXCLUDED.gsc_site_url", [B, gid])
    first = worker.ensure_fresh(conn, B)
    again = worker.ensure_fresh(conn, B)
    steps = [r[0] for r in conn.execute("SELECT step FROM pull_tasks WHERE pull_id=%s ORDER BY seq", [first])]
assert first and again is None and steps == ["gsc", "ga4", "rank", "ai", "pages", "blogs", "crawl"], steps
with connect() as conn:
    conn.execute("UPDATE pull_tasks SET status='done'"); conn.execute("UPDATE pull_queue SET requested_at = now() - interval '3 hours' WHERE label='auto'")
    conn.execute("""INSERT INTO gsc_search_daily (client_id, date, query, page, country, device, clicks, impressions, position, is_branded)
                    VALUES (%s, current_date - 3, 'q', 'https://beta.shop/', 'ind', 'MOBILE', 1, 10, 5, false)""", [B])
    conn.execute("INSERT INTO collector_runs (client_id, collector, status, started_at) VALUES (%s,'gsc','ok', now() - interval '40 hours')", [B])
    stale = worker.ensure_fresh(conn, B)
    steps = [r[0] for r in conn.execute("SELECT step FROM pull_tasks WHERE pull_id=%s ORDER BY seq", [stale])]
    conn.execute("UPDATE pull_tasks SET status='done'"); conn.execute("UPDATE pull_queue SET requested_at = now() - interval '3 hours' WHERE label='auto'")
    conn.execute("INSERT INTO collector_runs (client_id, collector, status, started_at) VALUES (%s,'gsc','ok', now())", [B])
    fresh = worker.ensure_fresh(conn, B)
assert steps == ["gsc", "ga4", "rank", "pages"] and fresh is None, steps                                 # free only; nothing when fresh
print("ready on open: a new brand's first pull starts by itself; stale data refreshes for free; fresh data starts nothing")

# 5. the endpoints: locked without the secret; the schedule queues and works; the buttons start at once
import app as webapp, dashboard
cl = webapp.app.test_client()
assert cl.post("/worker/run").status_code == 403 and cl.get("/cron/nightly").status_code == 403
assert cl.post("/worker/run", headers={"Authorization": "Bearer wrong"}).status_code == 403
worker.work = lambda **k: {"ran": 0, "queued": 0}
r = cl.get("/cron/nightly", headers={"Authorization": "Bearer s3cret-for-tests"}); assert r.status_code == 200 and r.json["nightly_pull"] is None   # already queued today
assert cl.post("/worker/run", headers={"Authorization": "Bearer s3cret-for-tests"}).json == {"ran": 0, "queued": 0}
dashboard.ONLINE = True
with cl.session_transaction() as s: s["staff_email"] = "seo@hovers.in"; s["csrf"] = "tok"
kicks.clear()
r = cl.post(f"/c/{A}/settings", data={"csrf": "tok", "what": "run", "only": "gsc,ga4"})
with connect() as conn:
    last = conn.execute("SELECT label, requested_by FROM pull_queue ORDER BY pull_id DESC LIMIT 1").fetchone()
if r is not None:
    assert r.status_code == 302 and "Started+on+the+Orbit+server" in r.headers["Location"] and kicks and last[1] == "seo@hovers.in", (r.headers.get("Location"), last)
print("endpoints: worker and schedule need the secret; a Pull button queues the tasks and starts the server at once")
