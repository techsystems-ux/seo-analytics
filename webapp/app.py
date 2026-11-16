"""Staff connect page: sign in with Google, see every Search Console site and GA4 property the account can access,
and import them as clients in one step. Run: flask --app webapp/app.py run --port 8080

Env (in addition to DATABASE_URL and the google_auth vars):
  FLASK_SECRET            random string for the session cookie
  ALLOWED_EMAIL_DOMAINS   comma list of Google account domains allowed to sign in, e.g. "hovers.in"
"""
import os, sys, secrets
from flask import Flask, request, redirect, session, url_for, render_template_string, abort
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collectors"))
from common import connect
import google_auth as ga

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024        # CSV uploads
app.secret_key = os.environ["FLASK_SECRET"]
import datetime as _dt
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True, PERMANENT_SESSION_LIFETIME=_dt.timedelta(days=14),
                  SESSION_COOKIE_SECURE=(os.environ.get("OAUTH_REDIRECT_URI") or os.environ.get("RENDER_EXTERNAL_URL", "")).startswith("https://"))

@app.before_request
def _one_address():
    """Online, every other address of the deployment (old project names, the long team address) goes to APP_HOST, so a
    Google sign-in that starts on one address never comes back to another (the session cookie belongs to one host)."""
    host = os.environ.get("APP_HOST")
    if host and os.environ.get("VERCEL_ENV") == "production" and request.host != host and request.path != "/healthz":
        return redirect(f"https://{host}{request.full_path.rstrip('?')}", 308)

@app.get("/healthz")
def healthz():
    return "ok"

def _cron_ok():
    """The nightly trigger (GitHub Actions) sends CRON_TOKEN as a bearer token. Without CRON_TOKEN set, these are off."""
    tok = os.environ.get("CRON_TOKEN", "")
    got = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(tok) and secrets.compare_digest(tok, got)

@app.post("/cron/daily")
def cron_daily():
    """Starts the nightly pull for every brand, unless one is already running. Same caps as a pull from the screen."""
    if not _cron_ok(): abort(403)
    import dashboard
    if dashboard.jobs_running(): return {"started": False, "running": True}
    dashboard.run_daily([])
    return {"started": True, "running": True}

@app.get("/cron/status")
def cron_status():
    if not _cron_ok(): abort(403)
    import dashboard
    return {"running": bool(dashboard.jobs_running())}
ALLOWED = [d.strip().lower() for d in os.environ.get("ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()]

# ---------------------------------------------------------------- helpers
def staff():
    return session.get("staff_email")

def require_staff():
    if not staff(): abort(redirect(url_for("home")))

def csrf_token():
    if "csrf" not in session: session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]

def check_csrf():
    if not request.form.get("csrf") or request.form.get("csrf") != session.get("csrf"): abort(400, "Form expired. Reload the page and try again.")

def save_discovery(conn, connection_id, sites, props):
    with conn.transaction():
        conn.execute("DELETE FROM google_gsc_sites WHERE connection_id=%s", [connection_id])
        conn.execute("DELETE FROM google_ga4_properties WHERE connection_id=%s", [connection_id])
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO google_gsc_sites (connection_id, site_url, permission_level) VALUES (%s,%s,%s)",
                            [(connection_id, s, p) for s, p in sites])
            cur.executemany("INSERT INTO google_ga4_properties (connection_id, property_id, display_name, account_name, web_uris) VALUES (%s,%s,%s,%s,%s)",
                            [(connection_id, p["property_id"], p["display_name"], p["account_name"], p["web_uris"]) for p in props])
        conn.execute("UPDATE google_connections SET last_discovered_at=now(), status='active', last_error=NULL WHERE connection_id=%s", [connection_id])

def discover(conn, connection_id, access_token):
    sites, props = ga.list_gsc_sites(access_token), ga.list_ga4_properties(access_token)
    save_discovery(conn, connection_id, sites, props)
    return len(sites), len(props)

def existing_clients(conn):
    rows = conn.execute("SELECT c.client_id, c.name, c.domain, i.gsc_site_url FROM clients c LEFT JOIN client_integrations i USING (client_id)").fetchall()
    return {ga.domain_of(d): (cid, name, site) for cid, name, d, site in rows}

# ---------------------------------------------------------------- pages
BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · Orbit by Hovers</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;700&family=Montserrat:wght@600;700&display=swap" rel="stylesheet"><style>
:root{--ink:#0F0F0F;--red:#B20F00;--grey:#6E6E6E;--mid:#A9A9A9;--surface:#F5F5F5;--tint:#FBEDEE;--hair:#E4E1DE;
  --fh:'Montserrat',Arial,sans-serif;--fb:'Inter',Calibri,Arial,sans-serif;--fn:'JetBrains Mono','Courier New',monospace}
*{box-sizing:border-box}body{margin:0;font:400 14px/1.55 var(--fb);color:var(--ink);background:#fff;-webkit-font-smoothing:antialiased}
a{color:var(--ink)}
.bar0{height:60px;border-bottom:1px solid var(--hair);display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 32px;font:600 13px var(--fh)}
.bar0 .crumb{color:var(--grey)}.bar0 .crumb b{color:var(--ink)}.bar0 .who{display:flex;align-items:center;gap:10px}
.chip{font:500 11px var(--fn);color:var(--grey);border:1px solid var(--hair);border-radius:3px;padding:4px 9px}
.wrap{max-width:1100px;margin:0 auto;padding:40px 32px 32px;display:flex;flex-direction:column;gap:16px}
.kicker{display:block;font:700 10.5px var(--fh);letter-spacing:.18em;text-transform:uppercase;color:var(--grey)}
.kicker::after{content:"";display:block;width:28px;height:2px;background:var(--ink);margin-top:12px}
h1{font:700 1.75rem/1.2 var(--fh);letter-spacing:-.01em;margin:0}.wrap>.btn,.wrap>form>.btn{align-self:flex-start}p.m{color:var(--grey);margin:0;max-width:720px}
.btn{appearance:none;display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--ink);background:#fff;color:var(--ink);border-radius:3px;padding:8px 14px;font:600 12.5px var(--fh);cursor:pointer;text-decoration:none;white-space:nowrap}
.btn:hover{background:var(--ink);color:#fff}.red,.blk{background:var(--ink);color:#fff}.red:hover,.blk:hover{background:#333;border-color:#333}.ln{background:#fff;color:var(--ink)}
.card,table{border:1px solid var(--hair);border-radius:6px;background:#fff}
table{width:100%;border-collapse:separate;border-spacing:0;overflow:hidden}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #EFEDEA;vertical-align:middle}
tr:last-child td{border-bottom:0}th{font:600 11px var(--fh);color:var(--grey);background:#fff}
input[type=text],select{font:inherit;padding:8px 10px;border:1px solid var(--hair);border-radius:3px;width:100%;background:#fff}input:focus,select:focus{outline:2px solid var(--ink);outline-offset:-1px}
.tag{font:500 11px var(--fn);border-radius:3px;padding:2px 7px;border:1px solid var(--hair);white-space:nowrap}.ok{background:var(--ink);color:#fff;border-color:var(--ink)}.warn{border-color:var(--red);color:var(--red);background:var(--tint)}
.box{background:var(--tint);border-left:3px solid var(--red);border-radius:3px;padding:14px 18px}.err{color:var(--red);font-weight:600}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.foot{margin-top:24px;padding-top:18px;border-top:1px solid var(--hair);display:flex;justify-content:space-between;font:700 10.5px var(--fh);letter-spacing:.14em;text-transform:uppercase;color:var(--grey)}
@media (max-width:700px){.bar0,.wrap{padding-left:16px;padding-right:16px}.bar0 .chip{display:none}}
</style></head><body>
<header class="bar0"><span class="crumb">Orbit / <b>{{ title }}</b>{% if staff %} · <a href="/c">Brands</a>{% endif %}</span>
{% if staff %}<form class="who" method="post" action="{{ url_for('logout') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><span class="chip">{{ staff }}</span><button class="btn ln">Sign out</button></form>{% else %}<span class="chip">Read-only Google access</span>{% endif %}</header>
<div class="wrap"><span class="kicker">Orbit · Setup</span>
{{ body|safe }}<footer class="foot"><span>Orbit · by Hovers®</span><span>Confidential</span></footer></div></body></html>"""

def page(title, body_tpl, **ctx):
    body = render_template_string(body_tpl, csrf=csrf_token(), **ctx)
    return render_template_string(BASE, title=title, body=body, staff=staff(), csrf=csrf_token())

@app.get("/")
def home():
    if not staff() and session.get("viewer_email"): return redirect(url_for("portal.home"))
    if not staff():
        import portal
        return portal.login_page("client" if request.args.get("as") == "client" else "team", err=request.args.get("err"))
    return redirect(url_for("dash.clients"))

@app.get("/connections")
def connections():
    require_staff()
    with connect() as conn:
        conns = conn.execute("""SELECT g.connection_id, g.google_email, g.status, g.last_error, g.last_discovered_at,
            (SELECT count(*) FROM google_gsc_sites s WHERE s.connection_id=g.connection_id),
            (SELECT count(*) FROM google_ga4_properties p WHERE p.connection_id=g.connection_id),
            (SELECT count(*) FROM client_integrations i WHERE i.google_connection_id=g.connection_id)
            FROM google_connections g ORDER BY g.connected_at""").fetchall()
    return page("Connections", """<h1>Google connections</h1><p class="m">Each connection is a Google account whose Search Console sites and GA4 properties we can read.</p>
{% if msg %}<div class="box">{{ msg }}</div>{% endif %}
<table><tr><th>Google account</th><th>Status</th><th>Search Console sites</th><th>GA4 properties</th><th>Clients linked</th><th>Last listed</th><th></th></tr>
{% for c in conns %}<tr><td>{{ c[1] }}</td><td>{% if c[2]=='active' %}<span class="tag ok">Active</span>{% else %}<span class="tag warn">Reconnect</span>{% endif %}</td>
<td>{{ c[5] }}</td><td>{{ c[6] }}</td><td>{{ c[7] }}</td><td>{{ c[4].strftime('%d %b %Y, %H:%M') if c[4] else 'Never' }}</td>
<td style="white-space:nowrap"><a class="btn blk" href="{{ url_for('import_page', cid=c[0]) }}">Import clients</a>
<form method="post" action="{{ url_for('refresh', cid=c[0]) }}" style="display:inline"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn ln">Refresh list</button></form></td></tr>
{% if c[3] %}<tr><td colspan="7" class="err">{{ c[3] }}</td></tr>{% endif %}{% endfor %}</table>
<a class="btn red" href="{{ url_for('auth_start') }}">Connect another Google account</a>""", conns=conns, msg=request.args.get("msg"))

@app.get("/auth/google")
def auth_start():
    session["oauth_state"] = secrets.token_urlsafe(24)
    return redirect(ga.auth_url(session["oauth_state"]))

@app.get("/auth/google/callback")
def auth_callback():
    if request.args.get("error"):
        return redirect(url_for("home", err="Google sign-in was cancelled."))
    if not request.args.get("state") or request.args.get("state") != session.pop("oauth_state", None):
        return redirect(url_for("home", err="Sign-in expired. Please try again."))
    try:
        tok = ga.exchange_code(request.args["code"])
        email = ga.userinfo(tok["access_token"]).get("email", "").lower()
    except (RuntimeError, PermissionError) as e:
        return redirect(url_for("home", err=str(e)))
    if not email or (ALLOWED and email.split("@")[-1] not in ALLOWED):
        return redirect(url_for("home", err=f"{email or 'This account'} is not allowed to connect. Use a Hovers Google account."))
    with connect() as conn:
        cid = conn.execute("""INSERT INTO google_connections (google_email, refresh_token_enc, scopes) VALUES (%s,%s,%s)
            ON CONFLICT (google_email) DO UPDATE SET refresh_token_enc=EXCLUDED.refresh_token_enc, scopes=EXCLUDED.scopes, status='active', last_error=NULL
            RETURNING connection_id""", [email, ga.encrypt(tok["refresh_token"]), tok.get("scope", "")]).fetchone()[0]
        try:
            discover(conn, cid, tok["access_token"])
        except RuntimeError as e:
            conn.execute("UPDATE google_connections SET last_error=%s WHERE connection_id=%s", [str(e)[:500], cid])
    session["staff_email"] = email
    return redirect(url_for("import_page", cid=cid))

@app.post("/connections/<cid>/refresh")
def refresh(cid):
    require_staff(); check_csrf()
    with connect() as conn:
        row = conn.execute("SELECT refresh_token_enc FROM google_connections WHERE connection_id=%s", [cid]).fetchone()
        if not row: abort(404)
        try:
            n_sites, n_props = discover(conn, cid, ga.refresh_access_token(ga.decrypt(row[0])))
            msg = f"Found {n_sites} Search Console sites and {n_props} GA4 properties."
        except ga.RevokedError as e:
            conn.execute("UPDATE google_connections SET status='revoked', last_error=%s WHERE connection_id=%s", [str(e), cid]); msg = str(e)
        except RuntimeError as e:
            conn.execute("UPDATE google_connections SET last_error=%s WHERE connection_id=%s", [str(e)[:500], cid]); msg = str(e)
    return redirect(url_for("connections", msg=msg))

@app.get("/import/<cid>")
def import_page(cid):
    require_staff()
    with connect() as conn:
        g = conn.execute("SELECT google_email, last_error FROM google_connections WHERE connection_id=%s", [cid]).fetchone()
        if not g: abort(404)
        sites = conn.execute("SELECT site_url, permission_level FROM google_gsc_sites WHERE connection_id=%s", [cid]).fetchall()
        props = [{"property_id": p, "display_name": n, "account_name": a, "web_uris": u} for p, n, a, u in
                 conn.execute("SELECT property_id, display_name, account_name, web_uris FROM google_ga4_properties WHERE connection_id=%s ORDER BY account_name, display_name", [cid]).fetchall()]
        plan = ga.import_plan(sites, props, existing_clients(conn))
    return page("Import clients", """<h1>Import clients</h1>
<p class="m">From {{ email }}. Tick the sites to import. Each one becomes a client, with its GA4 property matched by website address. Check the matches before importing.</p>
{% if err %}<p class="err">{{ err }}</p>{% endif %}
{% if not plan %}<div class="box">This account has no verified Search Console sites. Check you signed in with the right Google account.</div>{% else %}
<form method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
<p><button type="button" class="btn ln" onclick="document.querySelectorAll('input[name=pick]:not(:disabled)').forEach(c=>c.checked=true)">Select all</button>
<button type="button" class="btn ln" onclick="document.querySelectorAll('input[name=pick]').forEach(c=>c.checked=false)">Clear</button></p>
<table><tr><th></th><th>Search Console site</th><th>Access</th><th>Client name</th><th>GA4 property</th><th></th></tr>
{% for r in plan %}<tr><td><input type="checkbox" name="pick" value="{{ loop.index0 }}" aria-label="Import {{ r.domain }}" {% if r.already_linked %}disabled{% elif not r.existing_client_id %}checked{% endif %}>
<input type="hidden" name="site_{{ loop.index0 }}" value="{{ r.site_url }}"></td>
<td>{{ r.site_url }}</td><td><span class="tag">{{ {'siteOwner':'Owner','siteFullUser':'Full','siteRestrictedUser':'Restricted'}.get(r.permission, r.permission) }}</span></td>
<td><input type="text" name="name_{{ loop.index0 }}" value="{{ r.name }}" aria-label="Client name for {{ r.domain }}"></td>
<td><select name="ga4_{{ loop.index0 }}" aria-label="GA4 property for {{ r.domain }}"><option value="">No GA4 property</option>
{% for p in props %}<option value="{{ p.property_id }}" {% if p.property_id==r.ga4 %}selected{% endif %}>{{ p.display_name }} ({{ p.property_id }}){% if p.account_name %} · {{ p.account_name }}{% endif %}</option>{% endfor %}</select></td>
<td>{% if r.already_linked %}<span class="tag ok">Imported</span>{% elif r.existing_client_id %}<span class="tag">Links to existing client</span>{% elif r.ga4 %}<span class="tag">GA4 matched</span>{% else %}<span class="tag warn">No GA4 match</span>{% endif %}</td></tr>{% endfor %}</table>
<button class="btn red">Import selected</button> <a class="btn ln" href="{{ url_for('connections') }}">Back</a></form>{% endif %}""",
        email=g[0], err=g[1], plan=plan, props=props)

@app.post("/import/<cid>")
def import_submit(cid):
    require_staff(); check_csrf()
    created = linked = 0
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM google_connections WHERE connection_id=%s", [cid]).fetchone(): abort(404)
        allowed_sites = {s for (s,) in conn.execute("SELECT site_url FROM google_gsc_sites WHERE connection_id=%s", [cid]).fetchall()}
        allowed_props = {p for (p,) in conn.execute("SELECT property_id FROM google_ga4_properties WHERE connection_id=%s", [cid]).fetchall()}
        existing = existing_clients(conn)
        with conn.transaction():
            for i in request.form.getlist("pick"):
                site = request.form.get(f"site_{i}", "")
                name = (request.form.get(f"name_{i}") or "").strip()[:120]
                ga4 = request.form.get(f"ga4_{i}") or None
                if site not in allowed_sites or (ga4 and ga4 not in allowed_props):
                    abort(400, "That site or property is not available on this connection.")
                d = ga.domain_of(site)
                ex = existing.get(d)
                if ex:
                    client_id = ex[0]; linked += 1
                else:
                    client_id = conn.execute("INSERT INTO clients (name, domain) VALUES (%s,%s) RETURNING client_id", [name or ga.suggest_name(d), d]).fetchone()[0]; created += 1
                conn.execute("""INSERT INTO client_integrations (client_id, gsc_site_url, ga4_property_id, google_connection_id) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (client_id) DO UPDATE SET gsc_site_url=EXCLUDED.gsc_site_url,
                      ga4_property_id=coalesce(EXCLUDED.ga4_property_id, client_integrations.ga4_property_id), google_connection_id=EXCLUDED.google_connection_id""",
                             [client_id, site, ga4, cid])
    return redirect(url_for("connections", msg=f"Imported {created} new clients and linked {linked} existing ones. Collectors pick them up on the next run."))

@app.post("/logout")
def logout():
    check_csrf(); session.clear()
    return redirect(url_for("home"))

from dashboard import bp as dashboard_bp                   # Overview, Search, Competitors, AI visibility, Pages, Settings
app.register_blueprint(dashboard_bp)
from portal import bp as portal_bp                         # client sign-in: read-only view of their own brand
app.register_blueprint(portal_bp)
