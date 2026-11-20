"""Claude answers end to end, with the Anthropic API faked: the request (web search on, the shopper's country), the answer
read after the last search, citations and sites only read, brands judged by Haiku through a tool, cost per answer, scores under
engine 'claude' next to ChatGPT's, the monthly spend limit, a refused key stopping the run, a paused turn continued, retries,
the worker step and its once-a-day limit, and the Integrations card. Needs ADMIN_DATABASE_URL."""
import os, sys, json, re
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "webapp"), os.path.join(ROOT, "scripts")]
import psycopg

admin = os.environ["ADMIN_DATABASE_URL"]
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS aeo_claude")
    if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='site_owner'").fetchone():
        c.execute("CREATE ROLE site_owner LOGIN PASSWORD 'owner-pw' CREATEROLE NOSUPERUSER")
    c.execute("CREATE DATABASE aeo_claude OWNER site_owner")
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname='aeo_app'").fetchone():
        c.execute("GRANT aeo_app TO site_owner WITH ADMIN OPTION")
owner = re.sub(r"postgresql://[^@]*@/[^?]*", "postgresql://site_owner:owner-pw@/aeo_claude", admin)
from start_local import migrate
with psycopg.connect(owner, autocommit=True) as c: migrate(c)
from cryptography.fernet import Fernet
os.environ.update(DATABASE_URL=owner, DB_SET_ROLE="aeo_app", FLASK_SECRET="t", TOKEN_KEY=Fernet.generate_key().decode(),
                  GOOGLE_OAUTH_CLIENT_ID="x", GOOGLE_OAUTH_CLIENT_SECRET="x", OAUTH_REDIRECT_URI="http://localhost/cb",
                  ANTHROPIC_API_KEY="sk-ant-test")
os.environ.pop("VERCEL", None)
from common import connect
import claude_answers as CA, ai_visibility as AV

A = "44444444-0000-0000-0000-00000000000a"
with connect() as conn:
    conn.execute("INSERT INTO clients (client_id, name, domain) VALUES (%s,'Gargi','gargi.shop')", [A])
    conn.execute("INSERT INTO competitors (client_id, name, domain, tracked) VALUES (%s,'GIVA','giva.co',true), (%s,'Palmonas','palmonas.com',true)", [A, A])
    for t in ("best silver jewellery brands in india", "is 925 silver jewellery worth buying", "best place to buy silver anklets online"):
        conn.execute("INSERT INTO prompts (client_id, text, approved, region) VALUES (%s,%s,true,'IN')", [A, t])

class R:
    def __init__(s, status, body, headers=None): s.status_code, s._b, s.headers, s.text = status, body, headers or {}, json.dumps(body)
    def json(s): return s._b

ANSWER = {"id": "msg_1", "model": "claude-sonnet-5-5", "stop_reason": "end_turn", "content": [
    {"type": "text", "text": "I'll look up current options for you."},
    {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "best silver jewellery brands india"}},
    {"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": [
        {"type": "web_search_result", "url": "https://www.giva.co/silver", "title": "GIVA silver", "encrypted_content": "x"},
        {"type": "web_search_result", "url": "https://www.reddit.com/r/india/comments/abc", "title": "thread", "encrypted_content": "y"},
        {"type": "web_search_result", "url": "https://gargi.shop/collections/silver", "title": "Gargi", "encrypted_content": "z"}]},
    {"type": "text", "text": "Popular choices include "},
    {"type": "text", "text": "GIVA, known for 925 silver", "citations": [{"type": "web_search_result_location", "url": "https://www.giva.co/silver",
                                                                         "title": "GIVA silver", "cited_text": "925 silver"}]},
    {"type": "text", "text": ", and Gargi by PN Gadgil, which offers hallmarked pieces.", "citations": [
        {"type": "web_search_result_location", "url": "https://gargi.shop/collections/silver", "title": "Gargi", "cited_text": "hallmarked"}]}],
    "usage": {"input_tokens": 9000, "output_tokens": 800, "server_tool_use": {"web_search_requests": 1}}}
JUDGE = {"model": "claude-haiku-5-5", "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "tu", "name": "record_brands", "input": {"brands": [
    {"name": "GIVA", "sentiment": "neutral", "recommended": True}, {"name": "Gargi", "sentiment": "positive", "recommended": True},
    {"name": "Tanishq", "sentiment": "neutral", "recommended": False}]}}], "usage": {"input_tokens": 300, "output_tokens": 120}}

class Fake:
    def __init__(s): s.calls, s.mode, s.queue = [], "ok", []
    def post(s, url, headers=None, json=None, timeout=None):
        assert url == CA.API and headers["x-api-key"] == "sk-ant-test" and headers["anthropic-version"] == "2023-06-01"
        s.calls.append(json)
        if s.queue: return s.queue.pop(0)
        if s.mode == "badkey": return R(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
        if s.mode == "nocredit": return R(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                                            "message": "Your credit balance is too low to access the Anthropic API."}})
        if json.get("tool_choice"): return R(200, JUDGE)
        return R(200, ANSWER)

# ---------------------------------------------------------------- pieces
answer, cited, consulted = CA.parse(ANSWER)
assert answer == "Popular choices include GIVA, known for 925 silver, and Gargi by PN Gadgil, which offers hallmarked pieces.", answer
assert [u for u, _ in cited] == ["https://www.giva.co/silver", "https://gargi.shop/collections/silver"]
assert consulted == ["https://www.reddit.com/r/india/comments/abc"]
c1 = CA.cost("claude-sonnet-5-5", ANSWER["usage"]); assert abs(c1 - (9000 * 2 + 800 * 10) / 1e6 - 0.01) < 1e-9, c1
assert abs(CA.cost("claude-haiku-5-5", JUDGE["usage"]) - (300 * 0.10 + 120 * 0.50) / 1e6) < 1e-12
assert CA.cost("claude-haiku-5-5", {"input_tokens": 200_000, "output_tokens": 0}) == round(200_000 * 0.50 / 1e6, 6)   # long-prompt rate
print("pieces: answer read after the last search, cited vs only read, cost at published rates (incl. searches and Haiku's long-prompt rate)")

# ---------------------------------------------------------------- a run
F = Fake()
CA.time.sleep = lambda s: None
with connect() as conn:
    tally, scores = CA.sync_client(conn, A, "sk-ant-test", session=F)
    assert tally == {"ok": 3}, tally
    ask = F.calls[0]
    assert ask["model"] == "claude-sonnet-5-5" and ask["messages"] == [{"role": "user", "content": "best silver jewellery brands in india"}]
    tool = ask["tools"][0]
    assert tool["type"] == "web_search_20260318" and tool["name"] == "web_search" and tool["max_uses"] == 3
    assert tool["user_location"] == {"type": "approximate", "country": "IN"} and tool["allowed_callers"] == ["direct"]
    assert F.calls[1]["model"] == "claude-haiku-5-5" and F.calls[1]["tool_choice"] == {"type": "tool", "name": "record_brands"}
    assert conn.execute("SELECT count(*) FROM runs WHERE client_id=%s AND engine='claude' AND status='ok'", [A]).fetchone()[0] == 3
    m = conn.execute("""SELECT brand, is_client, rank_in_answer, sentiment, recommended FROM mentions WHERE client_id=%s
                        ORDER BY answer_id, rank_in_answer NULLS LAST LIMIT 3""", [A]).fetchall()
    assert m[0][:3] == ("GIVA", False, 1) and m[1][:4] == ("Gargi", True, 2, "positive") and m[2][0] == "Tanishq" and m[2][2] is None
    cit = conn.execute("SELECT DISTINCT domain, cited, is_client, source_type FROM citations WHERE client_id=%s", [A]).fetchall()
    assert ("gargi.shop", True, True, "own_site") in cit and ("giva.co", True, False, "competitor") in cit
    assert conn.execute("SELECT count(*) FROM citations WHERE client_id=%s AND NOT cited AND source_type='reddit'", [A]).fetchone()[0] == 3
    meta = conn.execute("SELECT raw_json, engine_version FROM answers LIMIT 1").fetchone()
    assert meta[1] == "claude-sonnet-5-5" and meta[0]["searches"] == 1 and meta[0]["via"] == "api"
    assert abs(meta[0]["cost_usd"] - (c1 + CA.cost("claude-haiku-5-5", JUDGE["usage"]))) < 1e-6
    sc = dict(conn.execute("SELECT brand, visibility_score FROM scores_daily WHERE client_id=%s AND engine='claude'", [A]).fetchall())
    assert set(sc) == {"", "GIVA", "Palmonas"} and float(sc[""]) > 0 and float(sc["Palmonas"]) == 0
    assert not conn.execute("SELECT 1 FROM scores_daily WHERE engine='chatgpt'").fetchone()        # ChatGPT's numbers untouched
    assert abs(CA.total_spend(conn) - 3 * meta[0]["cost_usd"]) < 1e-5
    led = conn.execute("SELECT count(*), sum(searches) FROM api_spend WHERE provider='anthropic' AND client_id=%s", [A]).fetchone()
    assert led == (6, 3)                                                 # every billed reply (3 answers + 3 judges) in the ledger
    again = CA.sync_client(conn, A, "sk-ant-test", session=F)[0]
    assert again == {"skipped": 3}                                       # once per prompt per day; no paid repeats
print("run: three prompts asked with web search for India, brands and order, Haiku's verdicts, own and competitor citations, "
      "Reddit read, cost kept per answer, scores under 'claude' only; a second run the same day asks nothing")

# ---------------------------------------------------------------- the strict limits
import datetime as dt
os.environ["CLAUDE_MAX_USD"] = "50"; assert CA.total_cap() == 10.0          # can be lowered, never raised above $10
os.environ["CLAUDE_MAX_USD"] = "abc"; assert CA.total_cap() == 10.0
os.environ.pop("CLAUDE_MAX_USD")
with connect() as conn:
    spent = CA.total_spend(conn)
    conn.execute("INSERT INTO api_spend (provider, client_id, model, usd) VALUES ('anthropic',%s,'x',%s)", [A, 10 - spent - 0.10])
    F.calls.clear()                                                      # $9.90 used: one more prompt's worst case would cross $10
    t = CA.sync_client(conn, A, "sk-ant-test", session=F, cycle=dt.date.today() + dt.timedelta(days=1))[0]
    assert "spend limit is reached" in t["stopped"] and F.calls == [] and "ok" not in t, t
    assert CA.status(conn)["room"] is False
    try: conn.execute("DELETE FROM api_spend WHERE model='x'"); raise AssertionError("ledger must be append-only for the app")
    except psycopg.errors.InsufficientPrivilege: pass
    with psycopg.connect(admin.replace("/postgres?", "/aeo_claude?"), autocommit=True) as oc:      # a superuser clears it
        assert oc.execute("DELETE FROM api_spend WHERE model='x'").rowcount == 1
    os.environ["CLAUDE_RUN_USD"] = "0.2"                                 # per click: stops once this run could cross it
    t = CA.sync_client(conn, A, "sk-ant-test", session=F, cycle=dt.date.today() + dt.timedelta(days=4))[0]
    assert t.get("ok") == 2 and "run reached its $0.2 limit" in t["stopped"], t      # 2 prompts fit; a 3rd could cross $0.20
    os.environ.pop("CLAUDE_RUN_USD")
    assert CA.total_spend(conn) < 10
print("limits: never above $10 in total (env can only lower it), stops before a prompt could cross it, and at most $2 per click")

# ---------------------------------------------------------------- refused key / no credit stop the run after one try
for mode, words in (("badkey", "key was refused"), ("nocredit", "run out of credit")):
    with connect() as conn:
        F.mode, F.calls[:] = mode, []
        t = CA.sync_client(conn, A, "sk-ant-test", session=F, cycle=dt.date.today() + dt.timedelta(days=2 if mode == "badkey" else 3))[0]
        assert t.get("error") == 1 and words in t["stopped"] and len(F.calls) == 1, (mode, t, len(F.calls))
        err = conn.execute("SELECT error FROM runs WHERE engine='claude' AND status='error' ORDER BY run_at DESC LIMIT 1").fetchone()[0]
        assert words in err
F.mode = "ok"
print("refused key or no credit: the first prompt records why and the rest are not asked")

# ---------------------------------------------------------------- busy API retried; paused turn continued
PAUSED = dict(ANSWER, stop_reason="pause_turn", content=ANSWER["content"][:3], usage={"input_tokens": 5000, "output_tokens": 50,
                                                                                         "server_tool_use": {"web_search_requests": 1}})
REST = dict(ANSWER, content=ANSWER["content"][3:], usage={"input_tokens": 6000, "output_tokens": 700})
F.queue = [R(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}), R(200, PAUSED), R(200, REST)]
F.calls.clear()
j = CA.ask(F, "sk-ant-test", "best silver jewellery brands in india")
assert len(F.calls) == 3 and F.calls[2]["messages"][-1] == {"role": "assistant", "content": PAUSED["content"]}
assert CA.parse(j)[0].startswith("Popular choices include GIVA") and j["usage"]["input_tokens"] == 11000
assert j["usage"]["server_tool_use"]["web_search_requests"] == 1
print("busy API: retried; a paused long turn is continued and its usage summed")

# ---------------------------------------------------------------- worker: a paid step, once a day per brand
import worker
assert "claude" in worker.PAID_STEPS and "claude" not in worker.NIGHTLY.split(",") and "claude" in worker.BUTTON_ONLY
assert worker.ORDER.index("claude") == worker.ORDER.index("ai") + 1
with connect() as conn:
    assert worker.paid_ok(conn, A, "claude")[0] is True and worker.paid_ok(conn, A, "claude")[0] is False
    assert worker.paid_next(conn, A, "claude")[0] is False and worker.paid_next(conn, A, "ai")[0] is True     # ChatGPT's limit separate
with connect() as conn:
    q = worker.nightly(conn, "nightly"); args = conn.execute("SELECT args FROM pull_queue WHERE pull_id=%s", [q]).fetchone()[0]
    assert "claude" not in ",".join(args)
    os.environ["PAID_RUNS_PER_DAY"] = "2"; conn.execute("UPDATE pull_queue SET requested_at = now() - interval '11 hours'")
    m = worker.nightly(conn, "midday"); margs = conn.execute("SELECT args FROM pull_queue WHERE pull_id=%s", [m]).fetchone()[0]
    assert "claude" not in ",".join(margs) and "ai" in ",".join(margs); os.environ.pop("PAID_RUNS_PER_DAY")
    assert "claude" not in worker.ai_due(conn, A)
print("worker: Claude never runs on a schedule or on page open, only from its button, at most once a day per brand")

# ---------------------------------------------------------------- Integrations card
import app as webapp, dashboard
ran = []; dashboard.run_daily = lambda args, out=None: ran.append(args)
cl = webapp.app.test_client()
with cl.session_transaction() as s: s["staff_email"] = "seo@hovers.in"; s["csrf"] = "tok"
page = cl.get(f"/c/{A}/integrations").get_data(as_text=True)
assert "Claude" in page and "Check Claude answers now" in page and "of the $10 limit used" in page and "claude-sonnet-5-5" in page
r = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "run_claude"})
assert "err=" in r.headers["Location"] and "already+ran+today" in r.headers["Location"]          # the paid_ok above used today's run
with connect() as conn: conn.execute("DELETE FROM paid_runs WHERE client_id=%s AND kind='claude'", [A])
r = cl.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "run_claude"})
assert "msg=Asking+Claude+3+prompts" in r.headers["Location"] and ran[-1][:4] == ["--client-id", A, "--only", "claude"]
v = webapp.app.test_client()
with v.session_transaction() as s: s["viewer_email"] = "c@gargi.shop"; s["viewer_clients"] = [A]; s["csrf"] = "tok"
vp = v.get(f"/c/{A}/integrations").get_data(as_text=True)
assert "Claude" in vp and "Check Claude answers now" not in vp and "limit used" not in vp and "ANTHROPIC" not in vp and "claude-sonnet" not in vp
assert v.post(f"/c/{A}/integrations", data={"csrf": "tok", "what": "run_claude"}).status_code == 403
os.environ.pop("ANTHROPIC_API_KEY")
page = cl.get(f"/c/{A}/integrations").get_data(as_text=True)
assert "Not connected yet" in page and "ANTHROPIC_API_KEY" in page                              # staff see where to add it
print("Integrations: staff see the Claude card, spend and the run button (once a day); clients see the status only")

# ---------------------------------------------------------------- blog drafts written by Claude (the Generate button)
import claude_blogs as CB, blog_writer as BW
POST = {"title": "How to care for silver anklets", "meta_title": "Silver anklet care", "meta_description": "x" * 150, "slug": "silver-anklet-care",
        "excerpt": "Keep them bright.", "tags": ["silver"], "body_html": "<h2>Why does silver tarnish?</h2><p>Air.</p>", "faqs": [], "sources": []}
class BlogFake:
    def __init__(s, script): s.calls, s.script = [], list(script)
    def post(s, url, headers=None, json=None, timeout=None):
        s.calls.append(json); return R(200, s.script.pop(0))
search_then_text = {"model": "claude-sonnet-5-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Here is the post..."}],
                    "usage": {"input_tokens": 20000, "output_tokens": 3000, "server_tool_use": {"web_search_requests": 4}}}
tool = {"model": "claude-sonnet-5-5", "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": "submit_post", "input": POST}],
        "usage": {"input_tokens": 23000, "output_tokens": 2500}}
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
with connect() as conn:
    before = CA.total_spend(conn)
    f = BlogFake([search_then_text, tool])
    out = BW.ask(f, CB.ClaudeWriter(conn, A), "system rules", "Primary keyword: silver anklet care")
    assert out == POST and len(f.calls) == 2
    b0, b1 = f.calls
    assert b0["model"] == "claude-sonnet-5-5" and b0["system"] == "system rules" and {t["name"] for t in b0["tools"]} == {"web_search", "submit_post"}
    assert b0["tools"][1]["input_schema"] == BW.DRAFT_SCHEMA and "tool_choice" not in b0
    assert b1["tool_choice"] == {"type": "tool", "name": "submit_post"} and b1["messages"][-1]["content"].startswith("Now call submit_post")
    spent = CB.blog_spend(conn)
    assert abs(spent - (CA.cost("claude-sonnet-5-5", search_then_text["usage"]) + CA.cost("claude-sonnet-5-5", tool["usage"]))) < 1e-6
    assert abs(CA.total_spend(conn) - before - spent) < 1e-6                                           # counts toward the $10 too
    assert conn.execute("SELECT count(*) FROM api_spend WHERE purpose='blogs'").fetchone()[0] == 2
    assert CB.blog_cap() == 2.5
    os.environ["CLAUDE_BLOG_MAX_USD"] = "50"; assert CB.blog_cap() == 2.5                              # env can only lower it
    os.environ["CLAUDE_BLOG_MAX_USD"] = str(round(spent + 0.2, 4))
    f2 = BlogFake([tool])
    try: BW.ask(f2, CB.ClaudeWriter(conn, A), "s", "u"); raise AssertionError("should stop")
    except CA.ClaudeError as e: assert "blog limit is reached" in str(e) and f2.calls == []             # stops before any request
    os.environ.pop("CLAUDE_BLOG_MAX_USD")
print("blogs on Claude: web search + submit_post tool, a text reply is asked again for the tool only, every reply in the ledger as 'blogs',"
      " counted in the $10 too, a $2.50 blog cap the env can only lower, checked before any request")
print("ALL CLAUDE TESTS PASSED")
