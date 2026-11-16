"""Client sign-in. People a brand's Settings, Client access, lists can sign in with Google (name and email only, through a
separate OAuth client, so no Search Console or Analytics access is ever asked of them) or with a one-time emailed link,
then view that brand's dashboards read-only (see dashboard._gate and dashboard.connect).

Env: CLIENT_OAUTH_CLIENT_ID / CLIENT_OAUTH_CLIENT_SECRET (Google sign-in for clients; redirect URI <site>/portal/auth/google/callback)
     RESEND_API_KEY, MAIL_FROM (emailed links, e.g. "Hovers <reports@hovers.in>"; the sending domain must be verified in Resend)"""
import datetime as dt, hashlib, os, secrets
from urllib.parse import urlencode
import requests
from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from common import connect

bp = Blueprint("portal", __name__)
AUTH, TOKEN, USERINFO = "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token", "https://openidconnect.googleapis.com/v1/userinfo"
LINK_MINUTES, LINKS_PER_HOUR = 15, 5
GENERIC = "If this email has access, a sign-in link is on its way. It works once, for 15 minutes."

def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]

def page(err=None, msg=None):
    return render_template("portal_login.html", err=err, msg=msg, csrf=csrf(),
                           google=bool(os.environ.get("CLIENT_OAUTH_CLIENT_ID")), email=bool(os.environ.get("RESEND_API_KEY")))

def brands_for(email):
    with connect() as conn:                       # staff-level read, only to find which brands this email may view
        return [str(r[0]) for r in conn.execute("SELECT client_id FROM client_viewers WHERE email=%s", [email])]

def sign_in(email):
    """Start a viewer session for the brands this email is listed on. Never a staff session."""
    email = email.strip().lower()
    ids = brands_for(email)
    if not ids: return False
    session.pop("staff_email", None)
    session["viewer_email"], session["viewer_clients"] = email, ids
    session.permanent = True
    with connect() as conn:
        conn.execute("UPDATE client_viewers SET last_login=now() WHERE email=%s", [email])
    return True

def redirect_uri():
    return request.host_url.rstrip("/") + "/portal/auth/google/callback"

@bp.get("/portal/login")
def login():
    if session.get("viewer_email"): return redirect(url_for("portal.home"))
    return page(err=request.args.get("err"), msg=request.args.get("msg"))

@bp.get("/portal/auth/google")
def google_start():
    if not os.environ.get("CLIENT_OAUTH_CLIENT_ID"): abort(404)
    session["portal_state"] = secrets.token_urlsafe(24)
    return redirect(AUTH + "?" + urlencode({"client_id": os.environ["CLIENT_OAUTH_CLIENT_ID"], "redirect_uri": redirect_uri(),
                                             "response_type": "code", "scope": "openid email profile", "state": session["portal_state"],
                                             "prompt": "select_account"}))

@bp.get("/portal/auth/google/callback")
def google_callback():
    if request.args.get("error"): return redirect(url_for("portal.login", err="Google sign-in was cancelled."))
    if not request.args.get("state") or request.args.get("state") != session.pop("portal_state", None):
        return redirect(url_for("portal.login", err="Sign-in expired. Please try again."))
    r = requests.post(TOKEN, data={"code": request.args.get("code", ""), "client_id": os.environ["CLIENT_OAUTH_CLIENT_ID"],
                                   "client_secret": os.environ["CLIENT_OAUTH_CLIENT_SECRET"], "redirect_uri": redirect_uri(),
                                   "grant_type": "authorization_code"}, timeout=30)
    if r.status_code != 200: return redirect(url_for("portal.login", err="Google sign-in failed. Please try again."))
    info = requests.get(USERINFO, headers={"Authorization": "Bearer " + r.json()["access_token"]}, timeout=30).json()
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified"): return redirect(url_for("portal.login", err="Google did not confirm this email address."))
    if not sign_in(email):
        return redirect(url_for("portal.login", err=f"{email} has no access yet. Ask your Hovers team to add it."))
    return redirect(url_for("portal.home"))

@bp.post("/portal/login")
def email_start():
    if not request.form.get("csrf") or request.form.get("csrf") != session.get("csrf"): abort(400, "Form expired. Reload the page and try again.")
    if not os.environ.get("RESEND_API_KEY"): abort(404)
    email = request.form.get("email", "").strip().lower()
    if "@" not in email or len(email) > 254: return page(err="Enter your work email.")
    with connect() as conn:
        listed = conn.execute("SELECT 1 FROM client_viewers WHERE email=%s LIMIT 1", [email]).fetchone()
        recent = conn.execute("SELECT count(*) FROM login_links WHERE email=%s AND created_at > now() - interval '1 hour'", [email]).fetchone()[0]
        if listed and recent < LINKS_PER_HOUR:
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO login_links (token_hash, email, expires_at) VALUES (%s,%s,now() + %s * interval '1 minute')",
                         [hashlib.sha256(token.encode()).hexdigest(), email, LINK_MINUTES])
            send_link(email, request.host_url.rstrip("/") + url_for("portal.email_link", t=token))
    return page(msg=GENERIC)                      # same answer either way, so nobody can test which emails have access

def send_link(email, link):
    body = (f"<p>Use this link to sign in to your SEO and AI search dashboard. It works once, for {LINK_MINUTES} minutes.</p>"
            f"<p><a href=\"{link}\">Sign in</a></p><p style=\"color:#6E6E6E\">If you did not ask for this, ignore this email.</p>")
    try:
        requests.post("https://api.resend.com/emails", timeout=20, headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"]},
                      json={"from": os.environ.get("MAIL_FROM", "Hovers <onboarding@resend.dev>"), "to": [email],
                            "subject": "Your sign-in link", "html": body})
    except requests.RequestException:
        pass

@bp.get("/portal/auth/link")
def email_link():
    h = hashlib.sha256(request.args.get("t", "").encode()).hexdigest()
    with connect() as conn:
        row = conn.execute("""UPDATE login_links SET used_at=now() WHERE token_hash=%s AND used_at IS NULL AND expires_at > now()
                              RETURNING email""", [h]).fetchone()
    if not row: return redirect(url_for("portal.login", err="This link has expired or was already used. Ask for a new one."))
    if not sign_in(row[0]): return redirect(url_for("portal.login", err="This email no longer has access."))
    return redirect(url_for("portal.home"))

@bp.get("/portal")
def home():
    ids = session.get("viewer_clients")
    if not ids: return redirect(url_for("portal.login"))
    ids = brands_for(session["viewer_email"])     # access removed since sign-in takes effect at once
    session["viewer_clients"] = ids
    if not ids: session.clear(); return redirect(url_for("portal.login", err="This email no longer has access."))
    if len(ids) == 1: return redirect(url_for("dash.overview", cid=ids[0]))
    with connect() as conn:
        brands = conn.execute("SELECT client_id, name, domain FROM clients WHERE client_id = ANY(%s::uuid[]) ORDER BY name", [ids]).fetchall()
    return render_template("portal_brands.html", brands=brands, csrf=csrf(), email=session["viewer_email"])

@bp.post("/portal/logout")
def logout():
    session.pop("viewer_email", None); session.pop("viewer_clients", None)
    return redirect(url_for("portal.login", msg="Signed out."))
