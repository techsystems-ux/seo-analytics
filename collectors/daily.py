#!/usr/bin/env python3
"""The daily job (Render cron). For every active client:
  every day      Search Console and GA4 (last 7 days, re-pulled so late data settles), ChatGPT prompts
  every Monday   page checks (or when never done)
  on request     Semrush, from Settings only (lifetime credit cap, see semrush.py); SEMRUSH_AUTO=1 adds it to Mondays
Each step is logged in collector_runs; one failure never stops the rest.
Usage: python daily.py [--client-id UUID] [--only gsc,ga4,ai,semrush,pages] [--force-weekly]
"""
import argparse, datetime as dt, os, sys, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "seo")]
from common import connect

def log(conn, client_id, name, fn):
    rid = conn.execute("INSERT INTO collector_runs (client_id, collector, status) VALUES (%s,%s,'running') RETURNING run_id",
                       [client_id, name]).fetchone()[0]
    try:
        out = fn()
        conn.execute("UPDATE collector_runs SET finished_at=now(), status='ok', error=%s WHERE run_id=%s", [str(out)[:500], rid])
        print(f"  {name}: {out}"); return True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"                                   # shown on screen; full traceback in the log
        conn.execute("UPDATE collector_runs SET finished_at=now(), status='error', error=%s WHERE run_id=%s",
                     [(msg[:400] + "\n" + traceback.format_exc()[-1200:]), rid])
        print(f"  {name}: FAILED {msg}", file=sys.stderr); traceback.print_exc(); return False

def has_rows(conn, table, client_id):
    """True when the table already holds at least 60 days of history for this brand (otherwise pull 90 days)."""
    return conn.execute(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE client_id=%s AND date <= current_date - 60)", [client_id]).fetchone()[0]

def weekly_due(conn, client_id, name, force):
    if force or dt.date.today().weekday() == 0: return True
    last = conn.execute("""SELECT max(started_at) FROM collector_runs WHERE client_id=%s AND collector=%s AND status='ok'""",
                        [client_id, name]).fetchone()[0]
    return last is None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--client-id"); ap.add_argument("--only", default="gsc,ga4,ai,semrush,pages")
    ap.add_argument("--force-weekly", action="store_true")
    a = ap.parse_args(); only = set(a.only.split(",")); ok = True
    import gsc, ga4
    from google_auth import SessionPool
    with connect() as conn:
        q = "SELECT client_id, name FROM clients WHERE status='active'" + (" AND client_id=%s" if a.client_id else "")
        clients = conn.execute(q, [a.client_id] if a.client_id else []).fetchall()
        gpool = {"gsc": SessionPool(conn, gsc.SCOPE), "ga4": SessionPool(conn, ga4.SCOPE)}
        for cid, name in clients:
            print(name)
            if "gsc" in only:
                for c, site, n, domain, aliases, gconn in gsc.clients_to_sync(conn, cid):
                    days = 7 if has_rows(conn, "gsc_search_daily", cid) else 90      # first pull: 90 days, so 28-day comparisons work
                    ok &= log(conn, cid, "gsc", lambda: gsc.sync_client(conn, gpool["gsc"].get(gconn), c, site, n, domain, aliases, days, 3))
            if "ga4" in only:
                for c, pid, n, gconn in ga4.clients_to_sync(conn, cid):
                    days = 7 if has_rows(conn, "ga4_organic_landing", cid) else 90
                    ok &= log(conn, cid, "ga4", lambda: ga4.sync_client(conn, gpool["ga4"].get(gconn), c, pid, days, 1))
            # Semrush never runs on the schedule unless SEMRUSH_AUTO=1; a person starts it from Settings (credit limit)
            if "semrush" in only and os.environ.get("SEMRUSH_API_KEY") and (a.force_weekly or os.environ.get("SEMRUSH_AUTO") == "1") \
               and weekly_due(conn, cid, "semrush", a.force_weekly):
                import semrush
                ok &= log(conn, cid, "semrush", lambda: semrush.sync_client(conn, cid, os.environ["SEMRUSH_API_KEY"],
                          max_units=int(os.environ.get("SEMRUSH_MAX_UNITS", "500"))))
            if "ai" in only and os.environ.get("OPENAI_API_KEY"):
                import ai_visibility
                ok &= log(conn, cid, "ai", lambda: ai_visibility.sync_client(conn, cid, os.environ["OPENAI_API_KEY"],
                          int(os.environ.get("AI_SAMPLES", "1")))[0])
            if "pages" in only and weekly_due(conn, cid, "pages", a.force_weekly):
                import run_checks, sitemap
                ok &= log(conn, cid, "sitemap", lambda: "{found} pages in the sitemap, {added} new, {keywords} keywords assigned".format(
                          **sitemap.sync_pages(conn, cid)))
                limit = int(os.environ.get("PAGES_PER_RUN", "100"))
                ok &= log(conn, cid, "pages", lambda: f"{len(run_checks.check_live(conn, cid, pause=0.5, limit=limit))} pages checked (top {limit} by priority)")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
