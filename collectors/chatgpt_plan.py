"""Continue with ChatGPT: a brand's own ChatGPT plan answers its AI prompts and writes its blog drafts, instead of the
OpenAI API key. Hovers' API key stays as the backup whenever the plan cannot answer.

How it works (OpenAI "Sign in with ChatGPT", plan usage, preview, Nov 2026):
  1. The client signs in at auth.openai.com (OAuth 2.0 + PKCE + OpenID Connect) and approves plan usage
     (scope chatgpt.tokens.use.direct). OpenAI issues a client id for the sign-in (oaiapp_...).
  2. Orbit keeps the access token (1 hour) and the rotating refresh token (30 days), encrypted with TOKEN_KEY.
  3. Requests go to POST https://api.openai.com/v1/responses with that access token, store=false and stream=true;
     those requests count against the client's ChatGPT plan, not Hovers' API bill.

Return address: OpenAI's self-serve route (client id dynamic_agent_client) only sends the browser back to
http://127.0.0.1 on the person's own computer. Orbit runs online, so in that mode the person copies the address the
browser lands on and pastes it into Orbit (the code in it is useless without the secret Orbit kept). Once OpenAI
registers Orbit (CHATGPT_OAUTH_CLIENT_ID), the browser comes straight back to /integrations/chatgpt/callback.

Env: TOKEN_KEY; optional CHATGPT_OAUTH_CLIENT_ID (registered id), CHATGPT_OAUTH_REDIRECT (override the return address),
CHATGPT_PLAN_MODEL (preferred model slug when the account offers it).
"""
import base64, datetime as dt, hashlib, json, os, secrets, time, uuid
from urllib.parse import urlencode, urlparse, parse_qs
import requests
import google_auth as ga

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
JWKS = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
RESPONSES = RESOURCE + "/responses"
MODELS = RESOURCE + "/models"
API = "https://api.openai.com/v1/responses"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
DYNAMIC = "dynamic_agent_client"
LOOPBACK = "http://127.0.0.1:1455/auth/callback"
APP_NAME = "Orbit by Hovers"
PENDING_MINUTES = 30
# Fields the plan route rejects (OpenAI preview limitations); removed before sending.
OMIT = ("background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation", "multi_agent", "prompt",
        "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs", "top_p", "truncation", "user",
        "previous_response_id", "service_tier")
TERMINAL_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                    "refresh_token_invalidated", "refresh_token_reused", "invalid_client"}

http = requests            # swapped for a fake in tests

class PlanError(Exception):
    """The plan could not answer this request. kind: limit | not_eligible | reconnect | unsupported | unavailable | failed"""
    def __init__(self, kind, message, code=None, param=None):
        super().__init__(message); self.kind, self.code, self.param = kind, code, param

def now(): return dt.datetime.now(dt.timezone.utc)
def b64url(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def registered_client_id(): return (os.environ.get("CHATGPT_OAUTH_CLIENT_ID") or "").strip() or None

def mode():
    """'direct' when OpenAI has registered Orbit (the browser comes back by itself), else 'paste'."""
    return "direct" if registered_client_id() or os.environ.get("CHATGPT_OAUTH_REDIRECT") else "paste"

# ------------------------------------------------------------------ sign-in
def start(conn, client_id, who, hosted_callback=None):
    """Remember a fresh sign-in attempt and return the OpenAI address to send the browser to."""
    reg = registered_client_id()
    redirect = os.environ.get("CHATGPT_OAUTH_REDIRECT") or (hosted_callback if reg else LOOPBACK)
    have = conn.execute("SELECT oauth_client_id, host_id, email FROM chatgpt_connections WHERE client_id=%s", [client_id]).fetchone()
    host_id = (have and have[1]) or f"urn:uuid:{uuid.uuid4()}"
    issued = reg or (have[0] if have and have[0] and have[0] != DYNAMIC else None)
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    conn.execute("DELETE FROM chatgpt_oauth_pending WHERE created_at < now() - %s * interval '1 minute'", [PENDING_MINUTES])
    conn.execute("""INSERT INTO chatgpt_oauth_pending (state, client_id, nonce, verifier_enc, host_id, redirect_uri, oauth_client_id, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                 [state, client_id, nonce, ga.encrypt(verifier), host_id, redirect, issued or DYNAMIC, who])
    q = {"client_id": issued or DYNAMIC, "response_type": "code", "redirect_uri": redirect, "scope": SCOPE, "resource": RESOURCE,
         "state": state, "nonce": nonce, "code_challenge_method": "S256",
         "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()), "ext_agent_host_id": host_id}
    if not issued: q["agent_name_hint"] = APP_NAME
    elif have and have[2]: q["login_hint"] = have[2]
    return AUTHORIZE + "?" + urlencode(q), state

def params_from(pasted):
    """The address the browser landed on (or just its ?query part) -> its parameters."""
    s = (pasted or "").strip()
    if "?" in s: s = s.split("?", 1)[1]
    return {k: v[0] for k, v in parse_qs(s.split("#", 1)[0]).items()}

def pending(conn, state):
    r = conn.execute("""SELECT client_id, nonce, verifier_enc, host_id, redirect_uri, oauth_client_id, created_by, created_at
                        FROM chatgpt_oauth_pending WHERE state=%s""", [state or ""]).fetchone()
    if not r: return None
    keys = ["client_id", "nonce", "verifier_enc", "host_id", "redirect_uri", "oauth_client_id", "created_by", "created_at"]
    p = dict(zip(keys, r)); p["client_id"] = str(p["client_id"])
    return p

def finish(conn, params, who):
    """Check what OpenAI sent back, swap the code for tokens, save the connection, load the account's models.
    -> (client_id, message). Raises PlanError('failed', ...) with a plain reason."""
    p = pending(conn, params.get("state"))
    if not p or p["created_at"] < now() - dt.timedelta(minutes=PENDING_MINUTES):
        raise PlanError("failed", "This sign-in has expired or was already used. Start again with Continue with ChatGPT.")
    conn.execute("DELETE FROM chatgpt_oauth_pending WHERE state=%s", [params["state"]])
    if params.get("error"):
        if params["error"] == "access_denied": raise PlanError("failed", "ChatGPT sign-in was cancelled, so nothing was connected.")
        raise PlanError("failed", f"ChatGPT sign-in did not finish: {params.get('error_description') or params['error']}")
    if not params.get("code"): raise PlanError("failed", "The address has no sign-in code. Copy the whole address from the browser bar.")
    issued = params.get("client_id") or (p["oauth_client_id"] if p["oauth_client_id"] != DYNAMIC else None)
    if p["oauth_client_id"] != DYNAMIC and params.get("client_id") and params["client_id"] != p["oauth_client_id"]:
        raise PlanError("failed", "OpenAI returned a different app id than this sign-in started with, so it was not used.")
    if not issued: raise PlanError("failed", "OpenAI did not return an app id for this sign-in (registration incomplete). Try again.")
    r = http.post(TOKEN, data={"grant_type": "authorization_code", "client_id": issued, "code": params["code"],
                               "code_verifier": ga.decrypt(p["verifier_enc"]), "redirect_uri": p["redirect_uri"], "resource": RESOURCE},
                  headers={"Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        raise PlanError("failed", f"OpenAI did not accept the sign-in code ({r.status_code}): {error_text(r)}. Start again.")
    tok = r.json()
    claims = verify_id_token(tok.get("id_token"), issued, p["nonce"])
    granted = set((tok.get("scope") or params.get("scope") or "").replace("+", " ").split())
    save(conn, p["client_id"], tok, claims, issued, p["host_id"], who or p["created_by"], PLAN_SCOPE in granted)
    note = ""
    if PLAN_SCOPE in granted:
        try: note = f" {len(load_models(conn, p['client_id']))} models available."
        except PlanError as e: note = f" Models could not be listed yet: {e}"
        return p["client_id"], f"Connected {claims.get('email') or 'the ChatGPT account'}. AI answers and blog drafts now use this plan.{note}"
    return p["client_id"], (f"Signed in as {claims.get('email') or 'the ChatGPT account'}, but plan usage was not approved, "
                            "so the API key keeps answering. Connect again and allow plan usage to switch.")

def error_text(r):
    try:
        j = r.json(); e = j.get("error")
        if isinstance(e, dict): return e.get("message") or e.get("code") or str(e)
        return j.get("error_description") or e or j.get("detail") or r.text[:200]
    except Exception:
        return r.text[:200]

_JWKS = {}

def verify_id_token(id_token, audience, nonce):
    """RS256 signature against OpenAI's published keys, then issuer, audience, expiry and nonce."""
    if not id_token: raise PlanError("failed", "OpenAI did not return an identity token.")
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import hashes
    from cryptography.exceptions import InvalidSignature
    def unb64(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    try:
        head_b, body_b, sig_b = id_token.split(".")
        head, claims = json.loads(unb64(head_b)), json.loads(unb64(body_b))
    except Exception:
        raise PlanError("failed", "The identity token from OpenAI could not be read.")
    if head.get("alg") != "RS256": raise PlanError("failed", "The identity token uses an unexpected signature type.")
    keys = _JWKS.get("keys") if _JWKS.get("at", 0) > time.time() - 3600 else None
    if not keys or not any(k.get("kid") == head.get("kid") for k in keys):
        keys = http.get(JWKS, timeout=20).json().get("keys", []); _JWKS.update(keys=keys, at=time.time())
    jwk = next((k for k in keys if k.get("kid") == head.get("kid")), None)
    if not jwk: raise PlanError("failed", "OpenAI's signing key for this sign-in was not found.")
    pub = rsa.RSAPublicNumbers(int.from_bytes(unb64(jwk["e"]), "big"), int.from_bytes(unb64(jwk["n"]), "big")).public_key()
    try:
        pub.verify(unb64(sig_b), f"{head_b}.{body_b}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise PlanError("failed", "The identity token's signature did not match OpenAI's key.")
    aud = claims.get("aud"); aud = aud if isinstance(aud, list) else [aud]
    if claims.get("iss") != ISSUER: raise PlanError("failed", "The identity token was not issued by OpenAI.")
    if audience not in aud: raise PlanError("failed", "The identity token was issued for a different app.")
    if (claims.get("exp") or 0) < time.time() - 60: raise PlanError("failed", "The identity token had already expired.")
    if claims.get("nonce") != nonce: raise PlanError("failed", "The identity token does not belong to this sign-in.")
    return claims

def _expiry(tok):
    t = now()
    return (t + dt.timedelta(seconds=int(tok.get("expires_in") or 3600)),
            dt.datetime.fromtimestamp(tok["earliest_refresh_at"], dt.timezone.utc) if isinstance(tok.get("earliest_refresh_at"), (int, float)) else None)

def save(conn, client_id, tok, claims, issued, host_id, who, plan_usage):
    exp, earliest = _expiry(tok)
    conn.execute("""INSERT INTO chatgpt_connections (client_id, email, subject, oauth_client_id, host_id, id_token_enc, access_token_enc,
                        refresh_token_enc, expires_at, earliest_refresh_at, scopes, plan_usage, status, last_error, limit_hit_at, connected_by,
                        connected_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',NULL,NULL,%s,now(),now())
                    ON CONFLICT (client_id) DO UPDATE SET email=EXCLUDED.email, subject=EXCLUDED.subject, oauth_client_id=EXCLUDED.oauth_client_id,
                        host_id=EXCLUDED.host_id, id_token_enc=EXCLUDED.id_token_enc, access_token_enc=EXCLUDED.access_token_enc,
                        refresh_token_enc=coalesce(EXCLUDED.refresh_token_enc, chatgpt_connections.refresh_token_enc),
                        expires_at=EXCLUDED.expires_at, earliest_refresh_at=EXCLUDED.earliest_refresh_at, scopes=EXCLUDED.scopes,
                        plan_usage=EXCLUDED.plan_usage, status='active', last_error=NULL, limit_hit_at=NULL,
                        connected_by=EXCLUDED.connected_by, connected_at=now(), updated_at=now()""",
                 [client_id, claims.get("email"), claims.get("sub"), issued, host_id, ga.encrypt(tok["id_token"]),
                  ga.encrypt(tok["access_token"]), ga.encrypt(tok["refresh_token"]) if tok.get("refresh_token") else None,
                  exp, earliest, tok.get("scope") or "", plan_usage, who])

COLS = ["email", "subject", "oauth_client_id", "host_id", "access_token_enc", "refresh_token_enc", "expires_at", "earliest_refresh_at",
        "scopes", "plan_usage", "models", "model", "use_for_ai", "use_for_blogs", "status", "last_error", "limit_hit_at", "last_used_at",
        "plan_calls", "api_fallbacks", "connected_by", "connected_at"]

def connection(conn, client_id, lock=False):
    r = conn.execute(f"SELECT {', '.join(COLS)} FROM chatgpt_connections WHERE client_id=%s" + (" FOR UPDATE" if lock else ""),
                     [client_id]).fetchone()
    return dict(zip(COLS, r)) if r else None

def disconnect(conn, client_id):
    conn.execute("DELETE FROM chatgpt_connections WHERE client_id=%s", [client_id])

def mark(conn, client_id, status=None, error=None, limit=False):
    conn.execute("""UPDATE chatgpt_connections SET status=coalesce(%s, status), last_error=%s,
                    limit_hit_at=CASE WHEN %s THEN now() ELSE limit_hit_at END, updated_at=now() WHERE client_id=%s""",
                 [status, error, limit, client_id])

def access_token(conn, client_id):
    """A working access token, refreshed when it expires within five minutes. The refresh token rotates, so the row is
    locked while refreshing: two runs refreshing at once would make OpenAI reject the reused token."""
    c = connection(conn, client_id)
    if not c: raise PlanError("reconnect", "ChatGPT is not connected for this brand.")
    if c["status"] == "reconnect": raise PlanError("reconnect", c["last_error"] or "Connect ChatGPT again.")
    if c["expires_at"] and c["expires_at"] > now() + dt.timedelta(minutes=5):
        return ga.decrypt(c["access_token_enc"])
    with conn.transaction():
        c = connection(conn, client_id, lock=True)
        if c["expires_at"] and c["expires_at"] > now() + dt.timedelta(minutes=5):     # another run refreshed it meanwhile
            return ga.decrypt(c["access_token_enc"])
        if c["earliest_refresh_at"] and c["earliest_refresh_at"] > now() and c["expires_at"] and c["expires_at"] > now():
            return ga.decrypt(c["access_token_enc"])
        if not c["refresh_token_enc"]:
            mark(conn, client_id, "reconnect", "The ChatGPT sign-in has expired. Connect ChatGPT again.")
            raise PlanError("reconnect", "The ChatGPT sign-in has expired. Connect ChatGPT again.")
        data = {"grant_type": "refresh_token", "client_id": c["oauth_client_id"], "refresh_token": ga.decrypt(c["refresh_token_enc"]),
                "resource": RESOURCE}
        r = http.post(TOKEN, data=data, headers={"Accept": "application/json"}, timeout=30)
        if r.status_code != 200:
            code = _code(r)
            if code in ("invalid_request", "invalid_target"):                          # some servers refuse resource on refresh
                data.pop("resource"); r = http.post(TOKEN, data=data, headers={"Accept": "application/json"}, timeout=30); code = _code(r)
            if r.status_code != 200:
                if code in TERMINAL_REFRESH or r.status_code in (400, 401):
                    msg = "ChatGPT access was removed or has expired. Connect ChatGPT again."
                    mark(conn, client_id, "reconnect", msg); raise PlanError("reconnect", msg, code)
                raise PlanError("unavailable", f"Could not renew ChatGPT access right now ({r.status_code}).", code)
        tok = r.json()
        exp, earliest = _expiry(tok)
        conn.execute("""UPDATE chatgpt_connections SET access_token_enc=%s, refresh_token_enc=coalesce(%s, refresh_token_enc), expires_at=%s,
                        earliest_refresh_at=%s, scopes=coalesce(nullif(%s,''), scopes), updated_at=now() WHERE client_id=%s""",
                     [ga.encrypt(tok["access_token"]), ga.encrypt(tok["refresh_token"]) if tok.get("refresh_token") else None,
                      exp, earliest, tok.get("scope") or "", client_id])
        return tok["access_token"]

def _code(r):
    try:
        j = r.json(); e = j.get("error")
        return (e.get("code") if isinstance(e, dict) else e) or j.get("code")
    except Exception:
        return None

def load_models(conn, client_id):
    """The models this ChatGPT account may use, in OpenAI's order; keeps the chosen one, else CHATGPT_PLAN_MODEL, else the first."""
    r = http.get(MODELS, headers={"Authorization": f"Bearer {access_token(conn, client_id)}"}, timeout=30)
    if r.status_code != 200: raise_for(conn, client_id, r)
    data = r.json()
    items = data.get("models") if isinstance(data.get("models"), list) else data.get("data") or []
    models = [{"slug": m.get("slug") or m.get("id"), "display_name": m.get("display_name") or m.get("slug") or m.get("id")}
              for m in items if (m.get("visibility") in (None, "list")) and (m.get("slug") or m.get("id"))]
    c = connection(conn, client_id)
    slugs = [m["slug"] for m in models]
    pick = c["model"] if c and c["model"] in slugs else os.environ.get("CHATGPT_PLAN_MODEL") if os.environ.get("CHATGPT_PLAN_MODEL") in slugs \
        else (slugs[0] if slugs else None)
    conn.execute("UPDATE chatgpt_connections SET models=%s, model=%s, updated_at=now() WHERE client_id=%s", [json.dumps(models), pick, client_id])
    return models

def raise_for(conn, client_id, r, code=None, message=None, param=None):
    """Turn an OpenAI refusal into a PlanError, and remember the lasting ones on the connection."""
    status = getattr(r, "status_code", None)
    if r is not None and code is None:
        code = _code(r)
        try:
            j = r.json(); e = j.get("error") if isinstance(j.get("error"), dict) else {}
            message, param = e.get("message") or j.get("detail") or message, e.get("param")
        except Exception:
            message = (r.text or "")[:200]
    message = message or code or f"HTTP {status}"
    if code == "subscription_sharing_usage_limit_exceeded" or status == 429:
        mark(conn, client_id, None, "The ChatGPT plan's limit for Orbit was reached; the API key covers until it frees up.", limit=True)
        raise PlanError("limit", "ChatGPT plan limit reached", code)
    if code == "subscription_sharing_user_not_eligible":
        msg = "This ChatGPT account cannot share its plan with apps (a paid Plus or Pro plan is needed, or the workspace does not allow it)."
        mark(conn, client_id, "not_eligible", msg); raise PlanError("not_eligible", msg, code)
    if code in ("subscription_sharing_invalid_user",) or status == 401:
        raise PlanError("reconnect", f"OpenAI did not accept this ChatGPT sign-in ({message}).", code)
    if code in ("subscription_sharing_unsupported_capability",) or status == 400:
        raise PlanError("unsupported", f"Not supported on the plan route: {param or message}", code, param)
    if code in ("subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable") or status in (500, 502, 503, 504):
        raise PlanError("unavailable", f"ChatGPT plan usage is unavailable right now ({message}).", code)
    raise PlanError("failed", f"OpenAI refused the plan request ({status}: {message}).", code, param)

# ------------------------------------------------------------------ requests on the plan
def plan_body(body, model):
    """An API request body -> the shape the plan route accepts: store false, streamed, input as a list, system messages moved to
    instructions, unsupported fields removed."""
    b = {k: v for k, v in body.items() if k not in OMIT}
    inp = b.get("input")
    if isinstance(inp, str): inp = [{"role": "user", "content": inp}]
    system = [m.get("content") for m in (inp or []) if isinstance(m, dict) and m.get("role") == "system"]
    inp = [m for m in (inp or []) if not (isinstance(m, dict) and m.get("role") == "system")]
    if system: b["instructions"] = "\n\n".join(([b["instructions"]] if b.get("instructions") else []) + [s for s in system if s])
    b.update(input=inp, store=False, stream=True)
    if model: b["model"] = model
    return b

def simplify(b, param):
    """One step simpler after an 'unsupported' refusal: drop the named extra, else the extras most likely refused. None = nothing left."""
    b = json.loads(json.dumps(b))
    p = (param or "").split(".")[0].split("[")[0]
    if p in b and p not in ("input", "model", "instructions", "store", "stream", "tools", "text"):
        b.pop(p); return b
    if "include" in b: b.pop("include"); return b
    for t in b.get("tools") or []:
        if "user_location" in t: t.pop("user_location"); return b
    return None

def events(r):
    """Server-sent events -> one dict per event. Lines arrive as bytes when the reply names no charset (OpenAI's event
    stream does not), so decode them ourselves; an event may span several data: lines and ends at a blank line."""
    buf = []
    for raw in r.iter_lines():
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else (raw or "")
        line = line.rstrip("\r")
        if not line:
            if buf:
                data, buf = "\n".join(buf), []
                if data.strip() == "[DONE]": return
                try: yield json.loads(data)
                except ValueError: pass
            continue
        if line.startswith("data:"): buf.append(line[5:].lstrip())
    if buf:
        data = "\n".join(buf)
        if data.strip() != "[DONE]":
            try: yield json.loads(data)
            except ValueError: pass

def stream(session, token, body, timeout):
    """Send one streamed Responses request; return the final response object (as the API returns without streaming)."""
    try:
        r = session.post(RESPONSES, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream"},
                         json=body, timeout=timeout, stream=True)
    except requests.RequestException as e:
        return None, ("interrupted", f"Could not reach ChatGPT ({type(e).__name__}).", None)
    if r.status_code != 200: return r, None
    final, err = None, None
    try:
        for ev in events(r):
            t = ev.get("type")
            if t == "response.completed": final = ev.get("response"); break
            if t in ("response.failed", "error"):
                e = (ev.get("response") or {}).get("error") or ev.get("error") or {}
                err = (e.get("code") or "unknown_error", e.get("message") or "", e.get("param")); break
            if t == "response.incomplete":
                reason = ((ev.get("response") or {}).get("incomplete_details") or {}).get("reason") or "incomplete"
                err = ("incomplete", f"The answer stopped early ({reason}).", None); break
    except requests.RequestException as e:
        err = ("interrupted", f"The answer stream was cut off ({type(e).__name__}).", None)
    finally:
        r.close()
    if err: return None, err
    if final is None: return None, ("interrupted", "The answer stream ended before it finished.", None)
    return None, final

class PlanRoute:
    """Stands where the API key string used to: post() answers from the brand's ChatGPT plan, and falls back to the API key
    when the plan cannot (limit reached, not eligible, signed out, unsupported request, outage)."""
    def __init__(self, conn, client_id, api_key, model):
        self.conn, self.client_id, self.api_key, self.model = conn, client_id, api_key, model
        self.off = None                           # why the plan is skipped for the rest of this run
        self.via = None                           # 'plan' or 'api' for the last request

    def __bool__(self): return True

    def label(self, default_model):
        return f"ChatGPT plan · {self.model}" if self.via == "plan" else default_model

    def post(self, session, body, timeout):
        if not self.off:
            try:
                j = self._plan(session, body, timeout)
                self.via = "plan"
                self.conn.execute("""UPDATE chatgpt_connections SET plan_calls=plan_calls+1, last_used_at=now(), last_error=NULL
                                     WHERE client_id=%s""", [self.client_id])
                return j
            except Exception as e:                       # anything unexpected on the plan route: the key answers instead
                if not isinstance(e, PlanError): e = PlanError("failed", f"Unexpected problem on the ChatGPT plan route: {e}")
                if e.kind in ("limit", "not_eligible", "reconnect"): self.off = str(e)
                if e.kind == "reconnect": mark(self.conn, self.client_id, "reconnect", str(e))
                elif e.kind in ("unsupported", "unavailable", "failed"): mark(self.conn, self.client_id, None, str(e)[:500])
                if not self.api_key: raise RuntimeError(f"ChatGPT plan: {e}")
        if not self.api_key: raise RuntimeError(f"ChatGPT plan: {self.off}")
        self.via = "api"
        self.conn.execute("UPDATE chatgpt_connections SET api_fallbacks=api_fallbacks+1 WHERE client_id=%s", [self.client_id])
        return api_post(session, self.api_key, body, timeout)

    def _plan(self, session, body, timeout):
        b = plan_body(body, self.model)
        for attempt in range(4):
            token = access_token(self.conn, self.client_id)
            refused, out = stream(session, token, b, timeout)
            if refused is None and not isinstance(out, tuple): return out
            try:
                if refused is not None: raise_for(self.conn, self.client_id, refused)
                code, msg, param = out                                        # refused or cut off mid-stream
                if code.startswith("subscription_sharing"): raise_for(self.conn, self.client_id, None, code=code, message=msg, param=param)
                if code in ("interrupted", "incomplete", "unknown_error", "server_error"):
                    raise PlanError("unavailable", msg or code, code, param)
                raise PlanError("failed", msg or code, code, param)
            except PlanError as e:
                if e.kind == "unsupported":
                    b2 = simplify(b, e.param)
                    if b2 is not None: b = b2; continue
                if e.kind == "unavailable" and attempt < 2: time.sleep(2 * (attempt + 1)); continue
                raise
        raise PlanError("unsupported", "The request could not be simplified enough for the plan route.")

def api_post(session, key, body, timeout):
    r = session.post(API, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
    if r.status_code != 200: raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:300]}")
    return r.json()

def post(session, key, body, timeout):
    """What the collectors call: key is the API key string, or a PlanRoute for a brand on its own ChatGPT plan."""
    return key.post(session, body, timeout) if isinstance(key, PlanRoute) else api_post(session, key, body, timeout)

def route_for(conn, client_id, api_key, purpose="ai"):
    """The brand's ChatGPT plan when it is connected, allowed plan usage and is on for this purpose ('ai' or 'blogs');
    otherwise the API key (which may be None)."""
    try:
        c = connection(conn, client_id)
    except Exception:
        return api_key                                                   # table not there yet (old database): API key as before
    if not c or c["status"] != "active" or not c["plan_usage"] or not c["model"]: return api_key
    if not c["use_for_ai" if purpose == "ai" else "use_for_blogs"]: return api_key
    if c["limit_hit_at"] and c["limit_hit_at"] > now() - dt.timedelta(hours=12) and api_key: return api_key
    return PlanRoute(conn, client_id, api_key, c["model"])

def test(conn, client_id, session=None):
    """Refresh the model list and ask one tiny question on the plan. -> (ok, message)"""
    session = session or requests.Session()
    try:
        models = load_models(conn, client_id)
        c = connection(conn, client_id)
        if not c["plan_usage"]: return False, "Signed in, but plan usage was not approved. Connect again and allow it."
        if not c["model"]: return False, "This ChatGPT account offers no models to apps."
        route = PlanRoute(conn, client_id, None, c["model"])
        t0 = time.time()
        j = route.post(session, {"model": c["model"], "input": "Reply with the single word: ready"}, 90)
        text = " ".join(p.get("text", "") for i in j.get("output", []) if i.get("type") == "message"
                        for p in i.get("content", []) if p.get("type") in ("output_text", "text")).strip()
        return True, f"Working. {c['model']} answered “{text[:60]}” in {time.time() - t0:.1f}s on the ChatGPT plan ({len(models)} models available)."
    except (PlanError, RuntimeError) as e:
        return False, str(e)
