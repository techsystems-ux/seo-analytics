"""Staff dashboard: Overview, Search performance, Competitors, AI visibility, Pages, Settings. One client at a time.
Registered by app.py. Every screen reads through analytics.py."""
import os, sys, json, secrets, subprocess, csv, io, datetime as dt
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, has_request_context, make_response
from markupsafe import Markup, escape
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "collectors"), os.path.join(HERE, "..", "seo"), os.path.join(HERE, "..", "scripts")]
from common import connect as _staff_connect
import analytics as A
import board as B, viz

# Screens a client may open for their own brand (read-only). Everything else, and every form, is staff only.
VIEWER_SCREENS = {"dash.overview", "dash.search", "dash.competitors", "dash.ai", "dash.ai_traffic", "dash.answer", "dash.reddit_view", "dash.sources",
                  "dash.youtube_view", "dash.monthly_report", "dash.monthly_report_download", "dash.blogs", "dash.blog", "dash.activity_status"}
# What a brand's owner (the person who signed up for it) may also do: review, edit and publish its blog posts, connect its store.
OWNER_ACTIONS = {"dash.blogs_post", "dash.blog_post", "dash.publishing", "dash.publishing_post"}
# What any client user of a brand may do: connect their own ChatGPT plan to it (Integrations).
CLIENT_ACTIONS = {"dash.integrations", "dash.integrations_post"}

def is_owner(cid):
    """The signed-in client is listed as this brand's owner (self-serve sign-up), not just a viewer."""
    if not is_viewer() or cid not in (session.get("viewer_clients") or []): return False
    with _staff_connect() as conn:
        return bool(conn.execute("SELECT 1 FROM client_viewers WHERE client_id::text=%s AND lower(email)=%s AND role='owner'",
                                 [cid, session["viewer_email"].lower()]).fetchone())

def can_edit(cid):
    return bool(session.get("staff_email")) or is_owner(cid)

def ui_mode():
    """One view for everyone: the full (Pro) SEO view. Kept as a function so templates and reports can still ask."""
    return "pro"

def is_viewer():
    return has_request_context() and not session.get("staff_email") and bool(session.get("viewer_email"))

def connect():
    """A staff request sees every brand. A client's request is narrowed in the database itself: not staff, and only the
    brand in the address, so a mistake in any query still cannot show another brand's rows."""
    conn = _staff_connect()
    if is_viewer():
        cid = (request.view_args or {}).get("cid", "")
        if cid not in (session.get("viewer_clients") or []): cid = ""      # not theirs: the session sees no brand at all
        conn.execute("SELECT set_config('app.is_staff', 'false', false), set_config('app.client_id', %s, false)", [cid])
    return conn

bp = Blueprint("dash", __name__)
STAGES = {"awareness": "Discovery", "consideration": "Research", "decision": "Decision"}

def staff_required():
    if not session.get("staff_email"): abort(redirect(url_for("login")))

def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]

def check_csrf():
    if not request.form.get("csrf") or request.form.get("csrf") != session.get("csrf"): abort(400, "Form expired. Reload the page and try again.")

_TAG = __import__("re").compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

def strip_staff_only(html):
    """Remove every element marked class="staff-only" (with all it contains) from a page, so a client's browser never
    receives team-only content, not even hidden by CSS. Script and style bodies are skipped while matching tags."""
    import re
    out, pos, i = [], 0, 0
    skip = re.compile(r"(?is)<(script|style)\b.*?</\1>")
    spans = [(m.start(), m.end()) for m in skip.finditer(html)]
    def in_skip(k): return any(a <= k < b for a, b in spans)
    for m in _TAG.finditer(html):
        if m.start() < pos or in_skip(m.start()) or m.group(1): continue
        cls = re.search(r"""class\s*=\s*["']([^"']*)["']""", m.group(3))
        if not cls or "staff-only" not in cls.group(1).split(): continue
        name = m.group(2).lower()
        if name in _VOID or m.group(3).rstrip().endswith("/"):
            out.append(html[pos:m.start()]); pos = m.end(); continue
        depth, end = 1, len(html)
        for n in _TAG.finditer(html, m.end()):
            if n.group(2).lower() != name or in_skip(n.start()): continue
            if n.group(1): depth -= 1
            elif not n.group(3).rstrip().endswith("/"): depth += 1
            if depth == 0: end = n.end(); break
        out.append(html[pos:m.start()]); pos = end
    out.append(html[pos:])
    return "".join(out)

@bp.after_request
def _client_view(resp):
    if is_viewer() and resp.mimetype == "text/html" and not resp.direct_passthrough:
        resp.set_data(strip_staff_only(resp.get_data(as_text=True)))
    return resp

@bp.before_request
def _gate():
    if session.get("staff_email"): return
    if session.get("viewer_email"):
        cid = (request.view_args or {}).get("cid")
        mine = cid in (session.get("viewer_clients") or [])
        if mine and ((request.method == "GET" and request.endpoint in VIEWER_SCREENS) or request.endpoint in CLIENT_ACTIONS
                     or (request.endpoint in OWNER_ACTIONS and is_owner(cid))):
            with _staff_connect() as conn:
                ends = conn.execute("SELECT trial_ends FROM clients WHERE client_id=%s", [cid]).fetchone()
            if ends and ends[0] and ends[0] < __import__("datetime").date.today():
                abort(make_response(render_template("trial_ended.html", ends=ends[0], csrf=csrf()), 402))
            return
        abort(redirect(url_for("portal.home")))
    staff_required()

def client_or_404(conn, cid):
    r = conn.execute("SELECT client_id, name, domain, sample_semrush FROM clients WHERE client_id::text=%s", [cid]).fetchone()
    if not r: abort(404)
    return {"id": str(r[0]), "name": r[1], "domain": r[2], "sample": r[3]}

_INTEL = {}

def intel_for(cid):
    """Sidebar and top-bar facts for a brand (latest data date, what the tool reads), cached for ten minutes."""
    import time
    hit = _INTEL.get(cid)
    if hit and time.time() - hit[0] < 600: return hit[1]
    try:
        with connect() as conn:
            r = conn.execute("""SELECT (SELECT max(date) FROM gsc_search_daily WHERE client_id=%(c)s),
                                       (SELECT count(DISTINCT query) FROM gsc_search_daily WHERE client_id=%(c)s
                                          AND date > (SELECT max(date) - 7 FROM gsc_search_daily WHERE client_id=%(c)s)),
                                       (SELECT count(*) FROM pages WHERE client_id=%(c)s),
                                       (SELECT count(*) FROM prompts WHERE client_id=%(c)s AND approved),
                                       (SELECT count(*) FROM rank_keywords WHERE client_id=%(c)s),
                                       (SELECT max(finished_at) FROM collector_runs WHERE client_id=%(c)s AND status='ok')""", {"c": cid}).fetchone()
        out = {"data_to": r[0], "queries": r[1] or 0, "pages": r[2] or 0, "prompts": r[3] or 0, "keywords": r[4] or 0, "updated": r[5]}
    except Exception:
        out = {"data_to": None, "queries": 0, "pages": 0, "prompts": 0, "keywords": 0, "updated": None}
    _INTEL[cid] = (time.time(), out)
    return out

def nudge_worker():
    """Safety net: when tasks are waiting and none has started for three minutes (a follow-on run was lost), start one."""
    if not ONLINE or not session.get("staff_email"): return
    try:
        with _staff_connect() as conn:
            stuck = conn.execute("""SELECT EXISTS (SELECT 1 FROM pull_tasks WHERE status='queued')
                                    AND NOT EXISTS (SELECT 1 FROM pull_tasks WHERE started_at > now() - interval '3 minutes')""").fetchone()[0]
        if stuck:
            import worker; worker.kick()
    except Exception:
        pass

def keep_fresh(client):
    """Opening a brand: if its free data is missing or stale, refresh it now (never the paid sources, see worker.ensure_fresh)."""
    if not ONLINE or not client: return
    try:
        import worker
        with _staff_connect() as conn:
            started = worker.ensure_fresh(conn, client["id"], session.get("staff_email") or session.get("viewer_email"))
        if started: worker.kick()
    except Exception:
        pass

def view(tpl, client=None, active=None, **ctx):
    nudge_worker()
    keep_fresh(client)
    info = intel_for(client["id"]) if client else None
    pro = ui_mode() == "pro"
    return render_template(tpl, client=client, active=active, staff=session.get("staff_email"), csrf=csrf(), stages=STAGES,
                           pro=pro, t=lambda simple, technical: technical if pro else simple,
                           viewer=session.get("viewer_email") if is_viewer() else None, viewer_brands=len(session.get("viewer_clients") or []),
                           owner=is_owner(client["id"]) if client and is_viewer() else False,
                           intel=info, data_to=info["data_to"] if info else None, **ctx)

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

def line_chart(points, series, colors=("#B20F00", "#0F0F0F"), height=170, ymax=None):
    """points = [{date, s1, s2}] -> the shared smooth SVG chart (webapp/viz.py), one line per series."""
    if len(points) < 2: return Markup('<p class="mute">Not enough data yet for a chart.</p>')
    labels = [p["date"].strftime("%d %b") if p.get("date") else "" for p in points]
    return viz.lines(labels, [{"name": s, "colour": colors[k % len(colors)], "values": [float(p[s] or 0) for p in points]}
                              for k, s in enumerate(series)], height=height + 40, ymax=ymax, area_first=len(series) == 1)

# ------------------------------------------------------------------ screens
@bp.get("/c")
def clients():
    with connect() as conn:
        rows = conn.execute("""SELECT c.client_id, c.name, c.domain,
                                 (SELECT visibility_score FROM scores_daily s WHERE s.client_id=c.client_id AND s.brand='' ORDER BY date DESC LIMIT 1),
                                 (SELECT max(date) FROM gsc_search_daily g WHERE g.client_id=c.client_id),
                                 (SELECT count(*) FROM prompts p WHERE p.client_id=c.client_id AND p.approved)
                               FROM clients c WHERE c.status='active' ORDER BY c.name""").fetchall()
        demos = conn.execute("""SELECT created_at, name, email, company, website, message, source FROM demo_requests
                                ORDER BY created_at DESC LIMIT 20""").fetchall()
        trials = conn.execute("""SELECT client_id, name, domain, signup_email, trial_ends FROM clients WHERE signup_email IS NOT NULL
                                 ORDER BY trial_ends DESC LIMIT 20""").fetchall()
    return view("clients.html", clients=[{"id": str(r[0]), "name": r[1], "domain": r[2], "score": r[3], "band": A.band(r[3]),
                                          "gsc_to": r[4], "prompts": r[5]} for r in rows], demos=demos, trials=trials)

def conn_trial(cid):
    with _staff_connect() as conn:
        r = conn.execute("SELECT trial_ends FROM clients WHERE client_id=%s", [cid]).fetchone()
    return r[0] if r else None

@bp.app_context_processor
def _viz_helpers():
    return {"ring": viz.ring, "gauge": viz.gauge, "engine_label": lambda k: B.LABEL.get(k, k)}

def _seo_by(conn, cid, ents):
    """Google footprint per entity: competitors from Semrush (keywords, traffic, shared keywords); the brand from its own
    Search Console queries and GA4 organic sessions, 28 days."""
    sem = {}
    for d, kw, tr, cm in conn.execute("""SELECT lower(replace(domain,'www.','')), max(organic_keywords), max(organic_traffic), max(common_keywords)
                                         FROM competitors WHERE client_id=%s AND domain IS NOT NULL GROUP BY 1""", [cid]):
        sem[d] = {"keywords": kw, "traffic": tr, "common": cm}
    out = {}
    for e in ents:
        if e["primary"]:
            q, ses = conn.execute("""SELECT (SELECT count(DISTINCT query) FROM gsc_search_daily WHERE client_id=%(c)s AND date > current_date - 28),
                                            (SELECT sum(sessions) FROM ga4_daily WHERE client_id=%(c)s AND channel_group='Organic Search'
                                             AND date > current_date - 28)""", {"c": cid}).fetchone()
            out[""] = {"keywords": q, "traffic": ses, "common": None}
        elif e["domain"]:
            out[e["key"]] = sem.get(e["domain"].lower().replace("www.", ""))
    return out

@bp.get("/c/<cid>")
def overview(cid):
    tab = request.args.get("tab", "dashboard")
    with connect() as conn:
        c = client_or_404(conn, cid)
        ents = B.entities(conn, cid)
        focus = B.pick(ents, request.args.get("b"))
        board, engs = B.ai_board(conn, cid, ents), B.engines(conn, cid)
        pm = B.prompt_matrix(conn, cid, focus["key"], ents, engs)
        aio = B.aio_keywords(conn, cid)
        seo_by = _seo_by(conn, cid, ents)
        vis, search, rev = A.visibility(conn, cid), A.search_totals(conn, cid), A.organic_revenue(conn, cid)
        last, gaps = A.keyword_gaps(conn, cid, 6)
        chart = line_chart(A.daily_clicks(conn, cid, 90), ["nonbranded", "branded"])
        runs = A.last_runs(conn, cid)
        kb, pb = A.keyword_board(conn, cid), A.prompt_board(conn, cid)
    labels = [w.strftime("%d %b") for w in board["weeks"]]
    trend = viz.lines(labels, [{"name": e["name"], "colour": e["colour"], "values": e["trend"]} for e in board["ents"]], ymax=100)
    scored = [e for e in board["ents"] if e["score"] is not None]
    vis_cols = viz.columns([e["name"] if len(e["name"]) <= 12 else e["name"][:11] + "…" for e in scored],
                           [{"name": "Visibility", "colour": "#2F5BEA", "values": [e["score"] for e in scored]}], ymax=100)
    sov_parts = [{"name": e["name"], "value": e["sov"] or 0, "colour": e["colour"]} for e in board["ents"]]
    sov_donut = viz.donut(sov_parts, centre=f"{board['answers']:,}", sub="answers read")
    return view("overview.html", c, "overview", tab=tab, ents=ents, focus=focus, board=board, engs=engs, pm=pm, aio=aio,
                search_ov=B.search_overview(kb, aio), comp_seo=seo_by.get(focus["key"]) if not focus["primary"] else None, seo_by=seo_by,
                trend=trend, vis_cols=vis_cols, sov_donut=sov_donut,
                ranked=sorted(board["ents"], key=lambda e: -(e["score"] if e["score"] is not None else -1)),
                vis=vis, search=search, rev=rev, gaps=gaps, chart=chart, runs=runs,
                kb=kb, pb=pb, news=A.headlines(kb, pb), welcome=request.args.get("welcome"), trial=conn_trial(cid))

@bp.get("/c/<cid>/sources")
def sources(cid):
    import niches as N
    with connect() as conn:
        c = client_or_404(conn, cid)
        niche = conn.execute("SELECT niche FROM clients WHERE client_id=%s", [cid]).fetchone()[0]
        guessed = None
        if not niche:
            guessed = N.guess(" ".join([c["name"], c["domain"] or ""]))
        S = A.source_presence(conn, cid, niche or guessed)
        rd, yt = A.reddit_summary(conn, cid), A.youtube_summary(conn, cid)
        cs, engs = B.citation_sov(conn, cid), B.engines(conn, cid)
    weeks = sorted({w["week"] for w in cs["weekly"]})
    wk = {(w["week"], w["engine"]): (100 * w["cited"] / w["answers"] if w["answers"] else None) for w in cs["weekly"]}
    cov = viz.lines([w.strftime("%d %b") for w in weeks],
                    [{"name": e["label"], "colour": viz.color(i), "values": [wk.get((w, e["key"])) for w in weeks]}
                     for i, e in enumerate([e for e in engs if e["tracked"]])], ymax=100, fmt="{:.0f}%")
    def community(sm, unit):
        if not sm: return None
        you = next((b for b in sm["brands"] if b["is_client"]), None)
        top = next((b for b in sm["brands"] if not b["is_client"]), None)
        return {"you": you[unit] if you else 0, "top": (top["brand"], top[unit]) if top else None, "unit": unit}
    extra = {"reddit.com": community(rd, "mentions"), "youtube.com": community(yt, "videos")}
    return view("sources.html", c, "sources", S=S, niche=niche, guessed=guessed, niche_label=N.label(niche or guessed),
                choices=N.choices(), kinds=N.KIND_LABEL, extra=extra, msg=request.args.get("msg"), cs=cs, engs=engs, cov=cov)

@bp.post("/c/<cid>/sources")
def sources_save(cid):
    check_csrf()
    import niches as N, re
    what = request.form.get("what")
    with connect() as conn:
        client_or_404(conn, cid)
        if what == "niche":
            v = request.form.get("niche") or None
            conn.execute("UPDATE clients SET niche=%s WHERE client_id=%s", [v if v in N.NICHES else None, cid])
            msg = "Niche set to " + (N.label(v) or "none") + ". The source list below follows it."
        elif what in ("status", "add", "hide"):
            d = (request.form.get("domain") or "").strip().lower()
            d = re.sub(r"^https?://", "", d).removeprefix("www.").split("/")[0]
            if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d):
                return redirect(url_for("dash.sources", cid=cid, msg="That is not a website address."))
            status = {"add": "unknown", "hide": "hidden"}.get(what, request.form.get("status") or "unknown")
            if status not in ("unknown", "listed", "in_progress", "not_listed", "hidden"): status = "unknown"
            conn.execute("""INSERT INTO client_sources (client_id, domain, name, kind, status, listing_url, updated_by)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (client_id, domain) DO UPDATE SET status=EXCLUDED.status,
                              listing_url=coalesce(EXCLUDED.listing_url, client_sources.listing_url), updated_by=EXCLUDED.updated_by, updated_at=now()""",
                         [cid, d, request.form.get("name") or None, "custom" if what == "add" else None, status,
                          (request.form.get("listing_url") or "").strip() or None, session.get("staff_email")])
            msg = {"add": d + " added.", "hide": d + " hidden from this brand's list."}.get(what, "Listing status saved for " + d + ".")
        else:
            abort(400)
    return redirect(url_for("dash.sources", cid=cid, msg=msg))

@bp.get("/c/<cid>/search")
def search(cid):
    tab = request.args.get("tab", "queries"); kind = request.args.get("kind", "nonbranded")
    from date_ranges import select_range
    if tab not in ('queries', 'pages', 'countries', 'devices', 'revenue', 'rankings', 'indexing', 'sitemaps', 'vitals'): tab = 'queries'
    if kind not in ('all', 'branded', 'nonbranded'): kind = 'nonbranded'
    state = request.args.get("state")
    with connect() as conn:
        c = client_or_404(conn, cid)
        latest = A.periods(conn, cid)[0][1]
        selected, date_error = select_range(request.args, latest)
        # the preset the selected dates match (also after a tab change, which carries start/end), else None = custom dates
        preset = next((n for n in (7, 28, 90) if selected == (latest - dt.timedelta(days=n - 1), latest)), None)
        date_args = {'date_range': selected}
        totals = A.search_totals(conn, cid, **date_args)
        chart = line_chart(A.daily_clicks(conn, cid, **date_args), ["nonbranded", "branded"])
        data = {"queries": lambda: A.top_queries(conn, cid, {"all": None, "branded": True, "nonbranded": False}[kind], **date_args),
                "pages": lambda: A.top_pages(conn, cid, **date_args),
                "countries": lambda: A.search_by(conn, cid, "country", **date_args),
                "devices": lambda: A.search_by(conn, cid, "device", **date_args),
                "revenue": lambda: A.revenue_by_landing(conn, cid, **date_args),
                "rankings": lambda: A.rankings(conn, cid, **date_args),
                "indexing": lambda: A.index_status(conn, cid, state),
                "sitemaps": lambda: A.sitemaps(conn, cid),
                "vitals": lambda: A.vitals(conn, cid)}[tab]()
        rev = A.organic_revenue(conn, cid, **date_args)
        kw = request.args.get("kw")
        trend = line_chart([{"date": t["date"], "pos": t["position"]} for t in A.rank_trend(conn, cid, kw, **date_args)], ["pos"]) if kw and tab == "rankings" else None
        actions = semrush_actions(conn, cid) if tab == "rankings" else {}
    def search_link(**changes):
        values = dict(tab=tab, kind=kind, start=selected[0].isoformat(), end=selected[1].isoformat())
        values.update(changes)
        return url_for('dash.search', cid=cid, **values)
    return view("search.html", c, "search", tab=tab, kind=kind, totals=totals, chart=chart, data=data, rev=rev, kw=kw, trend=trend, actions=actions, selected=selected, date_error=date_error, latest=latest, range_days=(selected[1]-selected[0]).days+1, search_link=search_link, preset=preset, state=state)

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
        ents = B.entities(conn, cid)
        focus = B.pick(ents, request.args.get("b"))
        board, engs = B.ai_board(conn, cid, ents), B.engines(conn, cid)
        pm = B.prompt_matrix(conn, cid, focus["key"], ents, engs)
    if stage != "all": prompts = [p for p in prompts if p["stage"] == stage]
    by_id = {str(p["prompt_id"]): p for p in prompts}
    rows = [dict(r, base=by_id[r["id"]]) for r in pm["rows"] if r["id"] in by_id]
    me = next(e for e in board["ents"] if e["key"] == focus["key"])
    trend = viz.lines([w.strftime("%d %b") for w in board["weeks"]], [{"name": me["name"], "colour": me["colour"], "values": me["trend"]}], ymax=100)
    return view("ai.html", c, "ai", vis=vis, prompts=prompts, cycle=cycle, cited=cited, others=others, stage=stage, trend=trend,
                ents=ents, focus=focus, board=board, engs=engs, pm=pm, rows=rows, me=me)

@bp.get("/c/<cid>/ai-traffic")
def ai_traffic(cid):
    days = request.args.get("days", "28")
    days = int(days) if days in ("7", "28", "90") else 28
    with connect() as conn:
        c = client_or_404(conn, cid)
        tr, engs = B.ai_traffic(conn, cid, days), B.engines(conn, cid)
    labels = [w.strftime("%d %b") for w in tr["weeks"]]
    ai_chart = viz.lines(labels, [{"name": "AI sessions", "colour": "#B20F00", "values": tr["ai_weekly"]}])
    org_chart = viz.lines(labels, [{"name": "Organic sessions", "colour": "#16A34A", "values": tr["org_weekly"]}])
    src_cols = viz.columns([s["label"] for s in tr["sources"]], [{"name": "Sessions", "colour": "#2F5BEA", "values": [s["sessions"] for s in tr["sources"]]}])
    return view("ai_traffic.html", c, "ai_traffic", tr=tr, engs=engs, days=days, ai_chart=ai_chart, org_chart=org_chart, src_cols=src_cols)

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
        viewers = conn.execute("SELECT email, added_by, added_at, last_login FROM client_viewers WHERE client_id=%s ORDER BY email", [cid]).fetchall()
    keys = {k: bool(os.environ.get(k)) for k in ("OPENAI_API_KEY_2", "SEMRUSH_API_KEY", "APIFY_TOKEN", "YOUTUBE_API_KEY")}
    login = {"google": bool(os.environ.get("CLIENT_OAUTH_CLIENT_ID")), "email": bool(os.environ.get("RESEND_API_KEY"))}
    return view("settings.html", c, "settings", paid_per_day=int(os.environ.get("PAID_RUNS_PER_DAY", "1")), semrush_auto=int(os.environ.get("SEMRUSH_AUTO_UNITS", "400")), tab=tab, rules=rules, prompts=prompts, integ=integ, runs=runs, budget=budget, keys=keys,
                msg=request.args.get("msg"), project=project, actions=actions, by_feature=by_feature, viewers=viewers, login=login,
                portal_url=request.host_url.rstrip("/") + "/portal")

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
        elif what in ("viewer_add", "viewer_remove"):
            import re
            emails = [e.strip().lower() for e in re.split(r"[,;\s]+", request.form.get("emails", "")) if e.strip()]
            bad = [e for e in emails if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e)]
            if bad: return redirect(url_for("dash.settings", cid=cid, tab="access", msg="Not an email address: " + ", ".join(bad[:3])))
            for e in emails:
                if what == "viewer_add":
                    conn.execute("INSERT INTO client_viewers (client_id, email, added_by) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                                 [cid, e, session.get("staff_email")])
                else:
                    conn.execute("DELETE FROM client_viewers WHERE client_id=%s AND email=%s", [cid, e])
            msg = (f"{len(emails)} added. Share the client sign-in address with them." if what == "viewer_add" else "Access removed.") if emails else "No email given."
            return redirect(url_for("dash.settings", cid=cid, tab="access", msg=msg))
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
        tix = A.tech_index(conn, cid)
        checklist = A.tech_checklist(conn, cid)
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in ("error", "warning", "notice")}
    return view("technical.html", c, "technical", summary=summary, issues=issues, show=show, code=code, pages=pages, sel=sel,
                counts=counts, actions=actions, today=__import__("datetime").date.today(), tix=tix, checklist=checklist)

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
    import worker
    tracked = len(ss.tracked_competitors(conn, cid))
    last = conn.execute("SELECT max(called_at) FROM semrush_usage WHERE client_id=%s AND coalesce(feature,'keywords')='keywords' AND units > 0", [cid]).fetchone()[0]
    recent = conn.execute("""SELECT 1 FROM paid_runs WHERE client_id=%s AND kind='semrush'
                             AND started_at > now() - make_interval(mins => %s)""", [cid, worker.MANUAL_GAP_MIN]).fetchone()
    out = {
        "keywords":  {"label": "Refresh keyword data now", "cost": int(os.environ.get("SEMRUSH_AUTO_UNITS", "400")) or 400, "where": "Competitors"},
        "ideas":     {"label": "Content ideas: questions and related searches", "cost": ss.estimate("ideas", seeds=len(seeds or []) or 3), "where": "Content ideas"},
        "backlinks": {"label": "Backlinks and link prospects", "cost": ss.estimate("backlinks", competitors=min(tracked, 3)), "where": "Competitors"},
        "gap":       {"label": "Keyword gap: what competitors rank for and you don't", "cost": ss.estimate("gap"), "where": "Competitors"},
        "audit":     {"label": "Import Semrush Site Audit", "cost": sp.estimate("audit"), "where": "Technical"},
        "rank":      {"label": "Import Semrush Position Tracking", "cost": sp.estimate("rank"), "where": "Search performance"},
    }
    for k, a in out.items():
        why = None
        if not key: why = "Keyword data is not connected yet"
        elif k in ("audit", "rank") and not project: why = "Add the Semrush project ID in Settings first"
        elif k == "gap" and not tracked: why = "Track at least one competitor first"
        elif a["cost"] > budget["left"]: why = "The monthly data allowance is used up"
        elif k == "keywords" and recent: why = f"Refreshed under {worker.MANUAL_GAP_MIN} minutes ago; try again shortly"
        a.update(key=k, ready=why is None, why=why)
    out["keywords"]["last"] = last
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
    if feature == "keywords": start_job(cid, "semrush", max_units=a["cost"], manual=True)   # the refresh button: outside the daily limit
    else: start_job(cid, f"semrush_{feature}", max_units=a["cost"], seeds=request.form.get("seeds", ""))
    return redirect(back + sep + urlencode({"msg": "Started. Results appear here in a few minutes."}))

ONLINE = bool(os.environ.get("VERCEL"))     # the online copy on Vercel: a request cannot outlive its response
QUEUED = "Started. Results appear here as each step finishes, usually within a few minutes."

def queue_pull(cid, label, args):
    """Online, a pull is split into tasks (collectors/worker.py) and the server starts working through them at once."""
    import json
    from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
    import worker
    with connect() as conn:
        pull_id = worker.enqueue(conn, cid, label, args, session.get("staff_email") or session.get("viewer_email"))
    worker.kick()
    back = (request.referrer or url_for("dash.clients")).split("#")[0]
    parts = urlsplit(back)
    query = dict(parse_qsl(parts.query)); query.update(msg=QUEUED, activity=pull_id)
    abort(redirect(urlunsplit(parts._replace(query=urlencode(query)))))

@bp.get("/c/<cid>/activity/<int:pull_id>")
def activity_status(cid, pull_id):
    with connect() as conn:
        client_or_404(conn, cid)
        row = conn.execute("SELECT finished_at, exit_code FROM pull_queue WHERE pull_id=%s AND client_id::text=%s", [pull_id, cid]).fetchone()
        if not row: abort(404)
        tasks = conn.execute("SELECT step, status, note FROM pull_tasks WHERE pull_id=%s AND client_id::text=%s ORDER BY seq, task_id", [pull_id, cid]).fetchall()
    labels = {"blogs": "Drafting blog", "ai": "Checking AI answers", "gsc": "Fetching Google Search data", "gsc_history": "Fetching search history", "ga4": "Fetching Analytics", "rank": "Updating rankings", "crawl": "Scanning pages", "index": "Checking indexing", "cwv": "Measuring page speed", "reddit": "Fetching Reddit", "youtube": "Fetching YouTube"}
    items = [{"label": labels.get(step, "Fetching research data"), "state": "skipped" if status == "done" and (note or "").startswith("skipped:") else status} for step, status, note in tasks]
    state = "error" if row[0] and row[1] else "finished" if row[0] else "running" if any(t["state"] == "running" for t in items) else "queued"
    response = make_response({"state": state, "tasks": items})
    response.headers["Cache-Control"] = "no-store"
    return response


def start_job(cid, only, max_units=None, seeds="", research="", blog_count=None, manual=False):
    """Runs the daily job for one client in the background. Results go to the run log on screen; full output,
    errors included, is appended to logs/jobs.log in the project folder."""
    import datetime as dt
    extra = (["--max-units", str(max_units)] if max_units else []) + (["--seeds", seeds] if seeds else []) + (["--research", research] if research else []) \
        + (["--manual", "1"] if manual else [])
    if blog_count is not None:
        import worker
        extra += ["--blog-count", str(worker.blog_count(blog_count))]
    if ONLINE: queue_pull(cid, only, ["--client-id", cid, "--only", only, "--force-weekly"] + extra)
    logdir = os.path.join(HERE, "..", "logs"); os.makedirs(logdir, exist_ok=True)
    out = open(os.path.join(logdir, "jobs.log"), "a")
    out.write(f"\n=== {dt.datetime.now():%Y-%m-%d %H:%M:%S} {only} for {cid}\n"); out.flush()
    run_daily(["--client-id", cid, "--only", only, "--force-weekly"] + extra, out)

JOBS = []          # background pulls started by this server process

def jobs_running():
    JOBS[:] = [p for p in JOBS if p.poll() is None]
    return len(JOBS)

def run_daily(args, out=None):
    """Start collectors/daily.py in the background. On a host that sleeps when idle (Render's free plan), the server
    pings its own public address every 4 minutes while a pull runs, so the pull is not cut off half way."""
    if out is None:
        logdir = os.path.join(HERE, "..", "logs"); os.makedirs(logdir, exist_ok=True)
        out = open(os.path.join(logdir, "jobs.log"), "a")
    JOBS.append(subprocess.Popen([sys.executable, os.path.join(HERE, "..", "collectors", "daily.py")] + args,
                                 stdout=out, stderr=subprocess.STDOUT, start_new_session=True))
    _keep_awake()

_AWAKE = {"on": False}
def _keep_awake():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url or _AWAKE["on"]: return
    import threading, time, urllib.request
    def loop():
        try:
            while jobs_running():
                time.sleep(240)
                try: urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=20).read()
                except Exception: pass
        finally: _AWAKE["on"] = False
    _AWAKE["on"] = True; threading.Thread(target=loop, daemon=True).start()


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
    why = (None if os.environ.get("SEMRUSH_API_KEY") else "Keyword data is not connected yet") or \
          ("The data allowance is used up" if cost > budget["left"] else None)
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
        if not os.environ.get("SEMRUSH_API_KEY"): return back_with(back, "Keyword data is not connected yet, so nothing was pulled.")
        if cost > budget["left"]: return back_with(back, "The data allowance is used up, so nothing was looked up.")
    start_job(cid, "research", max_units=cost, research=f"{tool}:{q}")
    return back_with(url_for("dash.research", cid=cid, tool=tool), f"Looking up {q}. Refresh in a minute.")

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
    if ONLINE or is_viewer() or not os.environ.get(AUTO_KEYS[source]) or os.environ.get("COMMUNITY_AUTO", "1") == "0": return None
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
    why = None if ONLINE or os.environ.get("APIFY_TOKEN") else "Reddit is not connected yet"
    if not why and left <= 0: why = "The Reddit data allowance is used up"
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
        if not ONLINE and not os.environ.get("APIFY_TOKEN"): return back_with(url_for("dash.reddit_view", cid=cid), "Reddit is not connected yet, so nothing was pulled.")
        n = min(rd.RUN_ITEMS, rd.items_left(conn))
        if n <= 0: return back_with(url_for("dash.reddit_view", cid=cid), "The Reddit data allowance is used up.")
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
    why = None if ONLINE or os.environ.get("YOUTUBE_API_KEY") else "YouTube is not connected yet"
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
        if not ONLINE and not os.environ.get("YOUTUBE_API_KEY"): return back_with(url_for("dash.youtube_view", cid=cid), "YouTube is not connected yet, so nothing was pulled.")
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

import execution  # noqa: E402,F401  Execution screens (blog posts, publishing) on this blueprint
import integrations  # noqa: E402,F401  Integrations: Continue with ChatGPT
