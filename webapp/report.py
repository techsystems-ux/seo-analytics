"""Monthly SEO report for a client, in the layout of the Hovers monthly deck (cover, contents, keyword rankings,
search performance, organic traffic, organic revenue, branded vs non-branded, top URLs, work done, plan of action).

compute() reads only data already stored (Search Console, GA4, the work tracked in the tool), so a download costs
nothing. Work done and plan of action are drafted from the tool's own records; an analyst's edits (monthly_reports)
replace the drafts. build() writes the .pptx with python-pptx: native tables and a native chart, Arial throughout.
"""
import calendar, datetime as dt, io, re
from urllib.parse import urlparse

CTR_CURVE = {1: .28, 2: .15, 3: .11, 4: .08, 5: .07, 6: .05, 7: .04, 8: .03, 9: .025, 10: .02}

# ------------------------------------------------------------------ formatting
def n(v):
    return "–" if v is None else f"{v:,.0f}"

def inr(v):
    """Indian grouping: 1139819 -> ₹11,39,819."""
    if v is None: return "–"
    s = f"{abs(round(v)):d}"
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = head + "," + tail
    return ("-" if v < 0 else "") + "₹" + s

def pct(v, d=0):
    return "–" if v is None else f"{v * 100:.{d}f}%"

def change(cur, prev):
    if cur is None or prev in (None, 0): return None
    return (cur - prev) / prev

def chg_txt(c):
    return "" if c is None else f"{'+' if c >= 0 else ''}{c * 100:.0f}%"

def month_name(d): return d.strftime("%B")

def month_bounds(m):
    return m, m.replace(day=calendar.monthrange(m.year, m.month)[1])

def prev_month(m): return (m - dt.timedelta(days=1)).replace(day=1)

def path_of(url):
    if not url: return ""
    p = urlparse(url if "://" in url else "https://x" + (url if url.startswith("/") else "/" + url))
    path = p.path.rstrip("/") or "/"
    return "Home page" if path == "/" else path

def short(s, k=34):
    return s if len(s) <= k else s[:k // 2 - 1] + "..." + s[-(k // 2 - 2):]

# ------------------------------------------------------------------ data
def gsc_totals(conn, cid, a, b):
    r = conn.execute("""SELECT count(*), sum(clicks), sum(impressions), sum(position * impressions) / nullif(sum(impressions), 0)
                        FROM gsc_totals_daily WHERE client_id=%s AND date BETWEEN %s AND %s""", [cid, a, b]).fetchone()
    if not r[0]: return None
    return {"days": r[0], "clicks": r[1] or 0, "impressions": r[2] or 0, "ctr": (r[1] or 0) / r[2] if r[2] else None,
            "position": float(r[3]) if r[3] is not None else None}

def query_positions(conn, cid, a, b, min_impr=10):
    return {q: (int(i), int(c), float(p)) for q, i, c, p in conn.execute(
        """SELECT lower(query), sum(impressions), sum(clicks), sum(position * impressions) / nullif(sum(impressions), 0)
           FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s GROUP BY 1 HAVING sum(impressions) >= %s""",
        [cid, a, b, min_impr]) if p is not None}

def ranking_stats(qp):
    top3 = sum(1 for i, c, p in qp.values() if p <= 3)
    top10 = sum(1 for i, c, p in qp.values() if p <= 10)
    ideal = sum(i for i, c, p in qp.values()) * CTR_CURVE[1]
    got = sum(i * CTR_CURVE.get(int(round(p)), .01 if p <= 20 else 0) for i, c, p in qp.values())
    return {"top3": top3, "top4_10": top10 - top3, "top10": top10, "visibility": got / ideal if ideal else None, "keywords": len(qp)}

def ga4_month(conn, cid, a, b):
    r = conn.execute("""SELECT count(DISTINCT date),
                               sum(sessions) FILTER (WHERE channel_group='Organic Search'), sum(new_users) FILTER (WHERE channel_group='Organic Search'),
                               sum(engaged_sessions) FILTER (WHERE channel_group='Organic Search'), sum(key_events) FILTER (WHERE channel_group='Organic Search'),
                               sum(transactions) FILTER (WHERE channel_group='Organic Search'), sum(revenue) FILTER (WHERE channel_group='Organic Search'),
                               sum(sessions), count(new_users) FILTER (WHERE channel_group='Organic Search')
                        FROM ga4_daily WHERE client_id=%s AND date BETWEEN %s AND %s""", [cid, a, b]).fetchone()
    if not r[0]: return None
    s = r[1] or 0
    return {"days": r[0], "sessions": s, "new_users": r[2] if r[8] else None, "engagement": (r[3] or 0) / s if s else None,
            "key_events": float(r[4] or 0), "transactions": r[5] or 0, "revenue": float(r[6] or 0), "share": s / r[7] if r[7] else None}

def landing(conn, cid, a, b):
    out = {}
    for page, s, k, rev in conn.execute("""SELECT landing_page, sum(sessions), sum(key_events), sum(revenue) FROM ga4_organic_landing
                                           WHERE client_id=%s AND date BETWEEN %s AND %s GROUP BY 1""", [cid, a, b]):
        p = path_of(page.split("?")[0])
        o = out.setdefault(p, [0, 0.0, 0.0]); o[0] += s or 0; o[1] += float(k or 0); o[2] += float(rev or 0)
    return out

def page_clicks(conn, cid, a, b):
    out = {}
    for page, c in conn.execute("""SELECT page, sum(clicks) FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s GROUP BY 1""", [cid, a, b]):
        p = path_of(page.split("?")[0].split("#")[0]); out[p] = out.get(p, 0) + int(c or 0)
    return out

def branded(conn, cid, a, b):
    r = conn.execute("""SELECT sum(clicks) FILTER (WHERE is_branded), sum(impressions) FILTER (WHERE is_branded),
                               sum(clicks) FILTER (WHERE NOT is_branded), sum(impressions) FILTER (WHERE NOT is_branded)
                        FROM gsc_search_daily WHERE client_id=%s AND date BETWEEN %s AND %s""", [cid, a, b]).fetchone()
    return {"b_clicks": r[0] or 0, "b_impr": r[1] or 0, "n_clicks": r[2] or 0, "n_impr": r[3] or 0}

def compute(conn, cid, month):
    name, domain = conn.execute("SELECT name, domain FROM clients WHERE client_id=%s", [cid]).fetchone()
    a, b = month_bounds(month)
    pm = prev_month(month); pa, pb = month_bounds(pm)
    ly = month.replace(year=month.year - 1); la, lb = month_bounds(ly)
    R = {"client": name, "domain": domain, "month": month, "prev": pm, "ly": ly, "label": f"{month_name(month)} {month.year}",
         "period": f"1 to {b.day} {month_name(month)} {month.year}", "pperiod": f"1 to {pb.day} {month_name(pm)}"}
    # rankings
    q, pq = query_positions(conn, cid, a, b), query_positions(conn, cid, pa, pb)
    rk, prk = ranking_stats(q), (ranking_stats(pq) if pq else None)
    t, pt = gsc_totals(conn, cid, a, b), gsc_totals(conn, cid, pa, pb)
    entered = sorted([(k, v[2], v[0]) for k, v in q.items() if v[2] <= 10 and (k not in pq or pq[k][2] > 10)], key=lambda x: -x[2])
    left = sorted([(k, q[k][2], v[0]) for k, v in pq.items() if v[2] <= 10 and k in q and q[k][2] > 10], key=lambda x: -x[2])   # position now
    moved = sorted([(k, pq[k][2], v[2], v[0]) for k, v in q.items() if k in pq and abs(v[2] - pq[k][2]) >= 3 and min(v[2], pq[k][2]) <= 20],
                   key=lambda x: -(abs(x[2] - x[1]) * (x[3] ** .5)))
    R["rank"] = {"cur": rk, "prev": prk, "position": t["position"] if t else None, "pposition": pt["position"] if pt else None,
                 "entered": entered, "left": left, "moved": moved, "has_prev": bool(pq)}
    R["search"] = {"cur": t, "prev": pt}
    # GA4
    g, pg, lg = ga4_month(conn, cid, a, b), ga4_month(conn, cid, pa, pb), ga4_month(conn, cid, la, lb)
    R["traffic"] = {"cur": g, "prev": pg, "ly": lg}
    L, PL = landing(conn, cid, a, b), landing(conn, cid, pa, pb)
    R["landing"] = [(p, v[0], PL.get(p, [0])[0], v[1]) for p, v in sorted(L.items(), key=lambda x: -x[1][0])[:5]]
    R["landing_growth"] = [(p, v[0], PL.get(p, [0])[0], v[1]) for p, v in
                           sorted(L.items(), key=lambda x: -(x[1][0] - PL.get(x[0], [0])[0]))[:5] if v[0] - PL.get(p, [0])[0] > 0]
    R["revenue"] = {"cur": g["revenue"] if g else None, "prev": pg["revenue"] if pg else None, "ly": lg["revenue"] if lg else None,
                    "orders": g["transactions"] if g else None}
    R["products"] = [(i, int(u or 0), float(r or 0)) for i, u, r in conn.execute(
        """SELECT item_name, items_purchased, item_revenue FROM ga4_organic_items WHERE client_id=%s AND month=%s ORDER BY item_revenue DESC LIMIT 5""",
        [cid, month])]
    R["revenue_pages"] = [(p, v[2], v[0]) for p, v in sorted(L.items(), key=lambda x: -x[1][2])[:5] if v[2] > 0]
    # branded
    br, pbr = branded(conn, cid, a, b), branded(conn, cid, pa, pb)
    hist = []
    m = month
    for _ in range(6):
        ma, mb = month_bounds(m); x = branded(conn, cid, ma, mb)
        if x["b_clicks"] or x["n_clicks"]: hist.append((m.strftime("%b"), x["b_clicks"], x["n_clicks"]))
        m = prev_month(m)
    R["branded"] = {"cur": br, "prev": pbr, "hist": list(reversed(hist)),
                    "coverage": (br["b_clicks"] + br["n_clicks"]) / t["clicks"] if t and t["clicks"] else None}
    # URLs
    pc, ppc = page_clicks(conn, cid, a, b), page_clicks(conn, cid, pa, pb)
    allp = set(pc) | set(ppc)
    delta = sorted([(p, pc.get(p, 0), ppc.get(p, 0), pc.get(p, 0) - ppc.get(p, 0)) for p in allp], key=lambda x: -x[3])
    R["gaining"] = [d for d in delta if d[3] > 0][:5]
    R["declining"] = [d for d in reversed(delta) if d[3] < 0][:5]
    R["work_auto"], R["plan_auto"] = work_done(conn, cid, a, b, pa, pb), plan(conn, cid, R)
    saved = conn.execute("SELECT work_done, plan, updated_by, updated_at FROM monthly_reports WHERE client_id=%s AND month=%s", [cid, month]).fetchone()
    R["saved"] = {"work": saved[0], "plan": saved[1], "by": saved[2], "at": saved[3]} if saved else None
    R["work"] = parse_work(saved[0]) if saved and saved[0] else R["work_auto"]
    R["plan"] = parse_plan(saved[1]) if saved and saved[1] else R["plan_auto"]
    return R

def clicks_for(conn, cid, kw, a, b):
    r = conn.execute("SELECT sum(clicks) FROM gsc_search_daily WHERE client_id=%s AND lower(query)=lower(%s) AND date BETWEEN %s AND %s",
                     [cid, kw, a, b]).fetchone()[0]
    return int(r or 0)

def work_done(conn, cid, a, b, pa, pb):
    """[(area, [lines], result)] from what the team recorded in the tool during the month."""
    out = []
    end = b + dt.timedelta(days=1)
    fixed = conn.execute("""SELECT title, pages, status FROM tech_issues WHERE client_id=%s AND status IN ('fixed','verified')
                            AND updated_at >= %s AND updated_at < %s ORDER BY severity, title""", [cid, a, end]).fetchall()
    if fixed:
        ver = sum(1 for f in fixed if f[2] == "verified")
        out.append(("Technical fixes", [f"Fixed: {f[0].lower()}" for f in fixed[:5]] + ([f"and {len(fixed) - 5} more"] if len(fixed) > 5 else []),
                    f"{ver} of {len(fixed)} confirmed by the re-crawl"))
    written = conn.execute("""SELECT keyword FROM keyword_ideas WHERE client_id=%s AND status='done' AND updated_at >= %s AND updated_at < %s""",
                           [cid, a, end]).fetchall()
    if written:
        res = [f"{k}: {clicks_for(conn, cid, k, a, b)} clicks" for (k,) in written[:4]]
        out.append(("Content", [f"Published content for {len(written)} topic{'s' if len(written) > 1 else ''}: " + ", ".join(k for (k,) in written[:6])], "\n".join(res)))
    audited = conn.execute("""SELECT count(DISTINCT page_id) FROM page_snapshots WHERE client_id=%s AND taken_at >= %s AND taken_at < %s""",
                           [cid, a, end]).fetchone()[0]
    if audited:
        out.append(("On-page audit", [f"Checked {audited} pages against the SOP: titles, descriptions, headings, content, links, schema and brand rules."],
                    "Issues listed per page in the tool"))
    crawls = conn.execute("""SELECT count(*), max(health_score) FILTER (WHERE source='hovers') FROM tech_crawls WHERE client_id=%s
                             AND started_at >= %s AND started_at < %s""", [cid, a, end]).fetchone()
    if crawls[0]:
        out.append(("Technical audit", [f"Ran {crawls[0]} site crawl{'s' if crawls[0] > 1 else ''} for broken links, redirects, canonicals and indexing."],
                    f"Site health {crawls[1]:.0f}%" if crawls[1] is not None else "Issues tracked in the tool"))
    tracked = conn.execute("SELECT count(*) FROM rank_keywords WHERE client_id=%s AND added_at >= %s AND added_at < %s AND NOT sample",
                           [cid, a, end]).fetchone()[0]
    if tracked:
        out.append(("Keyword tracking", [f"Started tracking {tracked} keywords daily."], "Positions in the Rankings view"))
    if not out:
        out.append(("Work log", ["Nothing was marked done in the tool this month. Add the month's work here before sharing."], ""))
    return out

def plan(conn, cid, R):
    """Four blocks from the data: new pages, content, pages to revamp, technical fixes."""
    blocks = []
    gap = [r[0] for r in conn.execute("""SELECT keyword FROM keyword_gaps WHERE client_id=%s AND pulled_on=(SELECT max(pulled_on) FROM keyword_gaps
                                         WHERE client_id=%s) ORDER BY search_volume DESC NULLS LAST LIMIT 5""", [cid, cid])]
    rel = [r[0] for r in conn.execute("""SELECT keyword FROM keyword_ideas WHERE client_id=%s AND kind='related' AND status IN ('new','planned')
                                         ORDER BY status='planned' DESC, search_volume DESC NULLS LAST LIMIT 5""", [cid])]
    targets = (gap + [k for k in rel if k not in gap])[:6]
    if targets:
        blocks.append({"title": "New collection pages", "goal": "rank for searches the site does not target today.",
                       "bullets": [", ".join(targets)] + ["Each page gets an SEO title, meta description, H1, intro copy, FAQs and internal links."]})
    qs = [r[0] for r in conn.execute("""SELECT keyword FROM keyword_ideas WHERE client_id=%s AND kind='question' AND status IN ('new','planned')
                                        ORDER BY status='planned' DESC, search_volume DESC NULLS LAST LIMIT 5""", [cid])]
    if qs:
        blocks.append({"title": f"{len(qs)} blog{'s' if len(qs) > 1 else ''}", "goal": "answer the questions shoppers ask and guide readers to products.",
                       "bullets": qs + ["Each blog includes FAQs, product links and on-page SEO."]})
    dec = [d for d in R["declining"] if d[0] != "Home page"][:5]
    if dec:
        blocks.append({"title": f"Page revamp ({len(dec)} pages)", "goal": "recover clicks on pages that slipped this month.",
                       "bullets": [f"{short(p, 40)} ({c} clicks, was {pc})" for p, c, pc, d in dec] +
                                  ["Refresh titles and descriptions, expand the Read more section, add FAQs and internal links."]})
    issues = conn.execute("""SELECT title, pages FROM tech_issues WHERE client_id=%s AND status IN ('open','in_progress') AND pages > 0
                             ORDER BY array_position(ARRAY['error','warning','notice'], severity), pages DESC LIMIT 5""", [cid]).fetchall()
    if issues:
        blocks.append({"title": "Technical fixes", "goal": "fix what stops Google crawling and indexing the site.",
                       "bullets": [f"{t} ({p} pages)" for t, p in issues]})
    if not blocks:
        blocks.append({"title": "Plan", "goal": "add next month's plan here before sharing.", "bullets": []})
    return blocks[:4]

# ------------------------------------------------------------------ the analyst's edits: plain text both ways
def work_text(items):
    return "\n".join(f"{a} | {' / '.join(lines)} | {res.replace(chr(10), ' / ')}" for a, lines, res in items)

def parse_work(text):
    out = []
    for line in (text or "").splitlines():
        if not line.strip(): continue
        parts = [p.strip() for p in line.split("|")] + ["", ""]
        out.append((parts[0], [x.strip() for x in parts[1].split(" / ") if x.strip()], parts[2].replace(" / ", "\n")))
    return out

def plan_text(blocks):
    return "\n\n".join("\n".join([b["title"], "Goal: " + b["goal"]] + [f"- {x}" for x in b["bullets"]]) for b in blocks)

def parse_plan(text):
    out = []
    for chunk in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [l.strip() for l in chunk.splitlines() if l.strip()]
        if not lines: continue
        goal = next((l[5:].strip() for l in lines[1:] if l.lower().startswith("goal:")), "")
        out.append({"title": lines[0], "goal": goal, "bullets": [l.lstrip("-•* ").strip() for l in lines[1:] if not l.lower().startswith("goal:")]})
    return out[:4]

# ------------------------------------------------------------------ the deck
RED, DARK, GREEN, GREY, MID, CARD, LINE, WHITE = "C8102E", "1A1A1A", "1E7B34", "8C8C8C", "4D4D4D", "F2F2F2", "D9D9D9", "FFFFFF"

def build(R):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION

    rgb = lambda h: RGBColor.from_string(h)
    prs = Presentation(); prs.slide_width, prs.slide_height = Inches(10), Inches(5.625)
    blank = prs.slide_layouts[6]
    sections = ["Keyword rankings", "Search performance", "Organic traffic", "Organic revenue", "Branded vs non-branded", "Top performing URLs",
                f"Work done in {month_name(R['month'])}", f"Plan of action for {month_name(R['month'] + dt.timedelta(days=32))}"]
    total = len(sections) + 2
    footer = f"{R['client'].upper()}   •   MONTHLY SEO REPORT, {R['label'].upper()}"

    def text(slide, x, y, w, h, s, size=11, color=DARK, bold=False, italic=False, align=None, anchor=None, name=None):
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf = tb.text_frame
        tf.word_wrap = True; tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        if anchor: tf.vertical_anchor = anchor
        runs = s if isinstance(s, list) else [[(s, {})]] if isinstance(s, str) else s
        for i, para in enumerate(runs if isinstance(s, list) else [[(s, {})]]):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            if align: p.alignment = align
            for t, o in (para if isinstance(para, list) else [(para, {})]):
                r = p.add_run(); r.text = t; f = r.font
                f.name = "Arial"; f.size = Pt(o.get("size", size)); f.bold = o.get("bold", bold); f.italic = o.get("italic", italic)
                f.color.rgb = rgb(o.get("color", color))
        if name: tb.name = name
        return tb

    def rect(slide, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE):
        r = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
        r.fill.solid(); r.fill.fore_color.rgb = rgb(fill); r.line.fill.background(); r.shadow.inherit = False
        return r

    def page(no, kicker, title, sub):
        s = prs.slides.add_slide(blank)
        text(s, .53, .38, 7.1, .23, kicker.upper(), 9, RED, True)
        text(s, .53, .61, 8.3, .53, title, 18, DARK, True)
        text(s, .53, 1.16, 8.6, .26, sub, 9, GREY)
        rect(s, 9.0, .34, .49, .49, RED)
        text(s, 9.0, .34, .49, .49, f"{no:02d}", 11, WHITE, True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        text(s, .53, 5.27, 7.1, .19, footer, 7, "AAAAAA")
        text(s, 8.4, 5.27, 1.09, .19, f"{no:02d} / {total:02d}", 7, "AAAAAA", align=PP_ALIGN.RIGHT)
        return s

    def card(s, x, y, w, h, big, label, lines):
        rect(s, x, y, w, h, CARD); rect(s, x, y, .07, h, RED)
        text(s, x + .26, y + .1, w - .4, .45, big, 18 if len(big) < 10 else 15, DARK, True)
        text(s, x + .26, y + .55, w - .4, .22, label, 8, GREY)
        for i, (t, good) in enumerate(lines):
            text(s, x + .26, y + .76 + i * .17, w - .4, .2, t, 8, GREEN if good else (RED if good is False else GREY), True)

    def table(s, x, y, w, rows, widths, header, size=8, bold_first=True, colors=None, left_cols=1):
        tbl = s.shapes.add_table(len(rows) + 1, len(header), Inches(x), Inches(y), Inches(w), Inches(.28 * (len(rows) + 1))).table
        for j, cw in enumerate(widths): tbl.columns[j].width = Inches(cw)
        for i in range(len(rows) + 1):
            tbl.rows[i].height = Inches(.28)
            for j in range(len(header)):
                c = tbl.cell(i, j); c.margin_left = c.margin_right = Inches(.06); c.margin_top = c.margin_bottom = Inches(.03)
                c.vertical_anchor = MSO_ANCHOR.MIDDLE
                val = header[j] if i == 0 else rows[i - 1][j]
                c.fill.solid(); c.fill.fore_color.rgb = rgb(DARK if i == 0 else (WHITE if i % 2 else CARD))
                tf = c.text_frame; tf.word_wrap = True; p = tf.paragraphs[0]; p.alignment = PP_ALIGN.LEFT if j < left_cols else PP_ALIGN.RIGHT
                r = p.add_run(); r.text = str(val); f = r.font; f.name = "Arial"; f.size = Pt(size)
                f.bold = i == 0 or (bold_first and j == 0)
                col = WHITE if i == 0 else DARK
                if i and colors: col = colors(i - 1, j) or col
                f.color.rgb = rgb(col)
        return tbl

    def was(cur, prev, fmt, lower_better=False, unit=""):
        if prev is None: return ("no data for the month before", None)
        better = (cur < prev) if lower_better else (cur > prev)
        return (f"was {fmt(prev)}{unit} in {month_name(R['prev'])}", better if cur != prev else None)

    P = month_name(R["prev"])
    # 1. cover
    s = prs.slides.add_slide(blank)
    rect(s, 0, 0, .16, 5.625, RED)
    text(s, .53, 1.0, 8, .3, "MONTHLY SEO REPORT", 11, RED, True)
    text(s, .53, 1.35, 8.5, .8, R["client"], 41, DARK, True)
    text(s, .53, 2.2, 8.5, .4, f"Monthly SEO Report, {R['label']}", 20, MID)
    rect(s, .53, 2.75, .55, .04, RED)
    text(s, .53, 2.95, 8.5, .3, f"Prepared by Hovers   •   Data period: {R['period']}", 11, GREY)
    text(s, .53, 4.9, 8.5, .25, "www.hovers.in   •   Pune   •   London   •   Kochi", 8, "AAAAAA")
    # 2. contents
    s = page(2, "Contents", "What this report covers", f"{len(sections)} sections, each one page")
    notes = ["Average position, keywords in the top 10, search visibility, and what moved since " + P,
             "Clicks, views, click-through rate and average position from Google search",
             "Organic sessions, new users, engagement, key events and top landing pages",
             "Revenue from Google organic search and what sold", "Searches for the brand by name, compared with general product searches",
             f"The five pages gaining the most and the five losing the most since {P}", "What the team completed this month", "What we will focus on next month"]
    for i, (sec, note) in enumerate(zip(sections, notes)):
        y = 1.55 + i * .43
        rect(s, .53, y, .3, .3, RED)
        text(s, .53, y, .3, .3, f"{i + 3:02d}", 10, WHITE, True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        text(s, 1.0, y + .02, 3.0, .3, sec, 12, DARK, True, anchor=MSO_ANCHOR.MIDDLE)
        text(s, 4.2, y, 5.2, .32, note, 9, MID, anchor=MSO_ANCHOR.MIDDLE)
        ln = s.shapes.add_connector(1, Inches(.53), Inches(y + .38), Inches(9.47), Inches(y + .38)); ln.line.color.rgb = rgb(LINE); ln.line.width = Pt(.5)
    # 3. rankings
    rk = R["rank"]; c, pr = rk["cur"], rk["prev"]
    if pr:
        d = c["top10"] - pr["top10"]
        head = f"{n(c['top10'])} keywords rank in the top 10, " + (f"up from {n(pr['top10'])}" if d > 0 else f"down from {n(pr['top10'])}" if d < 0 else "the same as " + P)
    else:
        head = f"{n(c['top10'])} keywords rank in the top 10"
    s = page(3, "Keyword rankings", head, f"Search Console, {R['period']} compared with {R['pperiod']}, keywords with at least 10 views")
    f1 = lambda v: f"{v:.1f}"
    cards = [(f1(rk["position"]) if rk["position"] else "–", "average position", was(rk["position"], rk["pposition"], f1, True) if rk["position"] else ("", None)),
             (n(c["top3"]), "keywords in the top 3", was(c["top3"], pr["top3"] if pr else None, n)),
             (n(c["top4_10"]), "keywords in positions 4 to 10", was(c["top4_10"], pr["top4_10"] if pr else None, n)),
             (pct(c["visibility"]), "search visibility", was(c["visibility"] or 0, pr["visibility"] if pr else None, pct))]
    for i, (big, lab, w) in enumerate(cards): card(s, .53 + i * 2.25, 1.5, 2.14, 1.12, big, lab, [w])
    cols = [(f"Entered the top 10 ({n(len(rk['entered']))})", [(short(k, 26), f"{p:.1f}") for k, p, i in rk["entered"][:5]], ["Keyword", "Position"], GREEN),
            (f"Left the top 10 ({n(len(rk['left']))})", [(short(k, 26), f"{p:.1f}") for k, p, i in rk["left"][:5]], ["Keyword", "Position now"], RED),
            ("Moved the most", [(short(k, 24), f"{a:.1f} to {b:.1f}") for k, a, b, i in rk["moved"][:5]], ["Keyword", "Position"], None)]
    for i, (title, rows, hdr, col) in enumerate(cols):
        x = .53 + i * 3.07
        text(s, x, 2.81, 2.85, .23, title.upper(), 8, RED, True)
        if rows:
            mv = rk["moved"][:5]
            table(s, x, 3.04, 2.85, rows, [1.95, .9], hdr, 7.5, False,
                  colors=(lambda r, j, col=col: col if j == 1 else None) if col else
                  (lambda r, j: (GREEN if mv[r][2] < mv[r][1] else RED) if j == 1 else None))
        else:
            text(s, x, 3.1, 2.85, .3, "None this month" if rk["has_prev"] else f"Needs {P}'s data", 9, GREY, italic=True)
    text(s, .53, 4.76, 8.93, .4, "Search visibility estimates how much of the available clicks the site captures, using standard click rates by position "
         "(100% would mean every keyword ranks first). Moved the most shows keywords that shifted by 3 or more positions.", 8, MID, italic=True)
    # 4. search performance
    t, pt = R["search"]["cur"], R["search"]["prev"]
    s = page(4, "Search performance", f"{n(t['clicks'])} clicks and {n(t['impressions'])} views from Google search" if t else "No Search Console data for this month",
             f"Google Search Console, {R['period']} compared with {R['pperiod']}")
    if t:
        def line(cur, prev, kind):
            if not pt or prev is None: return ("no data for the month before", None)
            if kind == "pct": cc = change(cur, prev); return (f"{P[:3]}: {n(prev)}  ({chg_txt(cc)})", cur > prev if cur != prev else None)
            if kind == "ctr": return (f"{P[:3]}: {prev * 100:.2f}%  ({'+' if cur >= prev else ''}{(cur - prev) * 100:.2f} pts)", cur > prev if cur != prev else None)
            return (f"{P[:3]}: {prev:.1f}  ({abs(prev - cur):.1f} {'better' if cur < prev else 'worse'})", cur < prev if cur != prev else None)
        boxes = [(n(t["clicks"]), "total clicks", line(t["clicks"], pt and pt["clicks"], "pct")),
                 (n(t["impressions"]), "total views (impressions)", line(t["impressions"], pt and pt["impressions"], "pct")),
                 (f"{t['ctr'] * 100:.2f}%" if t["ctr"] else "–", "click through rate (CTR)", line(t["ctr"] or 0, pt and pt["ctr"], "ctr")),
                 (f"{t['position']:.1f}" if t["position"] else "–", "average position", line(t["position"] or 0, pt and pt["position"], "pos"))]
        for i, (big, lab, ln_) in enumerate(boxes):
            x, y = .53 + (i % 2) * 4.5, 1.55 + (i // 2) * 1.6
            rect(s, x, y, 4.38, 1.45, CARD); rect(s, x, y, .07, 1.45, RED)
            text(s, x + .26, y + .18, 4, .55, big, 26, DARK, True)
            text(s, x + .26, y + .75, 4, .22, lab, 9, GREY)
            text(s, x + .26, y + .98, 4, .22, ln_[0], 9, GREEN if ln_[1] else (RED if ln_[1] is False else GREY), True)
    text(s, .53, 4.8, 8.93, .4, "Clicks are visits from Google search results. Views count each time a page showed in results. CTR is clicks divided by views. "
         f"Average position is where the site ranks on average, so a lower number is better. Green is better than {P}, red is worse.", 8, MID, italic=True)
    # 5. organic traffic
    g, pg, lg = R["traffic"]["cur"], R["traffic"]["prev"], R["traffic"]["ly"]
    if g and pg and pg["sessions"]:
        cc = change(g["sessions"], pg["sessions"])
        head = f"Organic sessions are {'up' if cc >= 0 else 'down'} {abs(cc) * 100:.0f}% on {P}"
    else:
        head = f"{n(g['sessions'])} organic sessions in {month_name(R['month'])}" if g else "No GA4 data for this month"
    s = page(5, "Organic traffic", head, f"Organic Search from Google Analytics 4, compared with {P}" + (f" and with {month_name(R['ly'])} {R['ly'].year}" if lg else ""))
    if g:
        def cmp(key, kind, ref, label):
            if not ref or ref.get(key) is None or g.get(key) is None: return None
            if kind == "pts":
                d = (g[key] - ref[key]) * 100; return (f"{'+' if d >= 0 else ''}{d:.1f} pts vs {label}", d > 0 if d else None)
            cc = change(g[key], ref[key]); return (f"{chg_txt(cc)} vs {label}", cc > 0 if cc else None) if cc is not None else None
        ly_label = R["ly"].strftime("%b %Y")
        items = [("sessions", n(g["sessions"]), "organic sessions", "pct"), ("new_users", n(g["new_users"]), "new users from organic", "pct"),
                 ("engagement", pct(g["engagement"], 1), "engagement rate", "pts"), ("key_events", n(g["key_events"]), "key events", "pct"),
                 ("share", pct(g["share"], 1), "share of total traffic", "pts")]
        for i, (k, big, lab, kind) in enumerate(items):
            lines = [x for x in (cmp(k, kind, pg, P), cmp(k, kind, lg, ly_label)) if x]
            card(s, .53 + i * 1.8, 1.5, 1.7, 1.12, big, lab, lines or [("no earlier data", None)])
        text(s, .53, 2.81, 4.3, .23, "TOP 5 ORGANIC LANDING PAGES", 8, RED, True)
        if R["landing"]:
            table(s, .53, 3.04, 4.35, [(short(p, 30), n(a), n(b), n(k)) for p, a, b, k in R["landing"]], [2.05, .75, .7, .85],
                  ["Page", "Sessions", P[:3], "Key events"], 7.5)
        text(s, 5.1, 2.81, 4.3, .23, f"FASTEST GROWING SINCE {P.upper()}", 8, RED, True)
        if R["landing_growth"]:
            table(s, 5.1, 3.04, 4.37, [(short(p, 30), n(a), n(b), f"+{n(a - b)}") for p, a, b, k in R["landing_growth"]], [2.07, .75, .7, .85],
                  ["Page", "Sessions", P[:3], "Change"], 7.5, colors=lambda r, j: GREEN if j == 3 else None)
        text(s, .53, 4.85, 8.93, .35, "Organic Search channel only. Key events are all events set up as key events in GA4, including add to cart and checkout steps.",
             8, MID, italic=True)
    # 6. organic revenue
    rv = R["revenue"]
    if rv["cur"] is not None and rv["prev"]:
        cc = change(rv["cur"], rv["prev"]); head = f"Organic revenue is {'up' if cc >= 0 else 'down'} {abs(cc) * 100:.0f}% on {P}"
    else:
        head = f"Organic revenue of {inr(rv['cur'])} in {month_name(R['month'])}" if rv["cur"] is not None else "No GA4 revenue data for this month"
    s = page(6, "Organic revenue", head, "Revenue from Google organic search in GA4 (purchase revenue), compared with " + P + (f" and with {month_name(R['ly'])} {R['ly'].year}" if rv["ly"] is not None else ""))
    vals = [(inr(rv["cur"]), f"organic revenue, {R['label']}"), (inr(rv["prev"]), f"organic revenue, {P} {R['prev'].year}")] + \
           ([(inr(rv["ly"]), f"organic revenue, {month_name(R['ly'])} {R['ly'].year}")] if rv["ly"] is not None else [])
    for i, (big, lab) in enumerate(vals):
        lines = []
        if i == 0:
            parts = [x for x in [f"{chg_txt(change(rv['cur'], rv['prev']))} vs {P}" if change(rv["cur"], rv["prev"]) is not None else "",
                                 f"{chg_txt(change(rv['cur'], rv['ly']))} vs {month_name(R['ly'])} {R['ly'].year}" if change(rv["cur"], rv["ly"]) is not None else ""] if x]
            if parts: lines = [("   ".join(parts), (rv["cur"] or 0) >= (rv["prev"] or 0))]
        card(s, .53 + i * 3.0, 1.5, 2.88, 1.12, big, lab, lines)
    if R["products"]:
        text(s, .53, 2.81, 8.9, .23, f"TOP 5 PRODUCTS FROM ORGANIC SEARCH, {R['label'].upper()}", 8, RED, True)
        table(s, .53, 3.04, 8.94, [(short(i, 60), n(u), inr(r)) for i, u, r in R["products"]], [6.1, 1.3, 1.54], ["Product", "Units sold", "Revenue"], 8)
        share = sum(r for i, u, r in R["products"]) / rv["cur"] if rv["cur"] else None
        note = f"These five products made up {pct(share)} of organic revenue. " if share else ""
    else:
        text(s, .53, 2.81, 8.9, .23, f"TOP 5 LANDING PAGES BY ORGANIC REVENUE, {R['label'].upper()}", 8, RED, True)
        if R["revenue_pages"]:
            table(s, .53, 3.04, 8.94, [(short(p, 60), inr(r), n(ss)) for p, r, ss in R["revenue_pages"]], [6.1, 1.54, 1.3], ["Landing page", "Revenue", "Sessions"], 8)
        note = "Product-level sales were not available from GA4 for this property, so revenue is shown by landing page. "
    text(s, .53, 4.85, 8.93, .35, note + f"Organic search placed {n(rv['orders'])} orders in {month_name(R['month'])}. Revenue is GA4 purchase revenue for the Organic Search channel.",
         8, MID, italic=True)
    # 7. branded
    b, pb = R["branded"]["cur"], R["branded"]["prev"]
    tot = b["b_clicks"] + b["n_clicks"]
    share_b = b["b_clicks"] / tot if tot else None
    s = page(7, "Branded vs non-branded", f"{pct(share_b)} of clicks come from brand searches" if share_b is not None else "No search data for this month",
             f"Clicks and views by type of search, {R['period']} compared with {R['pperiod']}")
    def cc_(a_, b_): x = change(a_, b_); return chg_txt(x) if x is not None else "–"
    rows = [("Branded", n(b["b_clicks"]), cc_(b["b_clicks"], pb["b_clicks"]), n(b["b_impr"]), cc_(b["b_impr"], pb["b_impr"]), pct(b["b_clicks"] / b["b_impr"], 1) if b["b_impr"] else "–"),
            ("Non-branded", n(b["n_clicks"]), cc_(b["n_clicks"], pb["n_clicks"]), n(b["n_impr"]), cc_(b["n_impr"], pb["n_impr"]), pct(b["n_clicks"] / b["n_impr"], 1) if b["n_impr"] else "–")]
    table(s, .53, 1.55, 5.0, rows, [1.15, .75, .7, .95, .7, .75], ["Type of search", "Clicks", "Change", "Views", "Change", "CTR"], 8,
          colors=lambda r, j: (GREEN if rows[r][j].startswith("+") else RED if rows[r][j].startswith("-") else None) if j in (2, 4) else None)
    text(s, .53, 2.5, 5.0, .7, [[("Branded: ", {"bold": True}), (f"searches with {R['client']} or its other names in them.", {})],
                                [("Non-branded: ", {"bold": True}), ("general product searches, from people who may not know the brand.", {})]], 8, MID)
    bc, nc = change(b["b_clicks"], pb["b_clicks"]), change(b["n_clicks"], pb["n_clicks"])
    if bc is not None and nc is not None:
        lead = "brand demand is what moves the total" if (share_b or 0) >= .5 else "general searches are what move the total"
        reading = (f"Branded clicks are {'up' if bc >= 0 else 'down'} {abs(bc) * 100:.0f}% and non-branded clicks are {'up' if nc >= 0 else 'down'} "
                   f"{abs(nc) * 100:.0f}% compared with {P}. Because {'most' if (share_b or 0) >= .5 else 'fewer'} clicks come from brand searches, {lead}.")
        rect(s, .53, 3.3, 5.0, 1.05, CARD); rect(s, .53, 3.3, .07, 1.05, RED)
        text(s, .75, 3.4, 4.65, .9, [[("Reading this:  ", {"bold": True}), (reading, {})]], 8, DARK)
    cov = R["branded"]["coverage"]
    if cov: text(s, .53, 4.5, 5.0, .3, f"Google hides rare searches for privacy, so this split covers about {pct(cov)} of {month_name(R['month'])} clicks.", 8, MID, italic=True)
    text(s, 5.85, 1.55, 3.6, .23, "CLICKS BY TYPE OF SEARCH, MONTH BY MONTH", 8, RED, True)
    if len(R["branded"]["hist"]) >= 2:
        cd = CategoryChartData(); cd.categories = [h[0] for h in R["branded"]["hist"]]
        cd.add_series("Branded", [h[1] for h in R["branded"]["hist"]]); cd.add_series("Non-branded", [h[2] for h in R["branded"]["hist"]])
        ch = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_STACKED, Inches(5.85), Inches(1.8), Inches(3.6), Inches(3.0), cd).chart
        ch.has_legend = True; ch.legend.position = XL_LEGEND_POSITION.BOTTOM; ch.legend.include_in_layout = False
        ch.legend.font.size = Pt(8); ch.legend.font.name = "Arial"
        for ser, colr in zip(ch.series, (DARK, RED)): ser.format.fill.solid(); ser.format.fill.fore_color.rgb = rgb(colr)
        ch.plots[0].gap_width = 60
        ch.value_axis.visible = False; ch.value_axis.has_major_gridlines = False
        ch.category_axis.tick_labels.font.size = Pt(8); ch.category_axis.tick_labels.font.name = "Arial"
        ch.category_axis.format.line.color.rgb = rgb(LINE)
    else:
        text(s, 5.85, 1.9, 3.6, .3, "The month-by-month chart fills in as more months are stored.", 9, GREY, italic=True)
    # 8. URLs
    s = page(8, "Top performing URLs", "Which pages are gaining and which are slipping", f"Clicks per page, {R['period']} compared with {R['pperiod']}")
    for i, (title, rows_, col) in enumerate([("TOP 5 GAINING", R["gaining"], GREEN), ("TOP 5 DECLINING", R["declining"], RED)]):
        x = .53 + i * 4.57
        text(s, x, 1.55, 4.3, .23, title, 8, RED, True)
        if rows_:
            table(s, x, 1.8, 4.37, [(short(p, 32), n(a), n(b_), f"{'+' if d > 0 else ''}{n(d)}") for p, a, b_, d in rows_], [2.3, .65, .65, .77],
                  ["Page", month_name(R["month"])[:3], P[:3], "Change"], 8, colors=lambda r, j, col=col: col if j == 3 else None)
        else:
            text(s, x, 1.85, 4.3, .3, "None this month", 9, GREY, italic=True)
    text(s, .53, 4.6, 8.93, .4, f"A page that is new this month shows 0 for {P}. A page can fall because it lost rankings, or because the searches it answers were less common this month.",
         8, MID, italic=True)
    # 9. work done
    W = R["work"]
    s = page(9, f"Work done in {month_name(R['month'])}", f"What the team completed in {month_name(R['month'])}", "Recorded in the tool, with early results from Search Console")
    rows = [(a_, "\n".join(lines), res or "Not tracked separately") for a_, lines, res in W[:6]]
    tbl = table(s, .53, 1.55, 8.94, rows, [1.7, 4.9, 2.34], ["Area", "Work completed", "Early result"], 8, left_cols=3)
    for i in range(1, len(rows) + 1): tbl.rows[i].height = Inches(.5)
    # 10. plan
    s = page(10, "Plan of action", f"Plan of action for {month_name(R['month'] + dt.timedelta(days=32))}", "From this month's data: what to build, write, fix and recover")
    for i, bl in enumerate(R["plan"][:4]):
        x, y = .53 + (i % 2) * 4.5, 1.55 + (i // 2) * 1.85
        rect(s, x, y, 4.38, 1.75, CARD); rect(s, x, y, .07, 1.75, RED)
        paras = [[(f"{i + 1}. {bl['title'].upper()}", {"bold": True, "color": RED, "size": 9})]]
        if bl.get("goal"): paras.append([("Goal: ", {"bold": True}), (bl["goal"], {})])
        paras += [[("•  " + x_, {})] for x_ in bl["bullets"][:6]]
        text(s, x + .22, y + .12, 4.0, 1.55, paras, 8, DARK)
    out = io.BytesIO(); prs.save(out); return out.getvalue()

def default_month(today=None):
    """The last complete month (Search Console needs ~3 days to settle, so before the 4th it is the month before)."""
    today = today or dt.date.today()
    m = prev_month(today.replace(day=1))
    return prev_month(m) if today.day < 4 else m
