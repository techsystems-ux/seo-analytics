"""Staff dashboard: Overview, Search performance, Competitors, AI visibility, Pages, Settings. One client at a time.
Registered by app.py. Every screen reads through analytics.py."""
import os, sys, json, secrets, subprocess, csv, io
from flask import Blueprint, render_template, request, redirect, url_for, session, abort
from markupsafe import Markup, escape
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "collectors"), os.path.join(HERE, "..", "seo"), os.path.join(HERE, "..", "scripts")]
from common import connect
import analytics as A

bp = Blueprint("dash", __name__)
STAGES = {"awareness": "Discovery", "consideration": "Research", "decision": "Decision"}

def staff_required():
    if not session.get("staff_email"): abort(redirect(url_for("home")))

def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]

def check_csrf():
    if not request.form.get("csrf") or request.form.get("csrf") != session.get("csrf"): abort(400, "Form expired. Reload the page and try again.")

@bp.before_request
def _gate(): staff_required()

def client_or_404(conn, cid):
    r = conn.execute("SELECT client_id, name, domain, sample_semrush FROM clients WHERE client_id::text=%s", [cid]).fetchone()
    if not r: abort(404)
    return {"id": str(r[0]), "name": r[1], "domain": r[2], "sample": r[3]}

def view(tpl, client=None, active=None, **ctx):
    return render_template(tpl, client=client, active=active, staff=session.get("staff_email"), csrf=csrf(), stages=STAGES, **ctx)

# ------------------------------------------------------------------ formatting helpers for templates
@bp.app_template_filter("n")
def fmt_n(v):
    if v is None: return "–"
    v = float(v)
    return f"{v:,.0f}" if abs(v) >= 100 or v == int(v) else f"{v:,.1f}"

@bp.app_template_filter("pct")
def fmt_pct(v, digits=0):
    return "–" if v is None else f"{100 * float(v):.{digits}f}%"

@bp.app_template_filter("delta")
def fmt_delta(v, unit="%"):
    if v is None: return Markup('<span class="mute">no earlier data</span>')
    cls = "up" if v > 0 else "down" if v < 0 else "mute"
    return Markup(f'<span class="{cls}">{"+" if v > 0 else ""}{v}{unit}</span>')

@bp.app_template_filter("path")
def fmt_path(url):
    u = url or ""
    for p in ("https://", "http://"): u = u.replace(p, "")
    return "/" + u.split("/", 1)[1] if "/" in u else "/"

def line_chart(points, series, colors=("#B20F00", "#000000"), height=170, ymax=None):
    """points = [{date, s1, s2}] -> inline SVG, no JavaScript. Labels the first, middle and last dates."""
    if len(points) < 2: return Markup('<p class="mute">Not enough data yet for a chart.</p>')
    W, H, L, B = 640, height, 36, 22
    vals = [float(p[s] or 0) for p in points for s in series]
    top = ymax or (max(vals) or 1) * 1.1
    x = lambda i: L + i * (W - L - 8) / (len(points) - 1)
    y = lambda v: 8 + (H - B - 8) * (1 - float(v or 0) / top)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Daily trend" style="width:100%;height:auto">']
    for f in (0, 0.5, 1):
        yy = y(top * f); out.append(f'<line x1="{L}" x2="{W-8}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#ECECEC"/>'
                                    f'<text x="{L-6}" y="{yy+4:.1f}" font-size="10" text-anchor="end" fill="#5A5A5A">{top*f:,.0f}</text>')
    for k, s in enumerate(series):
        d = " ".join(f'{"M" if i == 0 else "L"}{x(i):.1f},{y(p[s]):.1f}' for i, p in enumerate(points))
        out.append(f'<path d="{d}" fill="none" stroke="{colors[k % len(colors)]}" stroke-width="2"/>')
    for i, anchor in ((0, "start"), (len(points) // 2, "middle"), (len(points) - 1, "end")):
        out.append(f'<text x="{x(i):.1f}" y="{H-6}" font-size="10" text-anchor="{anchor}" fill="#5A5A5A">{points[i]["date"].strftime("%d %b")}</text>')
    return Markup("".join(out) + "</svg>")

# ------------------------------------------------------------------ screens
@bp.get("/c")
def clients():
    with connect() as conn:
        rows = conn.execute("""SELECT c.client_id, c.name, c.domain,
                                 (SELECT visibility_score FROM scores_daily s WHERE s.client_id=c.client_id AND s.brand='' ORDER BY date DESC LIMIT 1),
                                 (SELECT max(date) FROM gsc_search_daily g WHERE g.client_id=c.client_id),
                                 (SELECT count(*) FROM prompts p WHERE p.client_id=c.client_id AND p.approved)
                               FROM clients c WHERE c.status='active' ORDER BY c.name""").fetchall()
    return view("clients.html", clients=[{"id": str(r[0]), "name": r[1], "domain": r[2], "score": r[3], "band": A.band(r[3]),
                                          "gsc_to": r[4], "prompts": r[5]} for r in rows])

@bp.get("/c/<cid>")
def overview(cid):
    with connect() as conn:
        c = client_or_404(conn, cid)
        vis, search, rev = A.visibility(conn, cid), A.search_totals(conn, cid), A.organic_revenue(conn, cid)
        cycle, prompts = A.prompt_results(conn, cid)
        last, gaps = A.keyword_gaps(conn, cid, 6)
        pages = A.page_summary(conn, cid)
        chart = line_chart(A.daily_clicks(conn, cid, 90), ["nonbranded", "branded"])
        runs = A.last_runs(conn, cid)
    won = sum(1 for p in prompts if p["rank"]); asked = sum(1 for p in prompts if p["answer_id"])
    return view("overview.html", c, "overview", vis=vis, search=search, rev=rev, won=won, asked=asked, gaps=gaps,
                fixes=sum(p["fails"] for p in pages), pages_checked=sum(1 for p in pages if p["checked_at"]), chart=chart, runs=runs)

@bp.get("/c/<cid>/search")
def search(cid):
    tab = request.args.get("tab", "queries"); kind = request.args.get("kind", "nonbranded")
    with connect() as conn:
        c = client_or_404(conn, cid)
        totals = A.search_totals(conn, cid)
        chart = line_chart(A.daily_clicks(conn, cid, 90), ["nonbranded", "branded"])
        data = {"queries": lambda: A.top_queries(conn, cid, {"all": None, "branded": True, "nonbranded": False}[kind]),
                "pages": lambda: A.top_pages(conn, cid),
                "revenue": lambda: A.revenue_by_landing(conn, cid),
                "rankings": lambda: A.rankings(conn, cid)}[tab]()
        rev = A.organic_revenue(conn, cid)
        kw = request.args.get("kw")
        trend = line_chart([{"date": t["date"], "pos": t["position"]} for t in A.rank_trend(conn, cid, kw)], ["pos"]) if kw and tab == "rankings" else None
        actions = semrush_actions(conn, cid) if tab == "rankings" else {}
    return view("search.html", c, "search", tab=tab, kind=kind, totals=totals, chart=chart, data=data, rev=rev, kw=kw, trend=trend, actions=actions)

@bp.post("/c/<cid>/rankings")
def rankings_save(cid):
    check_csrf()
    with connect() as conn:
        client_or_404(conn, cid)
        if request.form.get("remove"):
            conn.execute("DELETE FROM rank_keywords WHERE client_id=%s AND keyword=%s", [cid, request.form["remove"]])
            conn.execute("DELETE FROM rank_positions WHERE client_id=%s AND keyword=%s", [cid, request.form["remove"]])
        n = 0
        for line in request.form.get("keywords", "").splitlines():
            kw, _, url = line.partition("|")
            if kw.strip():
                n += conn.execute("""INSERT INTO rank_keywords (client_id, keyword, target_url) VALUES (%s,%s,%s)
                                     ON CONFLICT (client_id, keyword) DO UPDATE SET target_url=coalesce(EXCLUDED.target_url, rank_keywords.target_url)""",
                                  [cid, kw.strip().lower()[:200], url.strip() or None]).rowcount
        if n:
            import rank
            rank.sync_client(conn, cid, days=90)          # fills history from Search Console data already stored: free and instant
    return redirect(url_for("dash.search", cid=cid, tab="rankings", msg=f"Added {n} keywords with their Search Console history." if n else None))

@bp.get("/c/<cid>/competitors")
def competitors(cid):
    with connect() as conn:
        c = client_or_404(conn, cid)
        comps, vis = A.competitors(conn, cid), A.visibility(conn, cid)
        gap_day, gaps = A.keyword_gaps(conn, cid)
        kw_day, kws = A.semrush_keywords(conn, cid)
        budget = A.semrush_budget(conn)
        bl, actions = A.backlinks(conn, cid), semrush_actions(conn, cid)
    return view("competitors.html", c, "competitors", comps=comps, vis=vis, gaps=gaps, gap_day=gap_day, kws=kws, kw_day=kw_day, budget=budget,
                tab=request.args.get("tab", "gap"), bl=bl, actions=actions)

@bp.post("/c/<cid>/competitors")
def competitors_save(cid):
    check_csrf()
    with connect() as conn:
        client_or_404(conn, cid)
        if request.form.get("add_name"):
            conn.execute("""INSERT INTO competitors (client_id, name, domain, tracked) VALUES (%s,%s,%s,true)
                            ON CONFLICT (client_id, name) DO UPDATE SET tracked=true, domain=coalesce(EXCLUDED.domain, competitors.domain)""",
                         [cid, request.form["add_name"].strip(), (request.form.get("add_domain") or "").strip().lower() or None])
        if request.form.get("toggle"):
            conn.execute("UPDATE competitors SET tracked = NOT tracked WHERE client_id=%s AND competitor_id::text=%s", [cid, request.form["toggle"]])
        if request.form.get("own_brand"):           # a sister brand: never tracked as a competitor, listed in the brand rules
            row = conn.execute("""UPDATE competitors SET own_brand = NOT own_brand, tracked = false WHERE client_id=%s AND competitor_id::text=%s
                                  RETURNING own_brand, domain""", [cid, request.form["own_brand"]]).fetchone()
            if row and row[1]:
                conn.execute("""UPDATE brand_rules SET sister_domains = CASE WHEN %s THEN (SELECT array(SELECT DISTINCT unnest(coalesce(sister_domains,'{}') || ARRAY[%s::text])))
                                  ELSE array_remove(sister_domains, %s) END WHERE client_id=%s""", [row[0], row[1], row[1], cid])
        if request.form.get("aliases_for"):
            conn.execute("UPDATE competitors SET name=%s, aliases=%s WHERE client_id=%s AND competitor_id::text=%s",
                         [request.form["name"].strip(), [a.strip() for a in request.form.get("aliases", "").split(",") if a.strip()],
                          cid, request.form["aliases_for"]])
        if request.form.get("prospect"):
            conn.execute("UPDATE backlink_gap SET status=%s WHERE client_id=%s AND domain=%s AND status <> %s",
                         [request.form["status"], cid, request.form["prospect"], request.form["status"]])
            return redirect(url_for("dash.competitors", cid=cid, tab="backlinks"))
    return redirect(url_for("dash.competitors", cid=cid, tab="list"))

@bp.get("/c/<cid>/ai")
def ai(cid):
    stage = request.args.get("stage", "all")
    with connect() as conn:
        c = client_or_404(conn, cid)
        vis = A.visibility(conn, cid)
        cycle, prompts = A.prompt_results(conn, cid)
        cited, others = A.top_cited_domains(conn, cid), A.untracked_brands(conn, cid)
    if stage != "all": prompts = [p for p in prompts if p["stage"] == stage]
    trend = line_chart([{"date": t["date"], "score": t["score"]} for t in (vis or {}).get("trend", [])], ["score"], ymax=100)
    return view("ai.html", c, "ai", vis=vis, prompts=prompts, cycle=cycle, cited=cited, others=others, stage=stage, trend=trend)

@bp.get("/c/<cid>/ai/answer/<aid>")
def answer(cid, aid):
    with connect() as conn:
        c = client_or_404(conn, cid); d = A.answer_detail(conn, cid, aid)
    if not d: abort(404)
    return view("answer.html", c, "ai", d=d)

@bp.get("/c/<cid>/pages")
def pages(cid):
    t, show, issue = request.args.get("type", "all"), request.args.get("show", "checked"), request.args.get("issue")
    with connect() as conn:
        c = client_or_404(conn, cid)
        rows = A.page_summary(conn, cid)
        issues = A.site_issues(conn, cid)
        with_issue = A.pages_with_issue(conn, cid, issue) if issue else None
        shown = [r for r in rows if (t == "all" or r["type"] == t)
                 and (bool(r["checked_at"]) == (show == "checked"))
                 and (with_issue is None or str(r["page_id"]) in with_issue)]
        sel = request.args.get("page") or (str(shown[0]["page_id"]) if shown else None)
        audit = A.page_audit(conn, cid, sel) if sel else None
    counts = {k: sum(1 for r in rows if r["type"] == k) for k in ("home", "collection", "product", "blog", "static")}
    checked = [r for r in rows if r["checked_at"]]
    return view("pages.html", c, "pages", rows=shown, sel=sel, audit=audit, type=t, show=show, issue=issue, issues=issues,
                all_count=len(rows), counts=counts, checked_count=len(checked), fixes=sum(r["fails"] for r in checked),
                issue_name=next((i["name"] for i in issues if i["id"] == issue), issue),
                selected=next((r for r in rows if str(r["page_id"]) == sel), None))

@bp.post("/c/<cid>/pages")
def pages_save(cid):
    check_csrf()
    import run_checks
    with connect() as conn:
        client_or_404(conn, cid)
        f = request.files.get("csv")
        if f and f.filename:
            n = run_checks.import_pages(conn, cid, csv.DictReader(io.StringIO(f.read().decode("utf-8-sig"))))
            return redirect(url_for("dash.pages", cid=cid, msg=f"Imported {n} pages. Checks run with the next pull, or press Check pages now."))
    start_job(cid, "pages")
    return redirect(url_for("dash.pages", cid=cid, msg="Reading the sitemap and checking pages. The list fills in within a few minutes; refresh to see progress."))

@bp.get("/c/<cid>/settings")
def settings(cid):
    tab = request.args.get("tab", "brand")
    import run_checks
    with connect() as conn:
        c = client_or_404(conn, cid)
        rules = run_checks.load_brand(conn, cid)
        prompts = conn.execute("SELECT prompt_id, text, funnel_stage, approved, active FROM prompts WHERE client_id=%s ORDER BY approved, funnel_stage, text",
                               [cid]).fetchall()
        integ = conn.execute("SELECT gsc_site_url, ga4_property_id FROM client_integrations WHERE client_id=%s", [cid]).fetchone()
        runs, budget = A.last_runs(conn, cid), A.semrush_budget(conn)
        project = conn.execute("SELECT semrush_project_id FROM clients WHERE client_id=%s", [cid]).fetchone()[0]
        actions, by_feature = semrush_actions(conn, cid), A.semrush_by_feature(conn)
    keys = {k: bool(os.environ.get(k)) for k in ("OPENAI_API_KEY", "SEMRUSH_API_KEY", "APIFY_TOKEN", "YOUTUBE_API_KEY")}
    return view("settings.html", c, "settings", tab=tab, rules=rules, prompts=prompts, integ=integ, runs=runs, budget=budget, keys=keys,
                msg=request.args.get("msg"), project=project, actions=actions, by_feature=by_feature)

LIST_FIELDS = ["brand_spellings", "product_names", "usps", "usp_markers", "banned_words", "title_banned_words", "claims_to_avoid",
               "region_words", "wrong_domains", "sister_domains"]

@bp.post("/c/<cid>/settings")
def settings_save(cid):
    check_csrf()
    import run_checks
    what = request.form.get("what")
    with connect() as conn:
        client_or_404(conn, cid)
        if what == "brand":
            rules = {f: [v.strip() for v in request.form.get(f, "").replace("\n", ",").split(",") if v.strip()] for f in LIST_FIELDS}
            fixes = {}
            for line in request.form.get("term_fixes", "").splitlines():
                if "=>" in line:
                    w, r = line.split("=>", 1)
                    if w.strip() and r.strip(): fixes[w.strip()] = r.strip()
            rules.update(term_fixes=fixes, correct_domain=request.form.get("correct_domain", "").strip() or None,
                         theme_appends_brand=bool(request.form.get("theme_appends_brand")), tone=request.form.get("tone") or None,
                         reviewer=request.form.get("reviewer") or None,
                         review_days=int(request.form["review_days"]) if request.form.get("review_days", "").isdigit() else None)
            run_checks.save_brand(conn, cid, rules)
            if request.form.get("signed_off"):
                conn.execute("UPDATE brand_rules SET approved_by=%s, approved_at=now() WHERE client_id=%s", [request.form.get("approved_by") or None, cid])
            return redirect(url_for("dash.settings", cid=cid, tab="brand", msg="Brand rules saved."))
        if what == "prompt_add":
            for line in request.form.get("text", "").splitlines():
                if line.strip():
                    conn.execute("""INSERT INTO prompts (client_id, text, funnel_stage, approved) VALUES (%s,%s,%s,false)
                                    ON CONFLICT DO NOTHING""", [cid, line.strip()[:500], request.form.get("stage", "consideration")])
        elif what == "prompt_toggle":
            conn.execute("UPDATE prompts SET approved = NOT approved WHERE client_id=%s AND prompt_id::text=%s", [cid, request.form["id"]])
        elif what == "prompt_approve_all":
            conn.execute("UPDATE prompts SET approved = true WHERE client_id=%s AND active", [cid])
        elif what == "prompt_delete":
            conn.execute("DELETE FROM prompts WHERE client_id=%s AND prompt_id::text=%s", [cid, request.form["id"]])
        elif what in ("sample_load", "sample_clear"):
            import sample_semrush
            msg = sample_semrush.load(conn, cid) if what == "sample_load" else sample_semrush.clear(conn, cid)
            return redirect(url_for("dash.settings", cid=cid, tab="data", msg=msg[0].upper() + msg[1:] + "."))
        elif what == "semrush_project":
            pid = "".join(ch for ch in request.form.get("project_id", "") if ch.isdigit()) or None
            conn.execute("UPDATE clients SET semrush_project_id=%s WHERE client_id=%s", [pid, cid])
            return redirect(url_for("dash.settings", cid=cid, tab="data", msg="Semrush project saved." if pid else "Semrush project removed."))
        elif what == "run":
            start_job(cid, request.form.get("only", "gsc,ga4,ai,semrush,pages"))
            return redirect(url_for("dash.settings", cid=cid, tab="data", msg="Pull started. Results appear as each step finishes."))
    return redirect(url_for("dash.settings", cid=cid, tab="prompts"))

# ------------------------------------------------------------------ technical audit, content ideas, Semrush feature runs
@bp.get("/c/<cid>/technical")
def technical(cid):
    show, code = request.args.get("show", "open"), request.args.get("issue")
    with connect() as conn:
        c = client_or_404(conn, cid)
        summary, issues = A.tech_summary(conn, cid), A.tech_issues(conn, cid, show)
        pages = A.tech_issue_pages(conn, cid, code) if code else None
        sel = next((i for i in A.tech_issues(conn, cid, "all") if i["code"] == code), None) if code else None
        actions = semrush_actions(conn, cid)
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in ("error", "warning", "notice")}
    return view("technical.html", c, "technical", summary=summary, issues=issues, show=show, code=code, pages=pages, sel=sel,
                counts=counts, actions=actions, today=__import__("datetime").date.today())

@bp.post("/c/<cid>/technical")
def technical_save(cid):
    check_csrf()
    with connect() as conn:
        client_or_404(conn, cid)
        if request.form.get("issue"):
            due = request.form.get("due") or None
            conn.execute("UPDATE tech_issues SET status=%s, owner=%s, due_date=%s, updated_at=CASE WHEN status IS DISTINCT FROM %s THEN now() ELSE updated_at END WHERE client_id=%s AND issue_code=%s",
                         [request.form.get("status", "open"), (request.form.get("owner") or "").strip() or None, due, request.form.get("status", "open"),
                          cid, request.form["issue"]])
            return redirect(url_for("dash.technical", cid=cid, issue=request.form["issue"], show=request.form.get("show", "open")))
    start_job(cid, "crawl")
    return redirect(url_for("dash.technical", cid=cid, msg="Crawl started. It checks up to 1,500 pages politely, so it can take 10 to 20 minutes; refresh to see results."))

@bp.get("/c/<cid>/ideas")
def ideas(cid):
    kind, status = request.args.get("kind"), request.args.get("status", "new")
    with connect() as conn:
        c = client_or_404(conn, cid)
        rows = A.ideas(conn, cid, kind or None, status)
        import semrush_suite
        seeds = semrush_suite.default_seeds(conn, cid)
        actions = semrush_actions(conn, cid)
    for r in rows: r["features"] = A.serp_features(r["fk"])
    return view("ideas.html", c, "ideas", rows=rows, kind=kind, status=status, seeds=seeds, actions=actions)

@bp.post("/c/<cid>/ideas")
def ideas_save(cid):
    check_csrf()
    with connect() as conn:
        client_or_404(conn, cid)
        conn.execute("UPDATE keyword_ideas SET status=%s, updated_at=now() WHERE client_id=%s AND kind=%s AND keyword=%s",
                     [request.form["status"], cid, request.form["kind"], request.form["keyword"]])
    return redirect(url_for("dash.ideas", cid=cid, kind=request.form.get("f_kind") or None, status=request.form.get("f_status", "new")))

def semrush_actions(conn, cid, seeds=None):
    """Each Semrush feature with its worst-case cost and whether it can run now. Nothing runs without a person pressing it."""
    import semrush_suite as ss, semrush_projects as sp
    budget, key = A.semrush_budget(conn), bool(os.environ.get("SEMRUSH_API_KEY"))
    project = conn.execute("SELECT semrush_project_id FROM clients WHERE client_id=%s", [cid]).fetchone()[0]
    tracked = len(ss.tracked_competitors(conn, cid))
    out = {
        "keywords":  {"label": "Ranking keywords and competitors", "cost": 400, "where": "Competitors"},
        "ideas":     {"label": "Content ideas: questions and related searches", "cost": ss.estimate("ideas", seeds=len(seeds or []) or 3), "where": "Content ideas"},
        "backlinks": {"label": "Backlinks and link prospects", "cost": ss.estimate("backlinks", competitors=min(tracked, 3)), "where": "Competitors"},
        "gap":       {"label": "Keyword gap: what competitors rank for and you don't", "cost": ss.estimate("gap"), "where": "Competitors"},
        "audit":     {"label": "Import Semrush Site Audit", "cost": sp.estimate("audit"), "where": "Technical"},
        "rank":      {"label": "Import Semrush Position Tracking", "cost": sp.estimate("rank"), "where": "Search performance"},
    }
    for k, a in out.items():
        why = None
        if not key: why = "No Semrush key on the server"
        elif k in ("audit", "rank") and not project: why = "Add the Semrush project ID in Settings first"
        elif k == "gap" and not tracked: why = "Track at least one competitor first"
        elif a["cost"] > budget["left"]: why = f"Needs up to {a['cost']:,} credits; the limit leaves {budget['left']:,}"
        a.update(key=k, ready=why is None, why=why)
    return out

@bp.post("/c/<cid>/semrush")
def semrush_run(cid):
    check_csrf()
    from urllib.parse import urlencode
    feature, back = request.form.get("feature"), request.form.get("back") or ""
    if not back.startswith(f"/c/{cid}"): back = url_for("dash.settings", cid=cid, tab="data")     # only back to this brand's screens
    with connect() as conn:
        client_or_404(conn, cid)
        a = semrush_actions(conn, cid).get(feature)
    if not a: abort(400)
    sep = "&" if "?" in back else "?"
    if not a["ready"]: return redirect(back + sep + urlencode({"msg": a["why"]}))
    with connect() as conn:                       # a real pull replaces the sample rows, never mixes with them
        import sample_semrush; sample_semrush.clear(conn, cid, only='semrush')
    if feature == "keywords": start_job(cid, "semrush", max_units=a["cost"])
    else: start_job(cid, f"semrush_{feature}", max_units=a["cost"], seeds=request.form.get("seeds", ""))
    return redirect(back + sep + urlencode({"msg": f"Semrush started: up to {a['cost']:,} credits. Results appear when it finishes."}))

def start_job(cid, only, max_units=None, seeds="", research=""):
    """Runs the daily job for one client in the background. Results go to the run log on screen; full output,
    errors included, is appended to logs/jobs.log in the project folder."""
    import datetime as dt
    logdir = os.path.join(HERE, "..", "logs"); os.makedirs(logdir, exist_ok=True)
    out = open(os.path.join(logdir, "jobs.log"), "a")
    out.write(f"\n=== {dt.datetime.now():%Y-%m-%d %H:%M:%S} {only} for {cid}\n"); out.flush()
    extra = (["--max-units", str(max_units)] if max_units else []) + (["--seeds", seeds] if seeds else []) + (["--research", research] if research else [])
    subprocess.Popen([sys.executable, os.path.join(HERE, "..", "collectors", "daily.py"), "--client-id", cid, "--only", only, "--force-weekly"] + extra,
                     stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


# ------------------------------------------------------------------ Research (Semrush lookups)
TOOLS = {"keyword": ("Keyword research", "keyword"), "domain": ("Domain research", "domain"),
         "backlinks": ("Backlink analytics", "domain"), "paid": ("Paid and shopping", "domain")}

def back_with(url, msg):
    from urllib.parse import urlencode
    return redirect(url + ("&" if "?" in url else "?") + urlencode({"msg": msg}))

@bp.get("/c/<cid>/research/<tool>")
def research(cid, tool):
    if tool not in TOOLS: abort(404)
    import semrush_research as sr, semrush_suite as ss
    with connect() as conn:
        c = client_or_404(conn, cid)
        hist, cur = A.lookups(conn, cid, tool), A.lookup(conn, cid, tool, request.args.get("id"))
        budget = A.semrush_budget(conn)
        comps = ss.tracked_competitors(conn, cid, 6)
        tracked = {r[0] for r in conn.execute("SELECT lower(keyword) FROM rank_keywords WHERE client_id=%s", [cid])}
        ideas = {r[0] for r in conn.execute("SELECT lower(keyword) FROM keyword_ideas WHERE client_id=%s", [cid])}
    cost = sr.estimate(tool)
    why = (None if os.environ.get("SEMRUSH_API_KEY") else "No Semrush key on the server") or \
          (f"Needs up to {cost:,} credits; the limit leaves {budget['left']:,}" if cost > budget["left"] else None)
    chart = None
    if cur and tool == "domain" and cur["result"].get("history"):
        pts = sorted(cur["result"]["history"], key=lambda r: str(r.get("date")))
        chart = line_chart([{"date": __import__("datetime").date.fromisoformat(str(p["date"])[:10]) if str(p.get("date", ""))[:4].isdigit() else None,
                             "traffic": p.get("org_traffic") or 0} for p in pts if p.get("date")], ["traffic"])
    return view("research.html", c, f"r_{tool}", tool=tool, label=TOOLS[tool][0], kind=TOOLS[tool][1], hist=hist, cur=cur, cost=cost, why=why,
                tab=request.args.get("tab"), comps=comps, tracked=tracked, ideas=ideas, chart=chart, msg=request.args.get("msg"))

@bp.post("/c/<cid>/research/<tool>")
def research_run(cid, tool):
    check_csrf()
    if tool not in TOOLS: abort(404)
    import semrush_research as sr
    back = url_for("dash.research", cid=cid, tool=tool, id=request.form.get("lookup") or None, tab=request.form.get("tab") or None)
    with connect() as conn:
        client_or_404(conn, cid)
        kw = (request.form.get("keyword") or "").strip().lower()[:200]
        if request.form.get("do") == "track" and kw:
            import rank
            conn.execute("INSERT INTO rank_keywords (client_id, keyword) VALUES (%s,%s) ON CONFLICT DO NOTHING", [cid, kw])
            rank.sync_client(conn, cid, days=90)
            return back_with(back, f"Tracking “{kw}” in Rankings.")
        if request.form.get("do") == "idea" and kw:
            conn.execute("""INSERT INTO keyword_ideas (client_id, seed, kind, keyword, search_volume, kd, pulled_on, status)
                            VALUES (%s,%s,%s,%s,%s,%s,current_date,'planned') ON CONFLICT (client_id, kind, keyword) DO UPDATE SET status='planned', updated_at=now()""",
                         [cid, request.form.get("seed") or kw, "question" if request.form.get("kind") == "question" else "related", kw,
                          request.form.get("volume") or None, request.form.get("kd") or None])
            return back_with(back, f"“{kw}” added to Content ideas as planned.")
        q = (request.form.get("query") or "").strip()
        if not q: return back_with(back, "Type a keyword or domain first.")
        cost, budget = sr.estimate(tool), A.semrush_budget(conn)
        if not os.environ.get("SEMRUSH_API_KEY"): return back_with(back, "No Semrush key on the server, so nothing was pulled.")
        if cost > budget["left"]: return back_with(back, f"Needs up to {cost:,} credits; the limit leaves {budget['left']:,}. Nothing was pulled.")
    start_job(cid, "research", max_units=cost, research=f"{tool}:{q}")
    return back_with(url_for("dash.research", cid=cid, tool=tool), f"Looking up {q}: up to {cost:,} credits. Refresh in a minute.")

# ------------------------------------------------------------------ Community: Reddit and YouTube
def brand_cols(conn, cid):
    import community
    from ai_visibility import tracked_brands
    return [{"name": b["name"], "label": community.display_name(b), "is_client": b["is_client"]} for b in tracked_brands(conn, cid)]

def topics_of(conn, cid):
    import community
    return community.topics(conn, cid)                    # (topics, found automatically?)

AUTO_KEYS = {"reddit": "APIFY_TOKEN", "youtube": "YOUTUBE_API_KEY"}

def auto_kick(conn, cid, source):
    """Reddit and YouTube fill themselves: when the key is set and the data is missing or a week old, a pull starts in the
    background the moment anyone opens the screen (or the app starts). Not again within an hour of a failure."""
    import datetime as dt
    if not os.environ.get(AUTO_KEYS[source]) or os.environ.get("COMMUNITY_AUTO", "1") == "0": return None
    st = A.source_status(conn, cid, source)
    now = dt.datetime.now(dt.timezone.utc)
    if st["running"] or (st["error_at"] and now - st["error_at"] < dt.timedelta(hours=1)): return st
    if st["last_ok"] and now - st["last_ok"] < dt.timedelta(days=7): return st
    if source == "reddit":
        import reddit as rd
        if rd.items_left(conn) <= 0: return st
    import sample_semrush; sample_semrush.clear(conn, cid, only=source)
    conn.execute("INSERT INTO collector_runs (client_id, collector, status, error) VALUES (%s,%s,'running','started automatically')", [cid, f"{source}_auto"])
    start_job(cid, source)
    st["running"] = now
    return st

@bp.get("/c/<cid>/reddit")
def reddit_view(cid):
    tab = request.args.get("tab", "overview")
    import reddit as rd
    with connect() as conn:
        c = client_or_404(conn, cid)
        cols = brand_cols(conn, cid)
        summ = A.reddit_summary(conn, cid)
        data = {}
        if tab == "overview": data["items"] = A.reddit_items(conn, cid, request.args.get("brand"), request.args.get("sentiment"))
        if tab == "subreddits": data["subs"] = A.subreddit_map(conn, cid, [b["name"] for b in cols])
        if tab == "questions": data["groups"], data["pains"] = A.reddit_questions(conn, cid)
        if tab == "cited": data["cited"] = A.reddit_cited(conn, cid)
        topics, auto = topics_of(conn, cid)
        status = auto_kick(conn, cid, "reddit") or A.source_status(conn, cid, "reddit")
        comms = A.reddit_communities(conn, cid)
        left = rd.items_left(conn)
    labels = {b["name"]: b["label"] for b in cols}
    why = None if os.environ.get("APIFY_TOKEN") else "No Apify token on the server"
    if not why and left <= 0: why = "The Apify result limit is used up"
    return view("reddit.html", c, "reddit", tab=tab, summ=summ, cols=cols, labels=labels, data=data, topics=topics, auto=auto, status=status,
                comms=comms, auto_items=min(rd.AUTO_ITEMS, left), run_items=min(rd.RUN_ITEMS, left), left=left, why=why, msg=request.args.get("msg"),
                brand=request.args.get("brand"), sentiment=request.args.get("sentiment"))

@bp.post("/c/<cid>/reddit")
def reddit_save(cid):
    check_csrf()
    import reddit as rd
    with connect() as conn:
        client_or_404(conn, cid)
        if request.form.get("what") == "topics":
            save_topics(conn, cid, request.form.get("topics", ""))
            return back_with(url_for("dash.reddit_view", cid=cid, tab=request.form.get("tab", "overview")), "Topics saved for Reddit and YouTube." if request.form.get("topics", "").strip() else "Topics are automatic again.")
        if not os.environ.get("APIFY_TOKEN"): return back_with(url_for("dash.reddit_view", cid=cid), "No Apify token on the server, so nothing was pulled.")
        n = min(rd.RUN_ITEMS, rd.items_left(conn))
        if n <= 0: return back_with(url_for("dash.reddit_view", cid=cid), "The Apify result limit is used up.")
        import sample_semrush; sample_semrush.clear(conn, cid, only='reddit')
    start_job(cid, "reddit", max_units=n)
    return back_with(url_for("dash.reddit_view", cid=cid), f"Reddit pull started: up to {n} results. Refresh in a few minutes.")

def save_topics(conn, cid, text):
    """A person's topics replace the automatic ones; saving an empty box goes back to automatic."""
    topics = [t.strip().lower()[:80] for t in text.replace("\n", ",").split(",") if t.strip()][:8]
    conn.execute("UPDATE clients SET community_topics=%s WHERE client_id=%s", [topics, cid])

@bp.get("/c/<cid>/youtube")
def youtube_view(cid):
    tab = request.args.get("tab", "overview")
    import youtube as yt
    with connect() as conn:
        c = client_or_404(conn, cid)
        cols = brand_cols(conn, cid)
        summ = A.youtube_summary(conn, cid)
        data = {}
        if tab == "overview": data["topics"] = A.youtube_topics(conn, cid, [b["name"] for b in cols])
        if tab == "videos": data["videos"] = A.youtube_videos(conn, cid, request.args.get("brand"))
        if tab == "creators": data["creators"] = A.youtube_creators(conn, cid)
        if tab == "comments": data["questions"], data["pains"] = A.youtube_comment_insights(conn, cid)
        if tab == "cited": data["cited"] = A.youtube_cited(conn, cid)
        topics, auto = topics_of(conn, cid)
        status = auto_kick(conn, cid, "youtube") or A.source_status(conn, cid, "youtube")
        channel = conn.execute("SELECT youtube_channel_id FROM clients WHERE client_id=%s", [cid]).fetchone()[0]
        quota = yt.used_today(conn)
    labels = {b["name"]: b["label"] for b in cols}
    why = None if os.environ.get("YOUTUBE_API_KEY") else "No YouTube API key on the server"
    return view("youtube.html", c, "youtube", tab=tab, summ=summ, cols=cols, labels=labels, data=data, topics=topics, auto=auto, status=status,
                channel=channel, quota=quota, daily=yt.DAILY, why=why, msg=request.args.get("msg"), brand=request.args.get("brand"))

@bp.post("/c/<cid>/youtube")
def youtube_save(cid):
    check_csrf()
    with connect() as conn:
        client_or_404(conn, cid)
        what = request.form.get("what")
        if what == "topics":
            save_topics(conn, cid, request.form.get("topics", ""))
            return back_with(url_for("dash.youtube_view", cid=cid), "Topics saved for Reddit and YouTube.")
        if what == "channel":
            ch = "".join(ch for ch in request.form.get("channel", "") if ch.isalnum() or ch in "-_")[:40] or None
            conn.execute("UPDATE clients SET youtube_channel_id=%s WHERE client_id=%s", [ch, cid])
            return back_with(url_for("dash.youtube_view", cid=cid), "Channel saved.")
        if not os.environ.get("YOUTUBE_API_KEY"): return back_with(url_for("dash.youtube_view", cid=cid), "No YouTube API key on the server, so nothing was pulled.")
        import sample_semrush; sample_semrush.clear(conn, cid, only='youtube')
    start_job(cid, "youtube")
    return back_with(url_for("dash.youtube_view", cid=cid), "YouTube pull started (free daily quota). Refresh in a minute or two.")

# ------------------------------------------------------------------ Monthly report (download as PowerPoint)
def report_month(arg):
    import datetime as dt, report
    try: return dt.datetime.strptime(arg, "%Y-%m").date() if arg else report.default_month()
    except ValueError: abort(400)

@bp.get("/c/<cid>/report")
def monthly_report(cid):
    import report, datetime as dt
    month = report_month(request.args.get("month"))
    with connect() as conn:
        c = client_or_404(conn, cid)
        R = report.compute(conn, cid, month)
    months, m = [], report.default_month()
    for _ in range(12): months.append(m); m = report.prev_month(m)
    a, b = report.month_bounds(month)
    full = (b - a).days + 1
    t, g = R["search"]["cur"], R["traffic"]["cur"]
    ready = [("Search Console, " + report.month_name(month), t["days"] if t else 0, full),
             ("Search Console, " + report.month_name(R["prev"]), R["search"]["prev"]["days"] if R["search"]["prev"] else 0,
              (report.month_bounds(R["prev"])[1] - R["prev"]).days + 1),
             ("Google Analytics, " + report.month_name(month), g["days"] if g else 0, full)]
    return view("report.html", c, "report", R=R, month=month, months=months, ready=ready,
                work_text=R["saved"]["work"] if R["saved"] and R["saved"]["work"] else report.work_text(R["work_auto"]),
                plan_text=R["saved"]["plan"] if R["saved"] and R["saved"]["plan"] else report.plan_text(R["plan_auto"]),
                edited=bool(R["saved"] and (R["saved"]["work"] or R["saved"]["plan"])), fmt=report)

@bp.post("/c/<cid>/report")
def monthly_report_save(cid):
    check_csrf()
    import report
    month = report_month(request.form.get("month"))
    with connect() as conn:
        client_or_404(conn, cid)
        if request.form.get("do") == "reset":
            conn.execute("DELETE FROM monthly_reports WHERE client_id=%s AND month=%s", [cid, month])
            msg = "Back to the automatic draft."
        else:
            conn.execute("""INSERT INTO monthly_reports (client_id, month, work_done, plan, updated_by) VALUES (%s,%s,%s,%s,%s)
                            ON CONFLICT (client_id, month) DO UPDATE SET work_done=EXCLUDED.work_done, plan=EXCLUDED.plan,
                            updated_by=EXCLUDED.updated_by, updated_at=now()""",
                         [cid, month, request.form.get("work", "").strip()[:8000] or None, request.form.get("plan", "").strip()[:8000] or None,
                          session.get("staff_email")])
            msg = "Saved. The download uses your text."
    return back_with(url_for("dash.monthly_report", cid=cid, month=month.strftime("%Y-%m")), msg)

@bp.get("/c/<cid>/report/<month>.pptx")
def monthly_report_download(cid, month):
    import report, io, re as _re
    from flask import send_file
    m = report_month(month)
    with connect() as conn:
        c = client_or_404(conn, cid)
        data = report.build(report.compute(conn, cid, m))
    name = _re.sub(r"[^A-Za-z0-9 ._-]", "", f"{c['name']} - SEO Report - {m.strftime('%B %Y')}.pptx")
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation")
