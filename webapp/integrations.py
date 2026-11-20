"""Integrations: connect a brand's own ChatGPT plan ("Continue with ChatGPT"), so its AI answers and blog drafts use that plan
instead of Hovers' API key. Staff and the brand's own client users can connect, test and disconnect. See collectors/chatgpt_plan.py."""
import os
from flask import request, session, redirect, url_for, abort
from dashboard import bp, connect, view, client_or_404, check_csrf, back_with
import chatgpt_plan as cp

def who():
    return session.get("staff_email") or session.get("viewer_email")

def err_to(url, text):
    from urllib.parse import urlencode
    return redirect(url + ("&" if "?" in url else "?") + urlencode({"err": text}))

def callback_url():
    host = os.environ.get("APP_HOST") or request.host
    return ("https://" if os.environ.get("APP_HOST") or os.environ.get("VERCEL") else request.scheme + "://") + host + "/integrations/chatgpt/callback"

@bp.get("/c/<cid>/integrations")
def integrations(cid):
    with connect() as conn:
        c = client_or_404(conn, cid)
        gpt = cp.connection(conn, cid)
        gsc = conn.execute("SELECT gsc_site_url IS NOT NULL, ga4_property_id IS NOT NULL FROM client_integrations WHERE client_id=%s", [cid]).fetchone()
        shop = conn.execute("SELECT status, shop FROM cms_connections WHERE client_id=%s AND platform='shopify'", [cid]).fetchone()
    st = session.get("chatgpt_oauth") or {}
    signing = st if st.get("cid") == cid and request.args.get("step") == "paste" else None
    return view("integrations.html", c, "integrations", gpt=gpt, mode=cp.mode(), signing=signing, gsc=gsc, shop=shop,
                has_key=bool(os.environ.get("OPENAI_API_KEY_2")), msg=request.args.get("msg"), err=request.args.get("err"))

@bp.post("/c/<cid>/integrations")
def integrations_post(cid):
    check_csrf()
    what = request.form.get("what")
    here = url_for("dash.integrations", cid=cid)
    with connect() as conn:
        client_or_404(conn, cid)
        if what == "connect":
            url, state = cp.start(conn, cid, who(), callback_url())
            session["chatgpt_oauth"] = {"cid": cid, "state": state, "url": url}
            if cp.mode() == "direct": return redirect(url)
            return redirect(here + "?step=paste")
        if what == "paste":
            st = session.get("chatgpt_oauth") or {}
            params = cp.params_from(request.form.get("address"))
            if st.get("cid") != cid or not params.get("state") or params.get("state") != st.get("state"):
                return err_to(here, "That address does not belong to the sign-in started here. Click Continue with ChatGPT and try again.")
            try:
                _, msg = cp.finish(conn, params, who())
            except cp.PlanError as e:
                return err_to(here, str(e))
            session.pop("chatgpt_oauth", None)
            return back_with(here, msg)
        g = cp.connection(conn, cid)
        if not g: return err_to(here, "ChatGPT is not connected for this brand yet.")
        if what == "test":
            ok, msg = cp.test(conn, cid)
            return back_with(here, msg) if ok else err_to(here, msg)
        if what == "settings":
            slugs = [m["slug"] for m in (g["models"] or [])]
            model = request.form.get("model") if request.form.get("model") in slugs else g["model"]
            conn.execute("UPDATE chatgpt_connections SET model=%s, use_for_ai=%s, use_for_blogs=%s, updated_at=now() WHERE client_id=%s",
                         [model, bool(request.form.get("use_for_ai")), bool(request.form.get("use_for_blogs")), cid])
            return back_with(here, "Saved.")
        if what == "disconnect":
            cp.disconnect(conn, cid)
            return back_with(here, "ChatGPT disconnected. AI answers and blog drafts are back on Orbit's own access. "
                                   "To remove Orbit from the ChatGPT account too, do it in ChatGPT's settings.")
    abort(400)

def chatgpt_callback():
    """Direct return from OpenAI (once Orbit is registered): same checks as the pasted address."""
    st = session.get("chatgpt_oauth") or {}
    cid = st.get("cid")
    if not cid or request.args.get("state") != st.get("state"):
        return redirect(url_for("login", err="The ChatGPT sign-in expired. Start it again from Integrations."))
    allowed = session.get("staff_email") or (session.get("viewer_email") and cid in (session.get("viewer_clients") or []))
    if not allowed: abort(403)
    here = url_for("dash.integrations", cid=cid)
    from common import connect as staff_connect
    with staff_connect() as conn:
        try:
            _, msg = cp.finish(conn, request.args.to_dict(), who())
        except cp.PlanError as e:
            return err_to(here, str(e))
    session.pop("chatgpt_oauth", None)
    return back_with(here, msg)
