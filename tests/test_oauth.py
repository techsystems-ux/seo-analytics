"""Google sign-in flow with a fake Google. Fixtures are SYNTHETIC, shaped like the documented OAuth,
Search Console sites.list and GA4 Admin API responses. Needs DATABASE_URL (aeo_app role) and migration 004."""
import os, sys, re, json
from urllib.parse import urlparse, parse_qs
from cryptography.fernet import Fernet
os.environ.update(GOOGLE_OAUTH_CLIENT_ID="test-client.apps.googleusercontent.com", GOOGLE_OAUTH_CLIENT_SECRET="test-secret",
                  OAUTH_REDIRECT_URI="http://localhost:8080/auth/google/callback", TOKEN_KEY=Fernet.generate_key().decode(),
                  FLASK_SECRET="test", ALLOWED_EMAIL_DOMAINS="hovers.in")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "collectors")); sys.path.insert(0, os.path.join(ROOT, "webapp"))
import google_auth as ga, gsc, ga4
from common import connect
import app as webapp

GSC, GA4 = ga.GSC_SCOPE, ga.GA4_SCOPE
class R:
    def __init__(self, j, code=200): self._j, self.status_code, self.text, self.headers = j, code, json.dumps(j), {"content-type": "application/json"}
    def json(self): return self._j
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(self.status_code)
class FakeGoogle:
    def __init__(self, email="seo@hovers.in", scope=f"openid email {GSC} {GA4}", refresh_ok=True):
        self.email, self.scope, self.refresh_ok, self.calls = email, scope, refresh_ok, []
    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        if data.get("grant_type") == "authorization_code":
            return R({"access_token": "at-1", "refresh_token": "rt-secret-1", "scope": self.scope, "expires_in": 3599})
        if data.get("grant_type") == "refresh_token":
            return R({"access_token": "at-2"}) if self.refresh_ok else R({"error": "invalid_grant"}, 400)
    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, params)); assert headers["Authorization"].startswith("Bearer ")
        if url == ga.USERINFO_URI: return R({"email": self.email})
        if url == ga.GSC_SITES: return R({"siteEntry": [
            {"siteUrl": "sc-domain:gargi.shop", "permissionLevel": "siteOwner"},
            {"siteUrl": "https://www.rightkind.in/", "permissionLevel": "siteFullUser"},
            {"siteUrl": "https://zevana.co/", "permissionLevel": "siteRestrictedUser"},
            {"siteUrl": "https://unverified.example/", "permissionLevel": "siteUnverifiedUser"}]})
        if url == ga.GA4_SUMMARIES:
            if not (params or {}).get("pageToken"):
                return R({"accountSummaries": [{"account": "accounts/1", "displayName": "Gargi", "propertySummaries": [{"property": "properties/111", "displayName": "Gargi Web"}]}], "nextPageToken": "p2"})
            return R({"accountSummaries": [{"account": "accounts/2", "displayName": "Right Kind", "propertySummaries": [{"property": "properties/222", "displayName": "RK - GA4"}, {"property": "properties/333", "displayName": "Old app"}]}]})
        m = re.search(r"properties/(\d+)/dataStreams", url)
        if m:
            uris = {"111": "https://www.gargi.shop", "222": "https://rightkind.in/", "333": None}[m.group(1)]
            if m.group(1) == "333": return R({"error": "denied"}, 403)
            return R({"dataStreams": [{"type": "WEB_DATA_STREAM", "webStreamData": {"defaultUri": uris}}]})
        raise AssertionError("unexpected GET " + url)

# ---- pure helpers ----
assert ga.domain_of("sc-domain:Gargi.shop") == "gargi.shop" and ga.domain_of("https://www.rightkind.in/") == "rightkind.in" and ga.domain_of("http://shop.x.com:8080/a") == "shop.x.com"
props = [{"property_id": "111", "display_name": "Gargi Web", "web_uris": ["https://www.gargi.shop"]}, {"property_id": "9", "display_name": "zevana.co main", "web_uris": []}]
assert ga.match_ga4("sc-domain:gargi.shop", props) == "111" and ga.match_ga4("https://zevana.co/", props) == "9" and ga.match_ga4("https://none.in/", props) is None
assert ga.suggest_name("right-kind.in") == "Right Kind"
q = parse_qs(urlparse(ga.auth_url("st8")).query)
assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and q["state"] == ["st8"] and GSC in q["scope"][0] and GA4 in q["scope"][0]
assert ga.decrypt(ga.encrypt("abc")) == "abc"
print("helpers OK")

# ---- missing consent box -> clear error ----
ga.http = FakeGoogle(scope=f"openid email {GSC}")
try: ga.exchange_code("c"); raise SystemExit("should fail")
except PermissionError as e: assert "Google Analytics" in str(e)
print("missing-scope error OK")

# ---- clean slate ----
with connect() as conn:
    conn.execute("DELETE FROM clients WHERE domain IN ('gargi.shop','rightkind.in','zevana.co')")
    conn.execute("DELETE FROM google_connections")
    conn.execute("INSERT INTO clients (name, domain) VALUES ('Right Kind Protein', 'rightkind.in')")   # existing client should be linked, not duplicated

c = webapp.app.test_client()
# ---- disallowed domain ----
ga.http = FakeGoogle(email="someone@gmail.com")
r = c.get("/auth/google"); st = parse_qs(urlparse(r.headers["Location"]).query)["state"][0]
r = c.get(f"/auth/google/callback?state={st}&code=x"); assert "not+allowed" in r.headers["Location"] or "not%20allowed" in r.headers["Location"], r.headers["Location"]
# ---- state mismatch ----
r = c.get("/auth/google/callback?state=forged&code=x"); assert "expired" in r.headers["Location"]
print("sign-in guards OK")

# ---- happy path ----
ga.http = fake = FakeGoogle()
r = c.get("/auth/google"); assert r.status_code == 302 and r.headers["Location"].startswith(ga.AUTH_URI)
st = parse_qs(urlparse(r.headers["Location"]).query)["state"][0]
r = c.get(f"/auth/google/callback?state={st}&code=abc"); assert r.status_code == 302 and "/import/" in r.headers["Location"], r.headers
cid = r.headers["Location"].rstrip("/").split("/")[-1]
with connect() as conn:
    enc, email = conn.execute("SELECT refresh_token_enc, google_email FROM google_connections WHERE connection_id=%s", [cid]).fetchone()
    assert email == "seo@hovers.in" and b"rt-secret-1" not in bytes(enc) and ga.decrypt(enc) == "rt-secret-1"
    sites = conn.execute("SELECT site_url FROM google_gsc_sites WHERE connection_id=%s ORDER BY 1", [cid]).fetchall()
    assert [s[0] for s in sites] == ["https://www.rightkind.in/", "https://zevana.co/", "sc-domain:gargi.shop"], sites   # unverified skipped
    p = dict(conn.execute("SELECT property_id, web_uris FROM google_ga4_properties WHERE connection_id=%s", [cid]).fetchall())
    assert set(p) == {"111", "222", "333"} and p["333"] == [], p                                                        # paging + stream failure tolerated
print("discovery OK")

html = c.get(f"/import/{cid}").get_data(as_text=True)
assert "sc-domain:gargi.shop" in html and "Links to existing client" in html and "No GA4 match" in html and 'value="Gargi"' in html
csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
rows = re.findall(r'name="site_(\d+)" value="([^"]+)"', html); idx = {v: k for k, v in rows}
ga4_sel = dict(re.findall(r'name="ga4_(\d+)".*?<option value="(\d+)" selected', html, re.S))
assert ga4_sel.get(idx["sc-domain:gargi.shop"]) == "111" and ga4_sel.get(idx["https://www.rightkind.in/"]) == "222"
form = {"csrf": csrf, "pick": [idx["sc-domain:gargi.shop"], idx["https://www.rightkind.in/"]]}
for k, v in rows: form[f"site_{k}"] = v; form[f"name_{k}"] = "Gargi Jewellery" if v.startswith("sc-domain") else "ignored"; form[f"ga4_{k}"] = ga4_sel.get(k, "")
assert c.post(f"/import/{cid}", data={**form, "csrf": "bad"}).status_code == 400              # CSRF enforced
r = c.post(f"/import/{cid}", data=form); assert r.status_code == 302 and "Imported+1+new" in r.headers["Location"], r.headers["Location"]
bad = {**form, "pick": ["0"], "site_0": "https://not-on-this-account.com/"}
assert c.post(f"/import/{cid}", data=bad).status_code == 400                                     # cannot import a site the account cannot see
with connect() as conn:
    got = conn.execute("""SELECT c.name, c.domain, i.gsc_site_url, i.ga4_property_id, i.google_connection_id::text FROM clients c
                          JOIN client_integrations i USING (client_id) WHERE c.domain IN ('gargi.shop','rightkind.in') ORDER BY c.domain""").fetchall()
    assert got == [("Gargi Jewellery", "gargi.shop", "sc-domain:gargi.shop", "111", cid), ("Right Kind Protein", "rightkind.in", "https://www.rightkind.in/", "222", cid)], got
    assert conn.execute("SELECT count(*) FROM clients WHERE domain='rightkind.in'").fetchone()[0] == 1
    g = [r for r in gsc.clients_to_sync(conn) if r[2] == "Gargi Jewellery"][0]; assert str(g[5]) == cid
    a = [r for r in ga4.clients_to_sync(conn) if r[2] == "Right Kind Protein"][0]; assert a[1] == "222" and str(a[3]) == cid
assert "Imported" in c.get(f"/import/{cid}").get_data(as_text=True)
print("import + collectors wiring OK")

# ---- refresh and revocation ----
r = c.post(f"/connections/{cid}/refresh", data={"csrf": csrf}); assert "Found+3" in r.headers["Location"], r.headers["Location"]
ga.http = FakeGoogle(refresh_ok=False)
r = c.post(f"/connections/{cid}/refresh", data={"csrf": csrf})
with connect() as conn:
    assert conn.execute("SELECT status FROM google_connections WHERE connection_id=%s", [cid]).fetchone()[0] == "revoked"
    try: ga.oauth_session(conn, cid, GSC); raise SystemExit("should fail")
    except ga.RevokedError: pass
    pool = ga.SessionPool(conn, GSC); os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    try: pool.get(None); raise SystemExit("should fail")
    except RuntimeError as e: assert "No Google sign-in" in str(e)
assert c.get("/").status_code == 302 and "Reconnect" in c.get("/connections").get_data(as_text=True)
print("refresh + revocation OK")

# ---- tenant isolation: clients never see connections or tokens ----
with connect() as conn:
    gargi = conn.execute("SELECT client_id FROM clients WHERE domain='gargi.shop'").fetchone()[0]
    conn.execute("SET app.is_staff = 'false'"); conn.execute("SELECT set_config('app.client_id', %s, false)", [str(gargi)])
    assert conn.execute("SELECT count(*) FROM google_connections").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM google_gsc_sites").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM client_integrations").fetchone()[0] == 1
print("isolation OK")
print("ALL OAUTH TESTS PASSED")
