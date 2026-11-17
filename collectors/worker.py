"""The Orbit server worker: runs every pull on Vercel, with nothing on a Mac.

A pull (a row in pull_queue: a button in Orbit, a trial's first pull, or the nightly pull) is split into tasks, one step
for one brand each. work() runs tasks until its time budget is spent, then, if any are left, starts a fresh run of itself
(kick), so a long nightly pull carries on run after run. Each task is short enough for one function run (Vercel stops a
function after 300 seconds): long Search Console histories go in 10-day windows, ChatGPT prompts and the crawl stop at a
deadline and continue in the next task, blog drafts are written one per task.

Triggers: Vercel's schedule (/cron/nightly at 02:00 IST: everything; /cron/nightly?slot=midday at 14:00 IST: the paid
sources again when PAID_RUNS_PER_DAY >= 2), sign-up and the team's buttons, and opening a brand whose free data is missing
or stale (ensure_fresh: free sources only, except a brand's very first pull). Data is ready when people open Orbit.
Guardrail for paid sources (paid_ok): ChatGPT prompts, blog drafts and Semrush run at most PAID_RUNS_PER_DAY times per brand
in 24 hours, whatever asks; Semrush also within SEMRUSH_AUTO_UNITS a run and the lifetime SEMRUSH_TOTAL_CAP.
"""
import datetime as dt, json, os, sys, time, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "seo")]

ORDER = ["gsc", "gsc_history", "ga4", "rank", "semrush", "ai", "pages", "semrush_ideas", "semrush_backlinks", "semrush_gap",
         "semrush_audit", "semrush_rank", "research", "reddit", "youtube", "blogs", "crawl"]
NIGHTLY = "gsc,ga4,rank,semrush,ai,pages,blogs,crawl,youtube,reddit"
PAID_STEPS = ("ai", "blogs", "semrush")         # OpenAI (prompts, blog drafts) and Semrush: limited per brand per day
FREE_REFRESH = "gsc,ga4,rank,pages"            # what opening a dashboard with stale data may start (costs nothing)
RUN_BUDGET = int(os.environ.get("WORKER_BUDGET", "100"))       # seconds a run keeps starting new tasks
HARD_STOP = int(os.environ.get("WORKER_HARD_STOP", "270"))     # tasks must wrap up before this (Vercel stops at 300)
STALE = "6 minutes"                                              # a task running longer was cut off
MAX_PARALLEL = int(os.environ.get("WORKER_PARALLEL", "3"))
GSC_WINDOW = 10                                                  # days per Search Console task

def parse_args(args):
    """pull_queue.args holds daily.py-style arguments: --client-id, --only, --force-weekly, --max-units, --seeds, --research."""
    a, i, out = list(args or []), 0, {"only": NIGHTLY, "force": False}
    while i < len(a):
        k = a[i]
        if k == "--force-weekly": out["force"] = True; i += 1; continue
        if i + 1 < len(a) and k.startswith("--"):
            out[k[2:].replace("-", "_")] = a[i + 1]; i += 2; continue
        i += 1
    return out

def enqueue(conn, client_id, label, args, who=None):
    """A pull as tasks. Marked started so an old Mac app never picks it up as well. Returns the pull id."""
    o = parse_args(args)
    pid = conn.execute("""INSERT INTO pull_queue (client_id, label, args, requested_by, started_at) VALUES (%s,%s,%s,%s,now())
                          RETURNING pull_id""", [client_id, label, json.dumps(args), who]).fetchone()[0]
    clients = [o["client_id"]] if o.get("client_id") else \
              [str(r[0]) for r in conn.execute("SELECT client_id FROM clients WHERE status='active' ORDER BY created_at")]
    steps = [s for s in ORDER if s in set(o["only"].split(","))]
    if o.get("research") and "research" not in steps: steps.append("research")
    extra = {k: o[k] for k in ("max_units", "seeds", "research") if o.get(k)}
    for cid in clients:
        for n, step in enumerate(steps):
            conn.execute("INSERT INTO pull_tasks (pull_id, client_id, step, args, seq) VALUES (%s,%s,%s,%s,%s)",
                         [pid, cid, step, json.dumps({**extra, "force": o["force"]}), n * 100])
    return pid

def adopt_waiting(conn):
    """Pulls queued the old way (for the Mac) that nothing has started: turn them into tasks for this worker."""
    for pid, cid, label, args, who in conn.execute("""SELECT pull_id, client_id::text, label, args, requested_by FROM pull_queue q
            WHERE started_at IS NULL AND NOT EXISTS (SELECT 1 FROM pull_tasks t WHERE t.pull_id=q.pull_id)""").fetchall():
        enqueue(conn, cid, label, args, who)
        conn.execute("UPDATE pull_queue SET started_at=now(), finished_at=now(), exit_code=0 WHERE pull_id=%s", [pid])

def paid_per_day():
    return max(0, int(os.environ.get("PAID_RUNS_PER_DAY", "1")))

def nightly(conn, slot="nightly"):
    """Scheduled pulls of every brand. 'nightly' (02:00 IST): everything. 'midday' (14:00 IST): the paid sources again,
    only when PAID_RUNS_PER_DAY is 2 or more. Each slot is queued at most once in 10 hours."""
    if slot == "midday" and paid_per_day() < 2: return None
    if conn.execute("SELECT 1 FROM pull_queue WHERE label=%s AND requested_at > now() - interval '10 hours'", [slot]).fetchone():
        return None
    return enqueue(conn, None, slot, ["--only", NIGHTLY if slot == "nightly" else ",".join(PAID_STEPS)], "schedule")

def paid_ok(conn, cid, kind, pull_id=None):
    """The guardrail for paid sources: at most PAID_RUNS_PER_DAY starts per brand and kind in 24 hours, spaced at least
    PAID_MIN_GAP_HOURS apart (default: 22 h for once a day, 10 h for twice). Records the start when allowed.
    Whatever asked for it (schedule, a button, a sign-up), the same limit applies; opening a page never asks."""
    per_day = paid_per_day()
    if per_day == 0: return False, "paid sources are switched off (PAID_RUNS_PER_DAY=0)"
    gap = float(os.environ.get("PAID_MIN_GAP_HOURS", str(max(1, 24 / per_day - 2))))
    n, last = conn.execute("""SELECT count(*), max(started_at) FROM paid_runs WHERE client_id=%s AND kind=%s
                              AND started_at > now() - interval '24 hours'""", [cid, kind]).fetchone()
    if n >= per_day: return False, f"already ran {n} time{'s' if n != 1 else ''} in 24 hours (limit {per_day} a day)"
    if last and conn.execute("SELECT %s > now() - make_interval(secs => %s)", [last, gap * 3600]).fetchone()[0]:
        return False, f"last run under {gap:g} hours ago"
    conn.execute("INSERT INTO paid_runs (client_id, kind, pull_id) VALUES (%s,%s,%s)", [cid, kind, pull_id])
    return True, ""

def paid_next(conn, cid, kind):
    """For screens: (True, '') if a paid run may start now for this brand, else (False, when it can next run)."""
    per_day = paid_per_day()
    if per_day == 0: return False, "switched off"
    gap = float(os.environ.get("PAID_MIN_GAP_HOURS", str(max(1, 24 / per_day - 2))))
    rows = [r[0] for r in conn.execute("""SELECT started_at FROM paid_runs WHERE client_id=%s AND kind=%s
                                          AND started_at > now() - interval '24 hours' ORDER BY started_at""", [cid, kind])]
    if not rows: return True, ""
    nxt = max(rows[-1] + dt.timedelta(hours=gap), rows[0] + dt.timedelta(hours=24) if len(rows) >= per_day else rows[-1])
    now = dt.datetime.now(dt.timezone.utc)
    if len(rows) < per_day and nxt <= now: return True, ""
    return False, nxt

def ensure_fresh(conn, cid, who=None):
    """Called when someone opens a brand. Starts a free refresh (Search Console, GA4, rankings, page checks) if the brand
    has Search Console linked and its data is missing or more than 30 hours old, nothing is already waiting, and no
    automatic refresh was queued in the last 2 hours. Paid sources are added only for a brand's very first pull, and
    even then the daily limit applies. Returns the pull id or None."""
    linked = conn.execute("SELECT gsc_site_url IS NOT NULL FROM client_integrations WHERE client_id=%s", [cid]).fetchone()
    if not linked or not linked[0]: return None
    busy = conn.execute("""SELECT EXISTS (SELECT 1 FROM pull_tasks WHERE client_id=%s AND status IN ('queued','running'))
                           OR EXISTS (SELECT 1 FROM pull_queue WHERE client_id=%s AND label='auto' AND requested_at > now() - interval '2 hours')""",
                        [cid, cid]).fetchone()[0]
    if busy: return None
    has_data = conn.execute("SELECT EXISTS (SELECT 1 FROM gsc_search_daily WHERE client_id=%s)", [cid]).fetchone()[0]
    fresh = conn.execute("""SELECT EXISTS (SELECT 1 FROM collector_runs WHERE client_id=%s AND collector='gsc' AND status='ok'
                            AND started_at > now() - interval '30 hours')""", [cid]).fetchone()[0]
    if has_data and fresh: return None
    only = FREE_REFRESH + ("" if has_data else ",ai,blogs,crawl")
    return enqueue(conn, cid, "auto", ["--client-id", cid, "--only", only] + ([] if has_data else ["--force-weekly"]), who or "auto")

# ------------------------------------------------------------------ steps
def _daily(cid, step, a):
    import daily
    argv = ["--client-id", cid, "--only", step] + (["--force-weekly"] if a.get("force") else [])
    for k in ("max_units", "seeds", "research"):
        if a.get(k): argv += [f"--{k.replace('_', '-')}", str(a[k])]
    return daily.main(argv)

def _windows(start, end, size=GSC_WINDOW):
    out, d = [], start
    while d <= end:
        e = min(end, d + dt.timedelta(days=size - 1)); out.append((d, e)); d = e + dt.timedelta(days=1)
    return out

def step_gsc(conn, task, deadline, history=False):
    """Search Console in 10-day windows: the latest week each night; 90 days the first time; then 7 months of history once."""
    import gsc, daily
    from google_auth import SessionPool
    cid, a = task["client_id"], task["args"]
    rows = gsc.clients_to_sync(conn, cid)
    if not rows: return "Search Console not linked", []
    c, site, name, domain, aliases, gconn = rows[0]
    if "start" not in a:                                         # plan the windows, run the first, queue the rest
        if history:
            if daily.ran_ok(conn, cid, "gsc_history"): return "history already pulled", []
            s, e = gsc.window(215, 3)
            e = gsc.window(90, 3)[0] - dt.timedelta(days=1)          # the last 90 days come with the first pull
        else:
            s, e = gsc.window(7 if daily.has_rows(conn, "gsc_search_daily", cid) else 90, 3)
        wins = _windows(s, e)
        more = [(task["step"], {"start": str(x), "end": str(y), "last": i == len(wins) - 1}) for i, (x, y) in enumerate(wins)]
        if not history and not daily.ran_ok(conn, cid, "gsc_history") and not conn.execute(
                "SELECT 1 FROM pull_tasks WHERE client_id=%s AND step='gsc_history' AND status IN ('queued','running')", [cid]).fetchone():
            more.append(("gsc_history", {"_seq": 9000}))           # once, at the end of the pull, so the screens fill first
        return f"{len(wins)} windows planned", more
    session = SessionPool(conn, gsc.SCOPE).get(gconn)
    s, e = dt.date.fromisoformat(a["start"]), dt.date.fromisoformat(a["end"])
    name_ = "gsc_history" if history and a.get("last") else "gsc_window" if history else "gsc"
    ok = daily.log(conn, cid, name_, lambda: f"{gsc.sync_window(conn, session, c, site, name, domain, aliases, s, e)} rows, {s} to {e}")
    if not ok: raise RuntimeError(f"Search Console {s} to {e} failed (see the run log)")
    return f"{s} to {e}", []

def step_ai(conn, task, deadline):
    import ai_visibility, daily
    key = os.environ.get("OPENAI_API_KEY")
    if not key: return "no OpenAI key on the server", []
    cid, out = task["client_id"], {}
    def go():
        tally, _ = ai_visibility.sync_client(conn, cid, key, int(os.environ.get("AI_SAMPLES", "1")), deadline=deadline - 60,
                                             sentiment=os.environ.get("AI_SENTIMENT", "1") != "0")
        out.update(tally); return {k: v for k, v in tally.items() if k != "more"}
    daily.log(conn, cid, "ai", go)
    return str(out), ([("ai", {"cont": True})] if out.get("more") else [])

def step_blogs(conn, task, deadline):
    """Topic research once, then one draft per task (each OpenAI call with web search can take a minute or two)."""
    import blog_writer, daily
    cid, a = task["client_id"], task["args"]
    key = os.environ.get("OPENAI_API_KEY")
    left = a.get("left", blog_writer.PER_RUN)
    note = {}
    def go():
        if not a.get("researched"):
            conn.execute("""UPDATE content_drafts SET status='idea' WHERE client_id=%s AND status='writing'
                            AND updated_at < now() - interval '1 hour'""", [cid])
            note["added"] = blog_writer.research(conn, cid)
        note["written"] = blog_writer.write(conn, cid, key, n=1) if key and left > 0 else []
        return f"{note.get('added', 0)} new ideas, {len(note['written'])} draft written" + ("" if key else " (no OpenAI key: ideas only)")
    daily.log(conn, cid, "blogs", go)
    more = [("blogs", {"left": left - 1, "researched": True, "cont": True})] if note.get("written") and left - 1 > 0 else []
    return f"{len(note.get('written') or [])} draft", more

def step_crawl(conn, task, deadline):
    import crawler, daily
    cid, a = task["client_id"], task["args"]
    if not daily.weekly_due(conn, cid, "crawl", a.get("force")): return "not due (weekly)", []
    daily.log(conn, cid, "crawl", lambda: "{pages} pages crawled, health {health}%, {error} errors, {warning} warnings, {notice} notices".format(
        **crawler.run(conn, cid, limit=int(os.environ.get("CRAWL_LIMIT", "500")), pause=0.2, deadline=deadline - 30)))
    return "crawled", []

def step_semrush(conn, task, deadline):
    """Semrush keyword and competitor data, within SEMRUSH_AUTO_UNITS a run and the lifetime cap (SEMRUSH_TOTAL_CAP)."""
    if not os.environ.get("SEMRUSH_API_KEY"): return "no Semrush key on the server", []
    units = task["args"].get("max_units") or int(os.environ.get("SEMRUSH_AUTO_UNITS", "50"))
    if int(units) <= 0: return "automatic Semrush is off (SEMRUSH_AUTO_UNITS=0)", []
    rc = _daily(task["client_id"], "semrush", {**task["args"], "force": True, "max_units": units})
    return ("ok" if rc == 0 else "finished with errors (see the run log)"), []

def run_task(conn, task, deadline):
    """-> (note, [(step, args)] to queue next)."""
    step = task["step"]
    if step in PAID_STEPS and not task["args"].get("cont"):                 # the paid guardrail, once per paid run
        ok, why = paid_ok(conn, task["client_id"], step, task["pull_id"])
        if not ok: return f"skipped: {why}", []
    if step == "semrush": return step_semrush(conn, task, deadline)
    if step == "gsc": return step_gsc(conn, task, deadline)
    if step == "gsc_history": return step_gsc(conn, task, deadline, history=True)
    if step == "ai": return step_ai(conn, task, deadline)
    if step == "blogs": return step_blogs(conn, task, deadline)
    if step == "crawl": return step_crawl(conn, task, deadline)
    rc = _daily(task["client_id"], step, task["args"])          # ga4, rank, pages, reddit, youtube, semrush...: one call each
    return ("ok" if rc == 0 else "finished with errors (see the run log)"), []

# ------------------------------------------------------------------ the loop
CLAIM = """UPDATE pull_tasks SET status='running', started_at=now(), attempts=attempts+1 WHERE task_id = (
  SELECT t.task_id FROM pull_tasks t JOIN pull_queue q USING (pull_id)
  WHERE t.status='queued' AND NOT EXISTS (SELECT 1 FROM pull_tasks e WHERE e.pull_id=t.pull_id AND e.client_id IS NOT DISTINCT FROM t.client_id
                                            AND e.seq < t.seq AND e.status IN ('queued','running'))
  ORDER BY (q.label = 'nightly'), t.pull_id, t.seq, t.task_id LIMIT 1 FOR UPDATE OF t SKIP LOCKED)
RETURNING task_id, pull_id, client_id::text, step, args, seq, attempts"""

def _finish_pulls(conn):
    conn.execute("""UPDATE pull_queue q SET finished_at=now(),
                      exit_code = CASE WHEN EXISTS (SELECT 1 FROM pull_tasks t WHERE t.pull_id=q.pull_id AND t.status='error') THEN 1 ELSE 0 END
                    WHERE q.finished_at IS NULL AND EXISTS (SELECT 1 FROM pull_tasks t WHERE t.pull_id=q.pull_id)
                      AND NOT EXISTS (SELECT 1 FROM pull_tasks t WHERE t.pull_id=q.pull_id AND t.status IN ('queued','running'))""")

def pending(conn):
    return conn.execute("SELECT count(*) FILTER (WHERE status='queued'), count(*) FILTER (WHERE status='running') FROM pull_tasks").fetchone()

def work(budget=RUN_BUDGET, hard_stop=HARD_STOP, connect=None, kick_after=True):
    """Runs tasks for up to `budget` seconds. Returns a summary; starts another run if tasks are left."""
    if connect is None: from common import connect
    os.environ.setdefault("APIFY_WAIT", "180")
    t0, done, failed = time.time(), 0, 0
    with connect() as conn:
        conn.execute(f"""UPDATE pull_tasks SET status = CASE WHEN attempts >= 3 THEN 'error' ELSE 'queued' END,
                         note = 'cut off; ' || CASE WHEN attempts >= 3 THEN 'gave up after 3 tries' ELSE 'retrying' END
                         WHERE status='running' AND started_at < now() - interval '{STALE}'""")
        adopt_waiting(conn)
        _finish_pulls(conn)
        if pending(conn)[1] >= MAX_PARALLEL: return {"ran": 0, "busy": True}
    while time.time() - t0 < budget:
        with connect() as conn:
            row = conn.execute(CLAIM).fetchone()
        if not row: break
        task = dict(zip(["task_id", "pull_id", "client_id", "step", "args", "seq", "attempts"], row))
        try:
            with connect() as conn:
                note, more = run_task(conn, task, t0 + hard_stop)
                for i, (step, args) in enumerate(more):
                    seq = args.pop("_seq", task["seq"] + 1 + i)
                    keep = {k: v for k, v in task["args"].items() if k not in ("start", "end", "last")}
                    conn.execute("INSERT INTO pull_tasks (pull_id, client_id, step, args, seq) VALUES (%s,%s,%s,%s,%s)",
                                 [task["pull_id"], task["client_id"], step, json.dumps({**keep, **args}), seq])
                conn.execute("UPDATE pull_tasks SET status='done', finished_at=now(), note=%s WHERE task_id=%s", [str(note)[:300], task["task_id"]])
            done += 1
        except Exception as e:
            failed += 1
            with connect() as conn:
                conn.execute("UPDATE pull_tasks SET status=CASE WHEN attempts >= 3 THEN 'error' ELSE 'queued' END, note=%s WHERE task_id=%s",
                             [f"{type(e).__name__}: {e}"[:300] + "\n" + traceback.format_exc()[-600:], task["task_id"]])
        with connect() as conn: _finish_pulls(conn)
    with connect() as conn:
        queued, running = pending(conn)
    if queued and kick_after: kick()
    return {"ran": done, "failed": failed, "queued": queued, "running": running, "seconds": round(time.time() - t0)}

def kick():
    """Start a run on the server without waiting for it (the request is sent; this side does not wait for the answer)."""
    import requests
    host, secret = os.environ.get("APP_HOST"), os.environ.get("CRON_SECRET")
    if not host or not secret: return False
    try:
        requests.post(f"https://{host}/worker/run", headers={"Authorization": f"Bearer {secret}"}, timeout=2)
    except Exception:
        pass                                    # a timeout is expected: the run carries on without us
    return True
