"""Continue with ChatGPT end to end, with OpenAI faked: the sign-in address, the pasted return address, the code exchange with a
signed identity token, the saved (encrypted) connection, the model list, AI answers and blog drafts on the plan (streamed),
the API key covering when the plan's limit is reached, token refresh, reconnect after a revoked sign-in, and who may connect.
Needs ADMIN_DATABASE_URL."""
import os, sys, json, re, time, base64, datetime as dt
from urllib.parse import urlparse, parse_qs, urlencode
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "webapp"), os.path.join(ROOT, "scripts")]
import psycopg

admin = os.environ["ADMIN_DATABASE_URL"]
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS aeo_gpt")
    if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='site_owner'").fetchone():
        c.execute("CREATE ROLE site_owner LOGIN PASSWORD 'owner-pw' CREATEROLE NOSUPERUSER")
    c.execute("CREATE DATABASE aeo_gpt OWNER site_owner")
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname='aeo_app'").fetchone():
        c.execute("GRANT aeo_app TO site_owner WITH ADMIN OPTION")
owner = re.sub(r"postgresql://[^@]*@/[^?]*", "postgresql://site_owner:owner-pw@/aeo_gpt", admin)
from start_local import migrate
with psycopg.connect(owner, autocommit=True) as c: migrate(c)
from cryptography.fernet import Fernet
os.environ.update(DATABASE_URL=owner, DB_SET_ROLE="aeo_app", FLASK_SECRET="t", TOKEN_KEY=Fernet.generate_key().decode(),
                  GOOGLE_OAUTH_CLIENT_ID="x", GOOGLE_OAUTH_CLIENT_SECRET="x", OAUTH_REDIRECT_URI="http://localhost/cb")
for k in ("CHATGPT_OAUTH_CLIENT_ID", "CHATGPT_OAUTH_REDIRECT", "CHATGPT_PLAN_MODEL", "VERCEL"): os.environ.pop(k, None)
from common import connect
import chatgpt_plan as cp, ai_visibility as AV, blog_writer as W

A, Bc = "33333333-0000-0000-0000-00000000000a", "33333333-0000-0000-0000-00000000000b"
with connect() as conn:
    conn.execute("INSERT INTO clients (client_id, name, domain) VALUES (%s,'Gargi','gargi.shop'), (%s,'Other','other.com')", [A, Bc])
    conn.execute("INSERT INTO competitors (client_id, name, domain, tracked) VALUES (%s,'GIVA','giva.co',true)", [A])
    pid = conn.execute("INSERT INTO prompts (client_id, text, approved, region) VALUES (%s,'best silver jewellery brands in india',true,'IN') RETURNING prompt_id", [A]).fetchone()[0]

# ---------------------------------------------------------------- pure helpers
b = cp.plan_body({"model": "gpt-x", "input": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}],
                  "temperature": 0.2, "max_output_tokens": 50, "include": ["web_search_call.action.sources"]}, "plan-model")
assert b["store"] is False and b["stream"] is True and b["model"] == "plan-model" and b["instructions"] == "Be brief."
assert b["input"] == [{"role": "user", "content": "hi"}] and "temperature" not in b and "max_output_tokens" not in b and "include" in b
assert cp.plan_body({"input": "q"}, "m")["input"] == [{"role": "user", "content": "q"}]
s1 = cp.simplify({"input": [], "include": ["x"], "tools": [{"type": "web_search", "user_location": {"country": "IN"}}]}, "include")
assert "include" not in s1 and "user_location" in s1["tools"][0]
s2 = cp.simplify(s1, None); assert "user_location" not in s2["tools"][0] and cp.simplify(s2, None) is None
assert cp.params_from("http://127.0.0.1:1455/auth/callback?code=abc&state=xyz&client_id=oaiapp_1") == {"code": "abc", "state": "xyz", "client_id": "oaiapp_1"}
assert cp.params_from("code=abc&state=xyz")["code"] == "abc"
class _L:
    def __init__(s, lines): s.l = lines
    def iter_lines(s): return iter(s.l)
evs = list(cp.events(_L([b"event: x", b'data: {"type":"a",', b'data: "n":1}', b"", "data: {\"type\":\"b\"}", b"", b"data: [DONE]", b""])))
assert evs == [{"type": "a", "n": 1}, {"type": "b"}], evs
print("helpers: plan request shape (store off, streamed, system to instructions, unsupported fields out), step-down, pasted address")

# ---------------------------------------------------------------- fake OpenAI
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
pubn = KEY.public_key().public_numbers()
JWK = {"kty": "RSA", "kid": "k1", "alg": "RS256", "n": b64(pubn.n.to_bytes(256, "big")), "e": b64(pubn.e.to_bytes(3, "big"))}
def id_token(aud, nonce, email="brand@example.com", exp=None, kid="k1", key=KEY):
    head = b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    body = b64(json.dumps({"iss": cp.ISSUER, "aud": aud, "sub": "user-123", "email": email, "nonce": nonce,
                           "exp": exp or int(time.time()) + 3600, "iat": int(time.time())}).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64(sig)}"

class R:
    def __init__(s, status, body=None, lines=None):
        s.status_code, s._body, s._lines, s.text = status, body, lines, json.dumps(body) if body is not None else ""
    def json(s): return s._body
    def iter_lines(s, decode_unicode=True): return iter(s._lines or [])
    def close(s): pass

def sse(*events):                                   # as OpenAI sends it: bytes, "event:" and "data:" lines, a blank line after each
    out = []
    for e in events: out += [f"event: {e.get('type')}".encode(), f"data: {json.dumps(e)}".encode(), b""]
    return out
COMPLETED = {"model": "gpt-plan-1", "id": "resp_1", "output": [
    {"type": "web_search_call", "action": {"sources": [{"url": "https://www.giva.co/silver"}]}},
    {"type": "message", "content": [{"type": "output_text", "text": "Top picks: GIVA and Gargi. Gargi is hallmarked.",
                                     "annotations": [{"type": "url_citation", "url": "https://gargi.shop/silver", "title": "Gargi"}]}]}]}
JUDGED = {"model": "gpt-plan-1", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({"brands": [
    {"name": "GIVA", "sentiment": "neutral", "recommended": True}, {"name": "Gargi", "sentiment": "positive", "recommended": True}]})}]}]}

class Http:                                       # auth.openai.com and /v1/models
    def __init__(s): s.posts, s.refresh_reply, s.models = [], None, [{"slug": "gpt-plan-1", "display_name": "GPT Plan 1", "visibility": "list"},
                                                                       {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
                                                                       {"slug": "gpt-plan-mini", "display_name": "GPT Plan mini", "visibility": "list"}]
    def post(s, url, data=None, headers=None, timeout=None):
        s.posts.append((url, dict(data)))
        if data["grant_type"] == "authorization_code":
            st = cp_state[data["code"]]
            return R(200, {"access_token": "at-1", "refresh_token": "rt-1", "id_token": id_token(data["client_id"], st["nonce"]),
                           "token_type": "Bearer", "expires_in": 3600, "scope": cp.SCOPE})
        return s.refresh_reply or R(200, {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600, "scope": cp.SCOPE})
    def get(s, url, headers=None, timeout=None):
        if url == cp.JWKS: return R(200, {"keys": [JWK]})
        assert url == cp.MODELS and headers["Authorization"].startswith("Bearer at-")
        return R(200, {"models": s.models})
H = Http(); cp.http = H
cp_state = {}

class Responses:                                  # api.openai.com/v1/responses: plan (streamed) and API key (plain)
    def __init__(s): s.calls, s.plan_mode = [], "ok"
    def post(s, url, headers=None, json=None, timeout=None, stream=False):
        tok = headers["Authorization"].split(" ", 1)[1]
        s.calls.append(("plan" if tok.startswith("at-") else "api", json))
        judging = "text" in json
        if tok.startswith("at-"):
            assert url == cp.RESPONSES and stream and json["store"] is False and json["stream"] is True
            assert not any(m.get("role") == "system" for m in json["input"]) and json["model"] in ("gpt-plan-1", "gpt-plan-mini")
            if s.plan_mode == "limit": return R(429, {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}})
            if s.plan_mode == "midstream_limit":
                return R(200, lines=sse({"type": "response.created"}, {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}))
            if s.plan_mode == "no_include" and "include" in json:
                return R(400, {"error": {"code": "subscription_sharing_unsupported_capability", "param": "include", "message": "include"}})
            if s.plan_mode == "revoked": return R(401, {"error": {"code": "subscription_sharing_invalid_user", "message": "revoked"}})
            body = JUDGED if judging else COMPLETED
            return R(200, lines=sse({"type": "response.created"}, {"type": "response.output_text.delta", "delta": "Top"},
                                    {"type": "response.completed", "response": body}))
        assert tok == "sk-api" and not stream
        return R(200, JUDGED if judging else dict(COMPLETED, model="gpt-api"))
RS = Responses()

# ---------------------------------------------------------------- sign-in (paste mode) through the screens
import app as webapp
cl = webapp.app.test_client()
with cl.session_transaction() as s: s["staff_email"] = "seo@hovers.in"; s["csrf"] = "tok"
page = cl.get(f"/c/{A}/integrations").get_data(as_text=True)
assert "Continue with ChatGPT" in page and "Not connected" in page and "test mode" in page
r = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "connect"})
assert r.status_code == 302 and r.headers["Location"].endswith("?step=paste")
page = cl.get(f"/c/{A}/integrations?step=paste").get_data(as_text=True)
url = re.search(r'href="(https://auth\.openai\.com[^"]+)"', page).group(1).replace("&amp;", "&")
q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
assert urlparse(url).path == "/api/accounts/authorize" and q["client_id"] == "dynamic_agent_client" and q["redirect_uri"] == cp.LOOPBACK
assert q["scope"] == cp.SCOPE and q["resource"] == cp.RESOURCE and q["code_challenge_method"] == "S256" and q["agent_name_hint"] == "Orbit by Hovers"
assert q["ext_agent_host_id"].startswith("urn:uuid:") and q["state"] and q["nonce"] and "login_hint" not in q
cp_state["code-1"] = {"nonce": q["nonce"]}
with connect() as conn:
    v = conn.execute("SELECT verifier_enc FROM chatgpt_oauth_pending WHERE state=%s", [q["state"]]).fetchone()[0]
    assert b"code" not in bytes(v)                                       # verifier kept encrypted, server side
# a pasted address from another sign-in is refused
bad = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "paste", "address": "http://127.0.0.1:1455/auth/callback?code=x&state=nope"})
assert "err=" in bad.headers["Location"]
back = f"http://127.0.0.1:1455/auth/callback?code=code-1&scope=chatgpt.tokens.use.direct+email+offline_access+openid+profile+resource.invoke&state={q['state']}&client_id=oaiapp_issued1"
r = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "paste", "address": back})
assert "msg=" in r.headers["Location"] and "Connected+brand%40example.com" in r.headers["Location"], r.headers["Location"]
ex = H.posts[-1][1] if H.posts[-1][1]["grant_type"] == "authorization_code" else H.posts[0][1]
assert ex["client_id"] == "oaiapp_issued1" and ex["redirect_uri"] == cp.LOOPBACK and ex["resource"] == cp.RESOURCE and len(ex["code_verifier"]) > 40
with connect() as conn:
    g = cp.connection(conn, A)
    raw = conn.execute("SELECT access_token_enc, refresh_token_enc FROM chatgpt_connections WHERE client_id=%s", [A]).fetchone()
    assert not conn.execute("SELECT 1 FROM chatgpt_oauth_pending WHERE state=%s", [q["state"]]).fetchone()   # used once
assert g["email"] == "brand@example.com" and g["subject"] == "user-123" and g["oauth_client_id"] == "oaiapp_issued1" and g["plan_usage"]
assert g["model"] == "gpt-plan-1" and [m["slug"] for m in g["models"]] == ["gpt-plan-1", "gpt-plan-mini"]
assert b"at-1" not in bytes(raw[0]) and b"rt-1" not in bytes(raw[1])
# replaying the same address does nothing
again = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "paste", "address": back})
assert "err=" in again.headers["Location"]
print("sign-in: OpenAI address with PKCE and the app name, pasted return checked against this sign-in, code swapped once, identity token verified, tokens encrypted, models listed")

# identity token checks
try: cp.verify_id_token(id_token("oaiapp_other", "n1"), "oaiapp_issued1", "n1"); raise AssertionError("wrong audience accepted")
except cp.PlanError: pass
try: cp.verify_id_token(id_token("oaiapp_issued1", "n2"), "oaiapp_issued1", "n1"); raise AssertionError("wrong nonce accepted")
except cp.PlanError: pass
other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
try: cp.verify_id_token(id_token("oaiapp_issued1", "n1", key=other), "oaiapp_issued1", "n1"); raise AssertionError("bad signature accepted")
except cp.PlanError: pass
print("identity token: wrong app, wrong sign-in and forged signature all refused")

# ---------------------------------------------------------------- the screen once connected; settings and test
page = cl.get(f"/c/{A}/integrations").get_data(as_text=True)
assert "brand@example.com" in page and "GPT Plan mini" in page and "Hidden" not in page and "Test connection" in page
cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "settings", "model": "gpt-plan-mini", "use_for_ai": "on"})
with connect() as conn: g = cp.connection(conn, A)
assert g["model"] == "gpt-plan-mini" and g["use_for_ai"] and not g["use_for_blogs"]
cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "settings", "model": "not-offered", "use_for_ai": "on", "use_for_blogs": "on"})
with connect() as conn: g = cp.connection(conn, A)
assert g["model"] == "gpt-plan-mini" and g["use_for_blogs"]                  # an unlisted model is ignored
import requests as _rq
_orig_session = _rq.Session
_rq.Session = lambda: RS
r = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "test"})
_rq.Session = _orig_session
assert "msg=Working" in r.headers["Location"], r.headers["Location"]
print("screen: account, models (hidden ones left out), choose model and what it is used for, test connection")

# ---------------------------------------------------------------- AI answers on the plan, then the limit
with connect() as conn:
    route = cp.route_for(conn, A, "sk-api", "ai")
    assert isinstance(route, cp.PlanRoute) and cp.route_for(conn, Bc, "sk-api", "ai") == "sk-api"
    RS.calls.clear()
    tally, scores = AV.sync_client(conn, A, route, session=RS)
    assert tally == {"ok": 1}, tally
    assert [c[0] for c in RS.calls] == ["plan", "plan"]                       # the answer and the brand judgement
    ans = conn.execute("SELECT engine_version, raw_json FROM answers WHERE client_id=%s", [A]).fetchone()
    assert ans[0] == "gpt-plan-1" and ans[1]["via"] == "plan"
    assert conn.execute("SELECT count(*) FROM citations WHERE client_id=%s AND is_client", [A]).fetchone()[0] == 1
    assert cp.connection(conn, A)["plan_calls"] >= 2
    # plan refuses 'include': Orbit drops it and asks again on the plan
    RS.plan_mode = "no_include"; RS.calls.clear()
    j = cp.post(RS, cp.route_for(conn, A, "sk-api", "ai"), {"model": "x", "input": "q", "include": ["a"]}, 30)
    assert [c[0] for c in RS.calls] == ["plan", "plan"] and "include" not in RS.calls[1][1] and j["model"] == "gpt-plan-1"
    # limit reached mid-stream: the API key answers, the rest of the run skips the plan, the screen says so
    RS.plan_mode = "midstream_limit"; RS.calls.clear()
    route = cp.route_for(conn, A, "sk-api", "ai")
    j1 = cp.post(RS, route, {"model": "x", "input": "q"}, 30); j2 = cp.post(RS, route, {"model": "x", "input": "q"}, 30)
    assert [c[0] for c in RS.calls] == ["plan", "api", "api"] and j1["model"] == "gpt-api" and route.via == "api"
    g = cp.connection(conn, A)
    assert g["limit_hit_at"] and g["api_fallbacks"] == 2 and g["status"] == "active"
    assert cp.route_for(conn, A, "sk-api", "ai") == "sk-api"                  # next runs today: straight to the key
    conn.execute("UPDATE chatgpt_connections SET limit_hit_at = now() - interval '1 day' WHERE client_id=%s", [A])
    RS.plan_mode = "ok"
page = cl.get(f"/c/{A}/integrations").get_data(as_text=True)
assert "covered by Orbit" in page
print("AI answers: asked on the plan (streamed), answer and citations stored as before and marked as plan; a refused extra dropped and asked again; "
      "limit reached: the key covers, no plan retries for the day")

# ---------------------------------------------------------------- blog drafts on the plan
DRAFT = {"title": "How to clean silver", "meta_title": "How to clean silver", "meta_description": "d", "slug": "clean-silver", "excerpt": "e",
         "tags": ["silver"], "body_html": "<p>Body</p>", "faqs": [], "sources": []}
class BlogRS(Responses):
    def post(s, url, headers=None, json=None, timeout=None, stream=False):
        s.calls.append(("plan" if headers["Authorization"].startswith("Bearer at-") else "api", json))
        out = {"model": "gpt-plan-mini", "output": [{"type": "message", "content": [{"type": "output_text", "text": __import__("json").dumps(DRAFT)}]}]}
        return R(200, lines=sse({"type": "response.completed", "response": out}))
BR = BlogRS()
with connect() as conn:
    route = cp.route_for(conn, A, "sk-api", "blogs")
    got = W.ask(BR, route, "Write well.", "Brief")
    assert got["title"] == "How to clean silver" and BR.calls[0][0] == "plan" and BR.calls[0][1]["instructions"] == "Write well."
    assert route.label(W.MODEL) == "ChatGPT plan · gpt-plan-mini"
    conn.execute("UPDATE chatgpt_connections SET use_for_blogs=false WHERE client_id=%s", [A])
    assert cp.route_for(conn, A, "sk-api", "blogs") == "sk-api"
    conn.execute("UPDATE chatgpt_connections SET use_for_blogs=true WHERE client_id=%s", [A])
print("blog drafts: written on the plan with the system brief as instructions; switch off per use")

# on the brand's own plan the once-a-day limit on paid runs does not apply (it costs Hovers nothing)
import worker
with connect() as conn:
    conn.execute("INSERT INTO paid_runs (client_id, kind) VALUES (%s,'blogs'), (%s,'blogs')", [A, Bc])
    assert worker.paid_next(conn, A, "blogs") == (True, "") and worker.paid_ok(conn, A, "blogs")[0]
    assert worker.paid_next(conn, Bc, "blogs")[0] is False and worker.paid_ok(conn, Bc, "blogs")[0] is False
    assert worker.paid_next(conn, A, "semrush")[0] is True                      # other paid sources untouched (none run yet)
    assert conn.execute("SELECT count(*) FROM paid_runs WHERE client_id=%s AND kind='blogs'", [A]).fetchone()[0] == 1   # not recorded
    conn.execute("UPDATE chatgpt_connections SET use_for_blogs=false WHERE client_id=%s", [A])
    assert worker.paid_next(conn, A, "blogs")[0] is False                        # back on the key: the limit applies again
    conn.execute("UPDATE chatgpt_connections SET use_for_blogs=true WHERE client_id=%s", [A])
print("daily limit: lifted while the brand's own plan writes; applies again on the API key")

# ---------------------------------------------------------------- refresh, then a revoked sign-in
with connect() as conn:
    conn.execute("UPDATE chatgpt_connections SET expires_at = now() + interval '1 minute' WHERE client_id=%s", [A])
    H.posts.clear()
    assert cp.access_token(conn, A) == "at-2"
    sent = H.posts[-1][1]
    assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "rt-1" and sent["client_id"] == "oaiapp_issued1"
    assert cp.access_token(conn, A) == "at-2" and len(H.posts) == 1                 # fresh now: no second refresh
    conn.execute("UPDATE chatgpt_connections SET expires_at = now() - interval '1 minute' WHERE client_id=%s", [A])
    H.refresh_reply = R(400, {"error": "refresh_token_reused"})
    RS.calls.clear()
    route = cp.route_for(conn, A, "sk-api", "ai")
    j = cp.post(RS, route, {"model": "x", "input": "q"}, 30)
    assert j["model"] == "gpt-api" and [c[0] for c in RS.calls] == ["api"]
    g = cp.connection(conn, A)
    assert g["status"] == "reconnect" and "Connect ChatGPT again" in g["last_error"]
    assert cp.route_for(conn, A, "sk-api", "ai") == "sk-api"
    H.refresh_reply = None
page = cl.get(f"/c/{A}/integrations").get_data(as_text=True)
assert "Connect again" in page and "Continue with ChatGPT" in page
print("tokens: refreshed before expiry with the rotating refresh token; a revoked sign-in falls back to the key and asks to connect again")

# anything unexpected on the plan route (here a broken stream) is covered by the key, not a failed draft
class Odd(Responses):
    def post(s, url, headers=None, json=None, timeout=None, stream=False):
        if headers["Authorization"].startswith("Bearer at-"):
            class Bad(R):
                def iter_lines(s2): raise TypeError("startswith first arg must be bytes or a tuple of bytes, not str")
            return Bad(200)
        return super().post(url, headers=headers, json=json, timeout=timeout, stream=stream)
with connect() as conn:
    conn.execute("UPDATE chatgpt_connections SET status='active', expires_at=now()+interval '1 hour', last_error=NULL WHERE client_id=%s", [A])
    od = Odd(); j = cp.post(od, cp.route_for(conn, A, "sk-api", "blogs"), {"model": "x", "input": "q"}, 30)
    assert j["model"] == "gpt-api" and [c[0] for c in od.calls] == ["api"] and "Unexpected problem" in cp.connection(conn, A)["last_error"]
print("unexpected plan error: the key writes it instead, and the reason is kept for the team")

# no backup key: a plan failure is an error, not a silent empty answer
with connect() as conn:
    conn.execute("UPDATE chatgpt_connections SET status='active', expires_at=now()+interval '1 hour', last_error=NULL WHERE client_id=%s", [A])
    RS.plan_mode = "revoked"
    try: cp.post(RS, cp.route_for(conn, A, None, "ai"), {"model": "x", "input": "q"}, 30); raise AssertionError("should fail")
    except RuntimeError as e: assert "ChatGPT plan" in str(e)
    assert cp.connection(conn, A)["status"] == "reconnect"
    RS.plan_mode = "ok"

# ---------------------------------------------------------------- who may connect
def viewer(email, brands):
    c = webapp.app.test_client()
    with c.session_transaction() as s: s["viewer_email"] = email; s["viewer_clients"] = brands; s["csrf"] = "tok"
    return c
v = viewer("client@gargi.shop", [A])
page = v.get(f"/c/{A}/integrations").get_data(as_text=True)
assert v.get(f"/c/{A}/integrations").status_code == 200 and "Continue with ChatGPT" in page
assert "test mode" not in page and "CHATGPT_OAUTH_CLIENT_ID" not in page and not re.search(r"class=\"[^\"]*staff-only", page)   # team note never reaches clients
r = v.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "connect"})
assert r.status_code == 302 and "step=paste" in r.headers["Location"]
with connect() as conn:
    assert conn.execute("SELECT created_by FROM chatgpt_oauth_pending WHERE client_id=%s ORDER BY created_at DESC LIMIT 1", [A]).fetchone()[0] == "client@gargi.shop"
assert v.get(f"/c/{Bc}/integrations").status_code in (302, 403, 404)              # not their brand
assert v.post(f"/c/{Bc}/integrations", data={"csrf": "tok", "what": "connect"}).status_code in (302, 403, 404)
with connect() as conn:
    assert not conn.execute("SELECT 1 FROM chatgpt_oauth_pending WHERE client_id=%s", [Bc]).fetchone()
assert v.get(f"/c/{A}/settings").status_code == 302                              # still no settings for clients
# a viewer's session cannot read another brand's connection, even by query
with connect() as conn:
    conn.execute("INSERT INTO chatgpt_connections (client_id, oauth_client_id, host_id) VALUES (%s,'oaiapp_b','urn:uuid:b')", [Bc])
import dashboard
with webapp.app.test_request_context(f"/c/{A}/integrations"):
    from flask import session as fs, request as fr
    fs["viewer_email"] = "client@gargi.shop"; fs["viewer_clients"] = [A]; fr.view_args = {"cid": A}
    with dashboard.connect() as conn:
        assert conn.execute("SELECT count(*) FROM chatgpt_connections").fetchone()[0] == 1
print("access: a client connects their own brand (started by them), never another brand's; the team note is removed for clients; rows isolated per brand")

# disconnect
r = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "disconnect"})
with connect() as conn: assert not cp.connection(conn, A) and cp.route_for(conn, A, "sk-api", "ai") == "sk-api"
print("disconnect: the brand is back on the API key")

# direct mode once OpenAI registers Orbit
os.environ["CHATGPT_OAUTH_CLIENT_ID"] = "oaiapp_registered"
r = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "connect"})
loc = r.headers["Location"]; q = {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()}
assert loc.startswith(cp.AUTHORIZE) and q["client_id"] == "oaiapp_registered" and q["redirect_uri"].endswith("/integrations/chatgpt/callback")
assert "agent_name_hint" not in q
cp_state["code-2"] = {"nonce": q["nonce"]}
r = cl.get("/integrations/chatgpt/callback?" + urlencode({"code": "code-2", "state": q["state"], "scope": cp.SCOPE}))
assert "msg=Connected" in r.headers["Location"], r.headers["Location"]
with connect() as conn: assert cp.connection(conn, A)["oauth_client_id"] == "oaiapp_registered"
r = cl.get("/integrations/chatgpt/callback?" + urlencode({"code": "code-2", "state": "forged"}))
assert "login" in r.headers["Location"]
os.environ.pop("CHATGPT_OAUTH_CLIENT_ID")
print("direct mode: with a registered app id the browser comes straight back to Orbit; a forged return is refused")
print("ALL CHATGPT PLAN TESTS PASSED")
