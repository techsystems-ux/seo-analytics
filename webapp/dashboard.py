"""Staff dashboard: Overview, Search performance, Competitors, AI visibility, Pages, Settings. One client at a time.
Registered by app.py. Every screen reads through analytics.py."""
import os, sys, json, secrets, subprocess, csv, io
from flask import Blueprint, render_template, request, redirect, url_for, session, abort
from markupsafe import Markup, escape
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "collectors"), os.path.join(HERE, "..", "seo")]
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
    r = conn.execute("SELECT client_id, name, domain FROM clients WHERE client_id::text=%s", [cid]).fetchone()
    if not r: abort(404)
    return {"id": str(r[0]), "name": r[1], "domain": r[2]}

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
                "revenue": lambda: A.revenue_by_landing(conn, cid)}[tab]()
        rev = A.organic_revenue(conn, cid)
    return view("search.html", c, "search", tab=tab, kind=kind, totals=totals, chart=chart, data=data, rev=rev)

@bp.get("/c/<cid>/competitors")
def competitors(cid):
    with connect() as conn:
        c = client_or_404(conn, cid)
        comps, vis = A.competitors(conn, cid), A.visibility(conn, cid)
        gap_day, gaps = A.keyword_gaps(conn, cid)
        kw_day, kws = A.semrush_keywords(conn, cid)
        budget = A.semrush_budget(conn)
    return view("competitors.html", c, "competitors", comps=comps, vis=vis, gaps=gaps, gap_day=gap_day, kws=kws, kw_day=kw_day, budget=budget,
                tab=request.args.get("tab", "gap"))

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
        if request.form.get("aliases_for"):
            conn.execute("UPDATE competitors SET name=%s, aliases=%s WHERE client_id=%s AND competitor_id::text=%s",
                         [request.form["name"].strip(), [a.strip() for a in request.form.get("aliases", "").split(",") if a.strip()],
                          cid, request.form["aliases_for"]])
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
    keys = {k: bool(os.environ.get(k)) for k in ("OPENAI_API_KEY", "SEMRUSH_API_KEY")}
    return view("settings.html", c, "settings", tab=tab, rules=rules, prompts=prompts, integ=integ, runs=runs, budget=budget, keys=keys,
                msg=request.args.get("msg"))

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
        elif what == "run":
            start_job(cid, request.form.get("only", "gsc,ga4,ai,semrush,pages"))
            return redirect(url_for("dash.settings", cid=cid, tab="data", msg="Pull started. Results appear as each step finishes."))
    return redirect(url_for("dash.settings", cid=cid, tab="prompts"))

def start_job(cid, only):
    """Runs the daily job for one client in the background. Results go to the run log on screen; full output,
    errors included, is appended to logs/jobs.log in the project folder."""
    import datetime as dt
    logdir = os.path.join(HERE, "..", "logs"); os.makedirs(logdir, exist_ok=True)
    out = open(os.path.join(logdir, "jobs.log"), "a")
    out.write(f"\n=== {dt.datetime.now():%Y-%m-%d %H:%M:%S} {only} for {cid}\n"); out.flush()
    subprocess.Popen([sys.executable, os.path.join(HERE, "..", "collectors", "daily.py"), "--client-id", cid, "--only", only, "--force-weekly"],
                     stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
