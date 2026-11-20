"""Execution screens: blog posts written from the brand's search data, reviewed and published to its store.

  /c/<cid>/blogs             drafts waiting for review, approved, published, and the idea list behind them
  /c/<cid>/blogs/<id>        review: read and edit the post, see its checks and why it was chosen, approve, publish
  /c/<cid>/publishing        connect the Shopify store, pick the blog and author
  /p/<token>                 (app.py) public preview link of a draft, for sharing before it goes live
Hovers staff and the brand's owner can edit and publish; other client viewers can read.
"""
import json, re, secrets, os
from flask import request, redirect, url_for, session, abort
from dashboard import bp, connect, view, client_or_404, check_csrf, back_with, can_edit, start_job, ONLINE
import blogs as B

STATUS = {"idea": ("Idea", "t-na"), "writing": ("Writing", "t-na"), "draft": ("For review", "t-blk"), "approved": ("Approved", "t-pass"),
          "published": ("Published", "t-pass"), "failed": ("Writing failed", "t-sev-error"), "discarded": ("Discarded", "t-na")}
TABS = [("review", "For review", ("draft", "writing", "failed")), ("approved", "Approved", ("approved",)),
        ("published", "Published", ("published",)), ("ideas", "Topic ideas", ("idea",)), ("discarded", "Discarded", ("discarded",))]
DCOLS = ["draft_id", "status", "target_keyword", "secondary_keywords", "intent", "evidence", "priority", "title", "meta_title",
         "meta_description", "slug", "excerpt", "tags", "body_html", "faqs", "sources", "internal_links", "word_count", "checks", "score",
         "preview_token", "model", "error", "created_at", "updated_at", "written_at", "edited_by", "reviewed_by", "reviewed_at",
         "published_by", "published_at", "platform", "external_id", "published_url"]

def err_to(url, text):
    from urllib.parse import urlencode
    return redirect(url + ("&" if "?" in url else "?") + urlencode({"err": text}))

def who():
    return session.get("staff_email") or session.get("viewer_email")

def why(evidence, limit=3):
    """Plain lines on why a topic was picked, strongest first."""
    out = []
    for e in (evidence or [])[:limit]:
        s = e.get("source")
        if s == "gsc": out.append(("Google", f"“{e['text']}” · {e['impressions']:,} impressions, position {e['position']}"))
        elif s == "prompt": out.append(("ChatGPT", f"“{e['text']}” · brand not named" + (f"; names {', '.join(e['rivals'][:2])}" if e.get("rivals") else "")))
        elif s == "reddit": out.append(("Reddit", f"“{e['text']}” · r/{e.get('subreddit') or '?'}"))
        else: out.append(("Google suggest", f"“{e['text']}”"))
    return out

def draft(conn, cid, did):
    r = conn.execute(f"SELECT {', '.join(DCOLS)} FROM content_drafts WHERE client_id=%s AND draft_id::text=%s", [cid, did]).fetchone()
    if not r: abort(404)
    d = dict(zip(DCOLS, r)); d["draft_id"] = str(d["draft_id"])
    return d

def store(conn, cid):
    import shopify
    c = shopify.connection(conn, cid)
    if c: c["blogs"] = c["blogs"] or []
    return c

def preview_url(token):
    host = os.environ.get("APP_HOST")
    return (f"https://{host}" if host else request.host_url.rstrip("/")) + url_for("preview", token=token)

@bp.get("/c/<cid>/blogs")
def blogs(cid):
    tab = request.args.get("tab", "review")
    states = dict((k, v) for k, _, v in TABS).get(tab) or TABS[0][2]
    with connect() as conn:
        c = client_or_404(conn, cid)
        counts = dict(conn.execute("SELECT status, count(*) FROM content_drafts WHERE client_id=%s GROUP BY status", [cid]).fetchall())
        rows = [dict(zip(DCOLS, r)) for r in conn.execute(f"""SELECT {', '.join(DCOLS)} FROM content_drafts WHERE client_id=%s AND status = ANY(%s)
                    ORDER BY CASE WHEN status='idea' THEN 0 ELSE 1 END, priority DESC NULLS LAST, coalesce(published_at, written_at, created_at) DESC
                    LIMIT 200""", [cid, list(states)]).fetchall()]
        s = store(conn, cid)
        last = conn.execute("""SELECT started_at, status, error FROM collector_runs WHERE client_id=%s AND collector='blogs'
                               ORDER BY started_at DESC LIMIT 1""", [cid]).fetchone()
        has_gsc = conn.execute("SELECT EXISTS (SELECT 1 FROM gsc_search_daily WHERE client_id=%s)", [cid]).fetchone()[0]
        import worker
        can_write, next_at = worker.paid_next(conn, cid, "blogs")
    tabcount = {k: sum(counts.get(x, 0) for x in v) for k, _, v in TABS}
    return view("blogs.html", c, "blogs", tab=tab, tabs=TABS, tabcount=tabcount, rows=rows, S=STATUS, why=why, store=s, last=last,
                has_gsc=has_gsc, edit=can_edit(cid), msg=request.args.get("msg"), preview_url=preview_url,
                can_write=can_write, next_at=next_at, per_day=worker.paid_per_day(), max_drafts=worker.blog_count_limit())

@bp.post("/c/<cid>/blogs")
def blogs_post(cid):
    check_csrf()
    what, did = request.form.get("what"), request.form.get("draft_id")
    import worker
    write_count = 1
    if what == "run":
        try:
            write_count = worker.blog_count(request.form.get("draft_count", "1"))
        except ValueError:
            return back_with(url_for("dash.blogs", cid=cid), f"Choose a whole number from 1 to {worker.blog_count_limit()}.")
    with connect() as conn:
        client_or_404(conn, cid)
        if what == "discard" and did:
            conn.execute("UPDATE content_drafts SET status='discarded', updated_at=now() WHERE client_id=%s AND draft_id::text=%s AND status <> 'published'", [cid, did])
            return back_with(url_for("dash.blogs", cid=cid, tab=request.form.get("tab", "review")), "Discarded. It will not be suggested again.")
        if what == "restore" and did:
            conn.execute("UPDATE content_drafts SET status=CASE WHEN body_html IS NULL THEN 'idea' ELSE 'draft' END, updated_at=now() WHERE client_id=%s AND draft_id::text=%s AND status='discarded'", [cid, did])
            return back_with(url_for("dash.blogs", cid=cid, tab="discarded"), "Restored.")
        if what == "write" and did:                                     # this idea next, then run the writer
            conn.execute("UPDATE content_drafts SET priority = 1000 + coalesce(priority,0), updated_at=now() WHERE client_id=%s AND draft_id::text=%s AND status IN ('idea','failed')", [cid, did])
        elif what == "add":                                             # a topic someone asked for
            kw = re.sub(r"\s+", " ", request.form.get("keyword", "")).strip()[:160]
            if len(kw) < 4: return back_with(url_for("dash.blogs", cid=cid, tab="ideas"), "Type the topic or keyword the post should target.")
            conn.execute("""INSERT INTO content_drafts (client_id, target_keyword, intent, evidence, priority) VALUES (%s,%s,'informational',%s,900)
                            ON CONFLICT DO NOTHING""", [cid, kw, json.dumps([{"source": "team", "text": kw, "by": who()}])])
            if request.form.get("now") != "1":
                return back_with(url_for("dash.blogs", cid=cid, tab="ideas"), f"Added “{kw}” to the ideas.")
        elif what != "run":
            abort(400)
        import worker
        ok, nxt = worker.paid_next(conn, cid, "blogs")
        if not ok:
            when = nxt.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).strftime("%d %b, %H:%M IST") if hasattr(nxt, "astimezone") else nxt
            return back_with(url_for("dash.blogs", cid=cid, tab=request.form.get("tab", "review")),
                             f"Saved. Drafts are written {worker.paid_per_day()} time(s) a day per brand; the next run can start {when}. Connect the brand's ChatGPT plan in Integrations to write drafts any time.")
    start_job(cid, "blogs", blog_count=write_count)
    return back_with(url_for("dash.blogs", cid=cid, tab="review"), "Writing has started. Drafts appear here in a few minutes, each with a preview link.")

@bp.get("/c/<cid>/blogs/<did>")
def blog(cid, did):
    import checks as K
    with connect() as conn:
        c = client_or_404(conn, cid)
        d = draft(conn, cid, did)
        s = store(conn, cid)
    if d["status"] == "idea": return redirect(url_for("dash.blogs", cid=cid, tab="ideas"))
    res = d["checks"] or []
    groups = []
    for g, label, _ in K.GROUPS:
        items = [dict(r, name=K.info(r["check_id"])[1]) for r in res if K.info(r["check_id"])[0] == g and r["result"] != "na"]
        if items: groups.append((label, items))
    fails = [r for r in res if r["result"] == "fail"]
    return view("blog.html", c, "blogs", d=d, S=STATUS, why=why, groups=groups, fails=fails, store=s, edit=can_edit(cid),
                msg=request.args.get("msg"), err=request.args.get("err"), preview=preview_url(d["preview_token"]) if d["preview_token"] else None)

def _save(conn, cid, d):
    """Applies the review form to the draft and re-runs the checks."""
    import run_checks
    f = request.form
    faqs = []
    for i in range(12):
        q, a = (f.get(f"faq_q_{i}") or "").strip(), (f.get(f"faq_a_{i}") or "").strip()
        if q and a: faqs.append({"q": q[:300], "a": a[:1500]})
    body, internal, external = B.clean_body(f.get("body_html", d["body_html"] or ""), client_or_404(conn, cid)["domain"], None)
    new = {"title": B.dashes(f.get("title", d["title"] or "")).strip()[:200], "meta_title": B.dashes(f.get("meta_title", "")).strip()[:120],
           "meta_description": B.dashes(f.get("meta_description", "")).strip()[:320], "slug": B.slugify(f.get("slug") or f.get("title") or d["slug"]),
           "excerpt": B.dashes(f.get("excerpt", "")).strip()[:600], "tags": [t.strip() for t in f.get("tags", "").split(",") if t.strip()][:10],
           "body_html": body, "faqs": faqs, "target_keyword": d["target_keyword"], "secondary_keywords": d["secondary_keywords"]}
    if not new["title"] or not body: abort(400, "A post needs a title and a body.")
    name, domain = conn.execute("SELECT name, domain FROM clients WHERE client_id=%s", [cid]).fetchone()
    s = store(conn, cid) or {}
    results, sc = B.check(new, domain, run_checks.load_brand(conn, cid), s.get("blog_handle"), s.get("author_name") or name, name)
    conn.execute("""UPDATE content_drafts SET title=%s, meta_title=%s, meta_description=%s, slug=%s, excerpt=%s, tags=%s, body_html=%s, faqs=%s,
                    internal_links=%s, word_count=%s, checks=%s, score=%s, edited_by=%s, updated_at=now(),
                    status=CASE WHEN status IN ('failed','writing') THEN 'draft' ELSE status END WHERE draft_id=%s""",
                 [new["title"], new["meta_title"], new["meta_description"], new["slug"], new["excerpt"], new["tags"], body, json.dumps(faqs),
                  internal, B.words(body), json.dumps(results), sc, who(), d["draft_id"]])
    return draft(conn, cid, d["draft_id"])

@bp.post("/c/<cid>/blogs/<did>")
def blog_post(cid, did):
    check_csrf()
    import shopify
    what = request.form.get("what")
    here = url_for("dash.blog", cid=cid, did=did)
    with connect() as conn:
        client_or_404(conn, cid)
        d = draft(conn, cid, did)
        if "title" in request.form and what in ("save", "approve", "publish", "update"):
            d = _save(conn, cid, d)                                      # the review form saves first, then acts
        if what == "save":
            return back_with(here, "Saved. Checks updated." + (" The live post is unchanged until you publish again." if d["status"] == "published" else ""))
        if what == "approve":
            conn.execute("UPDATE content_drafts SET status='approved', reviewed_by=%s, reviewed_at=now(), updated_at=now() WHERE draft_id=%s", [who(), d["draft_id"]])
            return back_with(here, "Approved. Publish it now, or later from the Approved tab.")
        if what == "discard":
            if d["status"] == "published": abort(400)
            conn.execute("UPDATE content_drafts SET status='discarded', updated_at=now() WHERE draft_id=%s", [d["draft_id"]])
            return back_with(url_for("dash.blogs", cid=cid), "Discarded.")
        if what in ("publish", "update"):
            try:
                if not d["reviewed_by"]:
                    conn.execute("UPDATE content_drafts SET reviewed_by=%s, reviewed_at=now() WHERE draft_id=%s", [who(), d["draft_id"]])
                url = shopify.publish(conn, cid, d, who())
            except shopify.Reconnect as e:
                return err_to(url_for("dash.publishing", cid=cid), str(e) + " Then publish again.")
            except Exception as e:
                conn.execute("UPDATE content_drafts SET error=%s WHERE draft_id=%s", [str(e)[:500], d["draft_id"]])
                return err_to(here, f"Not published. {e}")
            return back_with(here, ("Updated on the store: " if d["external_id"] else "Published: ") + url)
        if what == "unpublish" and d["external_id"]:
            try: shopify.unpublish(conn, cid, d)
            except Exception as e: return err_to(here, f"Could not hide it. {e}")
            return back_with(here, "Hidden on the store. It stays in Approved; publish again any time.")
    abort(400)

# ------------------------------------------------------------------ publishing settings (Shopify)
@bp.get("/c/<cid>/publishing")
def publishing(cid):
    import shopify
    with connect() as conn:
        c = client_or_404(conn, cid)
        s = store(conn, cid)
    detected = None
    if not (s and s.get("shop")) and c["domain"]: detected = shopify.detect_shop(c["domain"])
    shared = bool(os.environ.get("SHOPIFY_CLIENT_ID") and os.environ.get("SHOPIFY_CLIENT_SECRET"))
    return view("publishing.html", c, "publishing", store=s, detected=detected, shared=shared, edit=True,
                own_app=bool(s and s.get("app_client_id")), msg=request.args.get("msg"), err=request.args.get("err"),
                redirect_uri=redirect_uri())

def redirect_uri():
    if os.environ.get("SHOPIFY_REDIRECT_URI"): return os.environ["SHOPIFY_REDIRECT_URI"]
    host = os.environ.get("APP_HOST") or request.host
    return ("https://" if os.environ.get("APP_HOST") or ONLINE else request.scheme + "://") + host + "/shopify/callback"

@bp.post("/c/<cid>/publishing")
def publishing_post(cid):
    check_csrf()
    import shopify, google_auth as ga
    what = request.form.get("what")
    here = url_for("dash.publishing", cid=cid)
    with connect() as conn:
        client_or_404(conn, cid)
        s = store(conn, cid)
        if what == "connect":
            shop = shopify.normalise_shop(request.form.get("shop"))
            if not shopify.valid_shop(shop):
                return err_to(here, "Enter the store's .myshopify.com address, for example brand.myshopify.com.")
            client_id, secret = shopify.app_creds(s)
            if not client_id:
                return err_to(here, "Shopify publishing is not switched on yet: the Orbit Shopify app's keys are not set. Ask the Hovers team.")
            conn.execute("""INSERT INTO cms_connections (client_id, platform, shop, status) VALUES (%s,'shopify',%s,'pending')
                            ON CONFLICT (client_id, platform) DO UPDATE SET shop=EXCLUDED.shop, updated_at=now()""", [cid, shop])
            state = secrets.token_urlsafe(24)
            session["shopify_oauth"] = {"state": state, "cid": cid, "shop": shop}
            return redirect(shopify.authorize_url(shop, client_id, redirect_uri(), state))
        if what == "settings" and s:
            pick = next((b for b in s["blogs"] if b["id"] == request.form.get("blog_id")), None)
            conn.execute("""UPDATE cms_connections SET blog_id=coalesce(%s, blog_id), blog_handle=coalesce(%s, blog_handle), blog_title=coalesce(%s, blog_title),
                            author_name=%s, updated_at=now() WHERE client_id=%s AND platform='shopify'""",
                         [pick and pick["id"], pick and pick["handle"], pick and pick["title"],
                          (request.form.get("author") or "").strip()[:120] or None, cid])
            return back_with(here, "Saved. New posts go to " + ((pick or s)["title"] if pick else (s.get("blog_title") or "the store's blog")) + ".")
        if what == "reload" and s:
            try: n = len(shopify.load_store(conn, cid))
            except Exception as e: return err_to(here, str(e)[:300])
            return back_with(here, f"{n} blogs found on the store.")
        if what == "app":                                         # a per-store Shopify app: Hovers team only
            if not session.get("staff_email"): abort(403)
            cid_app, secret = (request.form.get("app_client_id") or "").strip(), (request.form.get("app_secret") or "").strip()
            if not cid_app or not secret: return err_to(here, "Enter both the client ID and the secret.")
            conn.execute("""INSERT INTO cms_connections (client_id, platform, app_client_id, app_secret_enc, status) VALUES (%s,'shopify',%s,%s,'pending')
                            ON CONFLICT (client_id, platform) DO UPDATE SET app_client_id=EXCLUDED.app_client_id, app_secret_enc=EXCLUDED.app_secret_enc, updated_at=now()""",
                         [cid, cid_app[:200], ga.encrypt(secret[:400])])
            return back_with(here, "Saved this store's own app. Now connect the store.")
        if what == "app_remove":
            if not session.get("staff_email"): abort(403)
            conn.execute("UPDATE cms_connections SET app_client_id=NULL, app_secret_enc=NULL WHERE client_id=%s AND platform='shopify'", [cid])
            return back_with(here, "This store now uses the shared Orbit app.")
        if what == "disconnect" and s:
            conn.execute("""UPDATE cms_connections SET access_token_enc=NULL, refresh_token_enc=NULL, expires_at=NULL, status='pending'
                            WHERE client_id=%s AND platform='shopify'""", [cid])
            return back_with(here, "Disconnected. Orbit can no longer publish to this store. Remove the app in Shopify too if you no longer need it.")
    abort(400)
