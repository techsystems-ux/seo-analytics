#!/usr/bin/env python3
"""Read a brand's Semrush Project: Site Audit results and Position Tracking positions.

The project is set up and crawled in Semrush itself (that uses the project's limits, not API units). This file
only reads results, with the cheapest calls Semrush offers, and each call is checked against the caps first:
  Site Audit info .................. 100 units   (health, error/warning/notice counts, issue counts by type)
  Site Audit issue list ............ 100 units   (issue names)
  Pages for one issue .............. 100 units   (only for the top error types, --issue-pages N)
  Position Tracking positions ...... 100 units per keyword returned
The 10,000-unit snapshot report is never called.

  python semrush_projects.py audit --client-id UUID --max-units 500
  python semrush_projects.py rank  --client-id UUID --max-units 2000 [--keywords 20]
"""
import argparse, datetime as dt, os, sys
from common import connect
from semrush import Semrush, BudgetExceeded

def estimate(feature, issue_pages=5, keywords=20):
    return 200 + 100 * issue_pages if feature == "audit" else 100 * keywords

def project_of(conn, client_id):
    pid, domain = conn.execute("SELECT semrush_project_id, domain FROM clients WHERE client_id=%s", [client_id]).fetchone()
    if not pid: raise ValueError("No Semrush project ID saved for this brand (Settings → Data)")
    return pid.strip(), domain.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")

def severity(issue_id, kind=None):
    """Semrush numbers errors below 100, warnings 100–199 and notices from 200."""
    if kind in ("error", "errors"): return "error"
    if kind in ("warning", "warnings"): return "warning"
    if kind in ("notice", "notices"): return "notice"
    i = int(issue_id)
    return "error" if i < 100 else "warning" if i < 200 else "notice"

def issue_counts(info):
    """{issue_id: (count, kind)} from the info response: 'defects' {id: count}, or the snapshot's typed lists."""
    out = {}
    for k, v in (info.get("defects") or {}).items():
        if v: out[str(k)] = (int(v), None)
    snap = info.get("current_snapshot") or {}
    for kind in ("errors", "warnings", "notices"):
        for it in snap.get(kind) or []:
            if isinstance(it, dict) and it.get("count"): out[str(it["id"])] = (int(it["count"]), kind)
    return out

def import_audit(conn, client_id, key, issue_pages=5, max_units=700, session=None, today=None):
    today = today or dt.date.today()
    pid, _ = project_of(conn, client_id)
    sm = Semrush(key, conn, client_id, max_units, session, feature="audit")
    try:
        info = sm.project_call("siteaudit_info", f"reports/v1/projects/{pid}/siteaudit/info", 100)
        meta = sm.project_call("siteaudit_meta_issues", f"reports/v1/projects/{pid}/siteaudit/meta/issues", 100)
    except BudgetExceeded as e:
        return f"stopped: {e} ({sm.spent} units)"
    titles = {str(i.get("id")): i.get("title") or i.get("title_page") or f"Semrush issue {i.get('id')}"
              for i in (meta.get("issues") if isinstance(meta, dict) else meta) or []}
    counts = issue_counts(info)
    snap = info.get("current_snapshot") or {}
    quality = (snap.get("quality") or {}).get("value")
    crawl_id = conn.execute("""INSERT INTO tech_crawls (client_id, source, finished_at, pages_crawled, health_score, errors, warnings, notices, external_id)
                               VALUES (%s,'semrush',now(),%s,%s,%s,%s,%s,%s) RETURNING crawl_id""",
                            [client_id, info.get("pages_crawled"), quality, info.get("errors"), info.get("warnings"), info.get("notices"),
                             snap.get("snapshot_id")]).fetchone()[0]
    due = {"error": 7, "warning": 14, "notice": 30}
    for iid, (n, kind) in counts.items():
        sev = severity(iid, kind)
        conn.execute("""INSERT INTO tech_issues (client_id, issue_code, source, title, severity, team, how_to_fix, pages, due_date)
                        VALUES (%s,%s,'semrush',%s,%s,'tech','See the issue in Semrush Site Audit for the fix.',%s,%s)
                        ON CONFLICT (client_id, issue_code) DO UPDATE SET pages=EXCLUDED.pages, last_seen=now(), title=EXCLUDED.title,
                          status=CASE WHEN tech_issues.status IN ('fixed','verified') THEN 'open' ELSE tech_issues.status END""",
                     [client_id, f"semrush:{iid}", titles.get(iid, f"Semrush issue {iid}"), sev, n, today + dt.timedelta(days=due[sev])])
    conn.execute("""UPDATE tech_issues SET pages=0, status=CASE WHEN status='fixed' THEN 'verified' ELSE status END
                    WHERE client_id=%s AND source='semrush' AND NOT (issue_code = ANY(%s)) AND pages > 0""",
                 [client_id, [f"semrush:{i}" for i in counts]])
    listed = 0
    if issue_pages and snap.get("snapshot_id"):
        top = sorted([(n, iid) for iid, (n, kind) in counts.items() if severity(iid, kind) == "error"], reverse=True)[:issue_pages]
        for _, iid in top:
            try:
                res = sm.project_call("siteaudit_issue_pages", f"reports/v1/projects/{pid}/siteaudit/snapshot/{snap['snapshot_id']}/issue/{iid}", 100,
                                      params={"limit": 100})
            except BudgetExceeded:
                break
            rows = res.get("data", []) if isinstance(res, dict) else res
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO tech_issue_pages (crawl_id, client_id, issue_code, url, detail) VALUES (%s,%s,%s,%s,%s)
                                   ON CONFLICT DO NOTHING""",
                                [(crawl_id, client_id, f"semrush:{iid}", r.get("source_url") or r.get("page_url") or r.get("url"),
                                  r.get("target_url")) for r in rows if (r.get("source_url") or r.get("page_url") or r.get("url"))])
            listed += 1
    return f"health {quality}, {len(counts)} issue types, pages listed for {listed} ({sm.spent} units)"

def parse_positions(resp, mask):
    """[(keyword, date, position, url)] from tracking_position_organic. Dt is {YYYYMMDD: {mask: position}}; Lu holds landing URLs."""
    data = resp.get("data") or {}
    rows = data.values() if isinstance(data, dict) else data
    out = []
    for r in rows:
        kw = r.get("Ph")
        if not kw: continue
        lu = r.get("Lu") or {}
        for day, v in (r.get("Dt") or {}).items():
            pos = v.get(mask, next(iter(v.values()), None)) if isinstance(v, dict) else v
            url = lu.get(mask) if isinstance(lu, dict) else None
            if isinstance(url, dict): url = url.get(str(day))
            try: pos = float(pos) if pos not in (None, "", "-", 0, "0") else None
            except (TypeError, ValueError): pos = None
            out.append((kw, dt.datetime.strptime(str(day), "%Y%m%d").date(), pos, url))
    return out

def import_positions(conn, client_id, key, keywords=20, days=7, max_units=2000, session=None, today=None):
    today = today or dt.date.today()
    pid, domain = project_of(conn, client_id)
    mask = f"*.{domain}/*"
    sm = Semrush(key, conn, client_id, max_units, session, feature="rank")
    try:
        resp = sm.project_call("tracking_position_organic", f"reports/v1/projects/{pid}/tracking/", 100 * keywords,
                               params={"action": "report", "type": "tracking_position_organic", "url": mask, "display_limit": keywords,
                                       "date_begin": (today - dt.timedelta(days=days)).strftime("%Y%m%d"), "date_end": today.strftime("%Y%m%d")},
                               per_item=(100, lambda d: len(d.get("data") or {})))
    except BudgetExceeded as e:
        return f"stopped: {e} ({sm.spent} units)"
    rows = parse_positions(resp, mask)
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO rank_keywords (client_id, keyword, tags) VALUES (%s,%s,'{semrush}') ON CONFLICT DO NOTHING""",
                        [(client_id, k) for k in {r[0] for r in rows}])
        cur.executemany("""INSERT INTO rank_positions (client_id, keyword, date, source, position, url) VALUES (%s,%s,%s,'semrush',%s,%s)
                           ON CONFLICT (client_id, keyword, date, source) DO UPDATE SET position=EXCLUDED.position, url=EXCLUDED.url""",
                        [(client_id, *r) for r in rows])
    return f"{len({r[0] for r in rows})} keywords, {len(rows)} daily positions ({sm.spent} units)"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["audit", "rank"]); ap.add_argument("--client-id", required=True)
    ap.add_argument("--max-units", type=int, required=True); ap.add_argument("--issue-pages", type=int, default=5)
    ap.add_argument("--keywords", type=int, default=20)
    a = ap.parse_args()
    with connect() as conn:
        key = os.environ["SEMRUSH_API_KEY"]
        print(import_audit(conn, a.client_id, key, a.issue_pages, a.max_units) if a.what == "audit"
              else import_positions(conn, a.client_id, key, a.keywords, max_units=a.max_units))
