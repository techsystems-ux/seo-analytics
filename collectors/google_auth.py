"""Google sign-in (OAuth 2.0) for Search Console and GA4.

One staff sign-in replaces adding a service account to every client property:
  1. A staff member signs in with a Google account that already has access to client properties.
  2. We keep that account's refresh token, encrypted with TOKEN_KEY.
  3. We list every Search Console site and GA4 property it can see, and staff import them as clients.
  4. Collectors use the stored token to pull data for each client.

Env: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URI, TOKEN_KEY (Fernet key).
"""
import os, re
from urllib.parse import urlencode, quote
import requests
from cryptography.fernet import Fernet

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
SCOPES = ["openid", "email", GSC_SCOPE, GA4_SCOPE]
GSC_SITES = "https://www.googleapis.com/webmasters/v3/sites"
GA4_SUMMARIES = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
GA4_STREAMS = "https://analyticsadmin.googleapis.com/v1beta/properties/{pid}/dataStreams"

http = requests            # swapped for a fake in tests

# ---------- token encryption ----------
def _fernet():
    key = os.environ["TOKEN_KEY"].strip()
    try:
        return Fernet(key.encode())
    except ValueError:                       # a generated server secret that is not a Fernet key: derive one from it
        import base64, hashlib
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))

def redirect_uri():
    """OAUTH_REDIRECT_URI, or on Render the service's own address."""
    return os.environ.get("OAUTH_REDIRECT_URI") or os.environ["RENDER_EXTERNAL_URL"].rstrip("/") + "/auth/google/callback"
def encrypt(token): return _fernet().encrypt(token.encode())
def decrypt(blob): return _fernet().decrypt(bytes(blob)).decode()

# ---------- sign-in ----------
def auth_url(state):
    return AUTH_URI + "?" + urlencode({
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"], "redirect_uri": redirect_uri(),
        "response_type": "code", "scope": " ".join(SCOPES), "state": state,
        "access_type": "offline",          # ask for a refresh token
        "prompt": "consent",               # always return one, even on a repeat sign-in
        "include_granted_scopes": "true"})

def exchange_code(code):
    r = http.post(TOKEN_URI, data={"code": code, "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
                                    "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
                                    "redirect_uri": redirect_uri(), "grant_type": "authorization_code"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Token exchange failed ({r.status_code}): {r.text[:200]}")
    tok = r.json()
    granted = set(tok.get("scope", "").split())
    missing = [s for s in (GSC_SCOPE, GA4_SCOPE) if s not in granted]
    if missing:
        raise PermissionError("Access to " + " and ".join("Search Console" if s == GSC_SCOPE else "Google Analytics" for s in missing)
                              + " was not granted. Sign in again and tick every box on the consent screen.")
    if not tok.get("refresh_token"):
        raise RuntimeError("Google did not return a refresh token. Remove the app's access at myaccount.google.com/permissions and sign in again.")
    return tok

def refresh_access_token(refresh_token):
    r = http.post(TOKEN_URI, data={"refresh_token": refresh_token, "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
                                    "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"], "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        try: err = r.json().get("error")
        except ValueError: err = r.text[:100]
        raise RevokedError(f"Google rejected the stored sign-in ({err}). Reconnect the account.") if err == "invalid_grant" \
            else RuntimeError(f"Token refresh failed ({r.status_code}): {r.text[:200]}")
    return r.json()["access_token"]

class RevokedError(RuntimeError): pass

def userinfo(access_token):
    r = http.get(USERINFO_URI, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    r.raise_for_status()
    return r.json()

# ---------- discovery ----------
def _get(access_token, url, params=None):
    r = http.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params or {}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{url.split('/')[2]} {r.status_code}: {r.text[:200]}")
    return r.json()

def list_gsc_sites(access_token):
    """Verified Search Console sites only (unverified entries cannot return data)."""
    data = _get(access_token, GSC_SITES)
    return [(s["siteUrl"], s["permissionLevel"]) for s in data.get("siteEntry", []) if s.get("permissionLevel") != "siteUnverifiedUser"]

def list_ga4_properties(access_token):
    """Every GA4 property across every account, with its web stream URLs."""
    props, token = [], None
    while True:
        data = _get(access_token, GA4_SUMMARIES, {"pageSize": 200, **({"pageToken": token} if token else {})})
        for acc in data.get("accountSummaries", []):
            for p in acc.get("propertySummaries", []):
                props.append({"property_id": p["property"].split("/")[-1], "display_name": p.get("displayName", ""),
                              "account_name": acc.get("displayName", "")})
        token = data.get("nextPageToken")
        if not token: break
    for p in props:
        try:
            streams = _get(access_token, GA4_STREAMS.format(pid=p["property_id"])).get("dataStreams", [])
            p["web_uris"] = [s["webStreamData"]["defaultUri"] for s in streams if s.get("webStreamData", {}).get("defaultUri")]
        except RuntimeError:
            p["web_uris"] = []          # no stream access: still listed, just not auto-matched
    return props

# ---------- matching ----------
def domain_of(url_or_site):
    """'sc-domain:example.com', 'https://www.example.com/', 'example.com' -> 'example.com'"""
    s = (url_or_site or "").strip().lower()
    s = re.sub(r"^sc-domain:", "", s); s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0].split(":")[0]
    return s[4:] if s.startswith("www.") else s

def suggest_name(domain):
    stem = domain.split(".")[0].replace("-", " ").strip()
    return stem.title() if stem else domain

def match_ga4(site_url, properties):
    """GA4 property whose web stream domain equals the site's domain (or is a subdomain of it)."""
    d = domain_of(site_url)
    for p in properties:
        for u in p.get("web_uris", []):
            pd = domain_of(u)
            if pd == d or pd.endswith("." + d):
                return p["property_id"]
    for p in properties:                                   # fallback: domain appears in the property name
        if d and d in p.get("display_name", "").lower():
            return p["property_id"]
    return None

def import_plan(sites, properties, existing):
    """One row per Search Console site: suggested client name, matched GA4 property, and any existing client to link.
    `existing` maps domain -> (client_id, name, current gsc_site_url)."""
    rows = []
    for site_url, perm in sorted(sites, key=lambda s: domain_of(s[0])):
        d = domain_of(site_url)
        ex = existing.get(d)
        rows.append({"site_url": site_url, "permission": perm, "domain": d,
                     "name": ex[1] if ex else suggest_name(d),
                     "ga4": match_ga4(site_url, properties),
                     "existing_client_id": ex[0] if ex else None,
                     "already_linked": bool(ex and ex[2] == site_url)})
    return rows

# ---------- sessions for collectors ----------
def oauth_session(conn, connection_id, scope):
    """AuthorizedSession built from a stored connection. Marks the connection revoked if Google rejects it."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    row = conn.execute("SELECT refresh_token_enc, status, google_email FROM google_connections WHERE connection_id=%s", [connection_id]).fetchone()
    if not row: raise RuntimeError("Google connection not found")
    if row[1] != "active": raise RevokedError(f"Google sign-in for {row[2]} needs reconnecting")
    creds = Credentials(token=None, refresh_token=decrypt(row[0]), token_uri=TOKEN_URI, scopes=[scope],
                        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"], client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"])
    try:
        creds.refresh(Request())
    except RefreshError as e:
        conn.execute("UPDATE google_connections SET status='revoked', last_error=%s WHERE connection_id=%s", [str(e)[:500], connection_id])
        raise RevokedError(f"Google sign-in for {row[2]} was rejected; reconnect it") from e
    return AuthorizedSession(creds)

class SessionPool:
    """One session per Google connection per run; service account for clients without a connection."""
    def __init__(self, conn, scope):
        self.conn, self.scope, self.cache = conn, scope, {}
    def get(self, connection_id):
        key = connection_id or "service-account"
        if key not in self.cache:
            if connection_id:
                self.cache[key] = oauth_session(self.conn, connection_id, self.scope)
            else:
                from common import google_session
                if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                    raise RuntimeError("No Google sign-in linked to this client and no service account configured")
                self.cache[key] = google_session(self.scope)
        return self.cache[key]
