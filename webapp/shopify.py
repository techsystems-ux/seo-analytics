"""Shopify publishing for Execution: connect a store (OAuth, expiring offline tokens), pick its blog, publish articles.

Two ways a store connects:
  shared Orbit app   SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET (Vercel settings): a public-distribution app in Hovers'
                     Shopify Dev Dashboard; any store can install it
  per-store app      a custom-distribution app made for one client store; its client ID and secret are saved on the
                     brand's Publishing screen by the Hovers team (encrypted with TOKEN_KEY)
Scopes: read_content, write_content (blogs and articles only). Tokens are encrypted at rest and refreshed before they expire.
"""
import datetime as dt, hashlib, hmac, os, re
from urllib.parse import urlencode
import requests
import google_auth as ga                       # encrypt / decrypt with TOKEN_KEY

SCOPES = "read_content,write_content"
VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-10")
SHOP_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$")

class Reconnect(Exception):
    """The store's access is gone (expired refresh token, app removed): someone has to connect it again."""

def valid_shop(shop):
    return bool(SHOP_RE.match(shop or ""))

def normalise_shop(s):
    s = (s or "").strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
    if s and "." not in s: s += ".myshopify.com"
    return s

def detect_shop(domain, session=None):
    """The store's xxx.myshopify.com address, read from its own home page (Shopify themes print it). None if not Shopify."""
    try:
        r = (session or requests).get(f"https://{domain.strip('/')}", timeout=10, headers={"User-Agent": "Mozilla/5.0 Orbit"})
        m = re.search(r'Shopify\.shop\s*=\s*["\']([a-z0-9\-]+\.myshopify\.com)', r.text) or \
            re.search(r'"myshopifyDomain"\s*:\s*"([a-z0-9\-]+\.myshopify\.com)"', r.text)
        return m.group(1) if m else None
    except Exception:
        return None

def app_creds(row=None):
    """(client_id, secret) for this brand: its own app if saved, else the shared Orbit app, else (None, None)."""
    if row and row.get("app_client_id") and row.get("app_secret_enc"):
        return row["app_client_id"], ga.decrypt(row["app_secret_enc"])
    return os.environ.get("SHOPIFY_CLIENT_ID"), os.environ.get("SHOPIFY_CLIENT_SECRET")

def authorize_url(shop, client_id, redirect_uri, state):
    return f"https://{shop}/admin/oauth/authorize?" + urlencode({"client_id": client_id, "scope": SCOPES,
                                                                 "redirect_uri": redirect_uri, "state": state})

def verify_hmac(args, secret):
    """Shopify signs the callback: HMAC-SHA256 over the other parameters, sorted, joined as key=value with &."""
    given = args.get("hmac", "")
    msg = "&".join(f"{k}={v}" for k, v in sorted((k, v) for k, v in args.items() if k not in ("hmac", "signature")))
    good = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return bool(given) and hmac.compare_digest(good, given)

def _tokens(shop, data):
    r = requests.post(f"https://{shop}/admin/oauth/access_token", data=data, timeout=20,
                      headers={"Accept": "application/json"})
    if r.status_code == 401: raise Reconnect("Shopify refused the stored access. Connect the store again.")
    if r.status_code != 200: raise RuntimeError(f"Shopify {r.status_code}: {r.text[:200]}")
    j = r.json(); now = dt.datetime.now(dt.timezone.utc)
    return {"access_token": j["access_token"], "refresh_token": j.get("refresh_token"), "scope": j.get("scope", ""),
            "expires_at": now + dt.timedelta(seconds=int(j["expires_in"])) if j.get("expires_in") else None,
            "refresh_expires_at": now + dt.timedelta(seconds=int(j["refresh_token_expires_in"])) if j.get("refresh_token_expires_in") else None}

def exchange_code(shop, client_id, secret, code):
    return _tokens(shop, {"client_id": client_id, "client_secret": secret, "code": code, "expiring": "1"})

def refresh(shop, client_id, secret, refresh_token):
    return _tokens(shop, {"client_id": client_id, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": refresh_token})

def gql(shop, token, query, variables=None):
    r = requests.post(f"https://{shop}/admin/api/{VERSION}/graphql.json", json={"query": query, "variables": variables or {}},
                      headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"}, timeout=30)
    if r.status_code == 401: raise Reconnect("Shopify no longer accepts Orbit's access. Connect the store again.")
    if r.status_code == 403: raise RuntimeError("Shopify refused the request (403). If the app has an IP allowlist, check that it allows Orbit's server.")
    if r.status_code != 200: raise RuntimeError(f"Shopify {r.status_code}: {r.text[:200]}")
    j = r.json()
    if j.get("errors"): raise RuntimeError("Shopify: " + "; ".join(e.get("message", "") for e in j["errors"])[:300])
    return j["data"]

COLS = ["shop", "app_client_id", "app_secret_enc", "access_token_enc", "refresh_token_enc", "expires_at", "refresh_expires_at",
        "blog_id", "blog_handle", "blog_title", "blogs", "author_name", "primary_domain", "status", "last_error", "connected_by", "connected_at"]

def connection(conn, client_id):
    r = conn.execute(f"SELECT {', '.join(COLS)} FROM cms_connections WHERE client_id=%s AND platform='shopify'", [client_id]).fetchone()
    return dict(zip(COLS, r)) if r else None

def save_tokens(conn, client_id, shop, tok, who):
    conn.execute("""INSERT INTO cms_connections (client_id, platform, shop, access_token_enc, refresh_token_enc, expires_at, refresh_expires_at,
                        scopes, status, last_error, connected_by, connected_at, updated_at)
                    VALUES (%s,'shopify',%s,%s,%s,%s,%s,%s,'active',NULL,%s,now(),now())
                    ON CONFLICT (client_id, platform) DO UPDATE SET shop=EXCLUDED.shop, access_token_enc=EXCLUDED.access_token_enc,
                        refresh_token_enc=EXCLUDED.refresh_token_enc, expires_at=EXCLUDED.expires_at, refresh_expires_at=EXCLUDED.refresh_expires_at,
                        scopes=EXCLUDED.scopes, status='active', last_error=NULL, connected_by=EXCLUDED.connected_by, connected_at=now(), updated_at=now()""",
                 [client_id, shop, ga.encrypt(tok["access_token"]), ga.encrypt(tok["refresh_token"]) if tok.get("refresh_token") else None,
                  tok.get("expires_at"), tok.get("refresh_expires_at"), tok.get("scope", ""), who])

def token_for(conn, client_id):
    """A working access token for the brand's store, refreshed first when it is about to expire."""
    c = connection(conn, client_id)
    if not c or not c["access_token_enc"]: raise Reconnect("No Shopify store is connected yet.")
    if c["status"] == "reconnect": raise Reconnect(c["last_error"] or "Connect the store again.")
    soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)
    if c["expires_at"] and c["expires_at"] < soon:
        if not c["refresh_token_enc"]: raise Reconnect("The store's access has expired. Connect it again.")
        cid, secret = app_creds(c)
        try:
            tok = refresh(c["shop"], cid, secret, ga.decrypt(c["refresh_token_enc"]))
        except Reconnect as e:
            conn.execute("UPDATE cms_connections SET status='reconnect', last_error=%s WHERE client_id=%s AND platform='shopify'", [str(e), client_id])
            raise
        conn.execute("""UPDATE cms_connections SET access_token_enc=%s, refresh_token_enc=coalesce(%s, refresh_token_enc), expires_at=%s,
                        refresh_expires_at=coalesce(%s, refresh_expires_at), updated_at=now() WHERE client_id=%s AND platform='shopify'""",
                     [ga.encrypt(tok["access_token"]), ga.encrypt(tok["refresh_token"]) if tok.get("refresh_token") else None,
                      tok["expires_at"], tok.get("refresh_expires_at"), client_id])
        return c["shop"], tok["access_token"]
    return c["shop"], ga.decrypt(c["access_token_enc"])

STORE_Q = """{ shop { name primaryDomain { url } } blogs(first: 25) { nodes { id title handle } } }"""

def load_store(conn, client_id):
    """After connecting: the store's blogs (the first becomes the default) and its public address."""
    shop, token = token_for(conn, client_id)
    d = gql(shop, token, STORE_Q)
    blogs = [{"id": b["id"], "title": b["title"], "handle": b["handle"]} for b in d["blogs"]["nodes"]]
    c = connection(conn, client_id)
    pick = next((b for b in blogs if b["id"] == c.get("blog_id")), None) or (blogs[0] if blogs else None)
    import json
    conn.execute("""UPDATE cms_connections SET blogs=%s, primary_domain=%s, blog_id=%s, blog_handle=%s, blog_title=%s,
                    author_name=coalesce(author_name, %s), updated_at=now() WHERE client_id=%s AND platform='shopify'""",
                 [json.dumps(blogs), d["shop"]["primaryDomain"]["url"].rstrip("/"), pick and pick["id"], pick and pick["handle"],
                  pick and pick["title"], d["shop"]["name"], client_id])
    return blogs

CREATE_BLOG = """mutation($blog: BlogCreateInput!) { blogCreate(blog: $blog) { blog { id title handle } userErrors { field message } } }"""
CREATE = """mutation($article: ArticleCreateInput!) { articleCreate(article: $article) {
  article { id handle blog { handle } } userErrors { code field message } } }"""
UPDATE = """mutation($id: ID!, $article: ArticleUpdateInput!) { articleUpdate(id: $id, article: $article) {
  article { id handle isPublished blog { handle } } userErrors { code field message } } }"""

def _errors(ue):
    return "; ".join(f"{'.'.join(e.get('field') or [])} {e['message']}".strip() for e in ue)

def publish(conn, client_id, d, who):
    """Creates the article on the store (or updates it if it was published before). Returns the live URL."""
    import blogs as B, json
    shop, token = token_for(conn, client_id)
    c = connection(conn, client_id)
    if not c["blog_id"]:
        load_store(conn, client_id); c = connection(conn, client_id)
    if not c["blog_id"]:                                     # a store with no blog yet: make one called News
        out = gql(shop, token, CREATE_BLOG, {"blog": {"title": "News"}})["blogCreate"]
        if out["userErrors"]: raise RuntimeError("Shopify: " + _errors(out["userErrors"]))
        b = out["blog"]
        conn.execute("UPDATE cms_connections SET blog_id=%s, blog_handle=%s, blog_title=%s WHERE client_id=%s AND platform='shopify'",
                     [b["id"], b["handle"], b["title"], client_id]); c = connection(conn, client_id)
    seo = [{"namespace": "global", "key": "title_tag", "type": "single_line_text_field", "value": d["meta_title"] or d["title"]},
           {"namespace": "global", "key": "description_tag", "type": "single_line_text_field", "value": d["meta_description"] or ""}]
    fields = {"title": d["title"], "body": B.article_body(d), "summary": f"<p>{d['excerpt']}</p>" if d.get("excerpt") else None,
              "tags": d.get("tags") or [], "isPublished": True, "metafields": seo}
    if d.get("external_id"):
        out = gql(shop, token, UPDATE, {"id": d["external_id"], "article": fields})["articleUpdate"]
    else:
        handle = d["slug"]
        for attempt in range(2):
            out = gql(shop, token, CREATE, {"article": {**fields, "blogId": c["blog_id"], "handle": handle,
                                                        "author": {"name": c["author_name"] or "Editorial team"}}})["articleCreate"]
            if out["userErrors"] and any("taken" in e["message"].lower() for e in out["userErrors"]) and attempt == 0:
                handle = f"{d['slug']}-2"; continue
            break
    if out["userErrors"]: raise RuntimeError("Shopify: " + _errors(out["userErrors"]))
    a = out["article"]
    base = (c["primary_domain"] or f"https://{shop}").rstrip("/")
    url = f"{base}/blogs/{a['blog']['handle']}/{a['handle']}"
    conn.execute("""UPDATE content_drafts SET status='published', platform='shopify', external_id=%s, published_url=%s, slug=%s,
                    published_by=%s, published_at=now(), error=NULL, updated_at=now() WHERE draft_id=%s""",
                 [a["id"], url, a["handle"], who, d["draft_id"]])
    conn.execute("""INSERT INTO pages (client_id, url, page_type, primary_keyword, secondary_keywords, status)
                    VALUES (%s,%s,'blog',%s,%s,'uploaded') ON CONFLICT (client_id, url) DO NOTHING""",
                 [client_id, url, d["target_keyword"], d.get("secondary_keywords") or []])
    return url

def unpublish(conn, client_id, d):
    shop, token = token_for(conn, client_id)
    out = gql(shop, token, UPDATE, {"id": d["external_id"], "article": {"isPublished": False}})["articleUpdate"]
    if out["userErrors"]: raise RuntimeError("Shopify: " + _errors(out["userErrors"]))
    conn.execute("UPDATE content_drafts SET status='approved', published_at=NULL, updated_at=now() WHERE draft_id=%s", [d["draft_id"]])
