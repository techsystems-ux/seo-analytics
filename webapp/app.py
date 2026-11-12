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
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=(os.environ.get("OAUTH_REDIRECT_URI") or os.environ.get("RENDER_EXTERNAL_URL", "")).startswith("https://"))

@app.get("/healthz")
def healthz():
    return "ok"
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
<title>{{ title }} | Hovers AI Search</title><style>
:root{--red:#B20F00;--line:#E5E5E5;--grey:#F5F5F5;--mute:#5A5A5A}*{box-sizing:border-box}
body{margin:0;font:15px/1.5 Montserrat,system-ui,Arial,sans-serif;color:#000;background:#fff}
.wrap{max-width:1100px;margin:0 auto;padding:32px 20px}h1{font-size:30px;margin:0 0 6px}p.m{color:var(--mute);margin:0 0 24px}
.btn{display:inline-block;border:0;border-radius:999px;padding:11px 20px;font:600 14px Montserrat,Arial;cursor:pointer;text-decoration:none}
.red{background:var(--red);color:#fff}.blk{background:#000;color:#fff}.ln{background:#fff;color:#000;box-shadow:inset 0 0 0 1px #000}
table{width:100%;border-collapse:collapse;margin:16px 0}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:12px;text-transform:uppercase;letter-spacing:.02em;color:var(--mute)}input[type=text],select{font:inherit;padding:7px 10px;border:1px solid #000;border-radius:8px;width:100%}
.tag{font-size:11px;font-weight:600;text-transform:uppercase;border-radius:999px;padding:2px 8px;border:1px solid var(--line)}.ok{background:#000;color:#fff}.warn{border-color:var(--red);color:var(--red)}
.box{background:var(--grey);border-radius:16px;padding:20px;margin:16px 0}.err{color:var(--red);font-weight:600}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
</style></head><body><div class="wrap">
<div class="top"><strong>Hovers AI Search · Google connections{% if staff %} · <a href="/c">Brands</a>{% endif %}</strong>{% if staff %}<form method="post" action="{{ url_for('logout') }}"><input type="hidden" name="csrf" value="{{ csrf }}"><span style="color:#5A5A5A;margin-right:10px">{{ staff }}</span><button class="btn ln">Sign out</button></form>{% endif %}</div>
{{ body|safe }}</div></body></html>"""

def page(title, body_tpl, **ctx):
    body = render_template_string(body_tpl, csrf=csrf_token(), **ctx)
    return render_template_string(BASE, title=title, body=body, staff=staff(), csrf=csrf_token())

@app.get("/")
def home():
    if not staff():
        return page("Sign in", """<h1 style="margin-top:40px">Connect Google</h1>
<p class="m">Sign in with the Google account that has access to your clients' Search Console and Google Analytics. We will list every site and property it can see, and you choose which to import as clients.</p>
{% if err %}<p class="err">{{ err }}</p>{% endif %}<a class="btn red" href="{{ url_for('auth_start') }}">Sign in with Google</a>
<p class="m" style="margin-top:16px">Read-only access. We never change anything in Search Console or Analytics.</p>""", err=request.args.get("err"))
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
    return page("Connections", """<h1 style="margin-top:32px">Google connections</h1><p class="m">Each connection is a Google account whose Search Console sites and GA4 properties we can read.</p>
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
    return page("Import clients", """<h1 style="margin-top:32px">Import clients</h1>
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
