"""Brand matching and the AI dashboard's engine buttons: competitors saved as web addresses are found however an answer
writes them, saved answers are re-read for free when the rules or the tracked brands change, names show as brands, and
every engine button is a link that switches the dashboard (an engine without answers says so). Needs ADMIN_DATABASE_URL."""
import os, sys, json, re
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "collectors"), os.path.join(ROOT, "seo"), os.path.join(ROOT, "webapp"), os.path.join(ROOT, "scripts")]
import psycopg

admin = os.environ["ADMIN_DATABASE_URL"]
with psycopg.connect(admin, autocommit=True) as c:
    c.execute("DROP DATABASE IF EXISTS aeo_match")
    if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='site_owner'").fetchone():
        c.execute("CREATE ROLE site_owner LOGIN PASSWORD 'owner-pw' CREATEROLE NOSUPERUSER")
    c.execute("CREATE DATABASE aeo_match OWNER site_owner")
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname='aeo_app'").fetchone():
        c.execute("GRANT aeo_app TO site_owner WITH ADMIN OPTION")
owner = re.sub(r"postgresql://[^@]*@/[^?]*", "postgresql://site_owner:owner-pw@/aeo_match", admin)
from start_local import migrate
with psycopg.connect(owner, autocommit=True) as c: migrate(c)
from cryptography.fernet import Fernet
os.environ.update(DATABASE_URL=owner, DB_SET_ROLE="aeo_app", FLASK_SECRET="t", TOKEN_KEY=Fernet.generate_key().decode(),
                  GOOGLE_OAUTH_CLIENT_ID="x", GOOGLE_OAUTH_CLIENT_SECRET="x", OAUTH_REDIRECT_URI="http://localhost/cb")
os.environ.pop("VERCEL", None)
from common import connect
import ai_visibility as AV, worker

# ---------------------------------------------------------------- matching
brands = [{"name": "Gargi", "aliases": ["Gargi by PNG"], "domain": "gargi.shop", "is_client": True},
          {"name": "zavya.co", "aliases": [], "domain": "zavya.co", "is_client": False},
          {"name": "truesilver.co.in", "aliases": [], "domain": "truesilver.co.in", "is_client": False},
          {"name": "CaratLane", "aliases": [], "domain": "caratlane.com", "is_client": False},
          {"name": "pngexclusive.com", "aliases": [], "domain": "pngexclusive.com", "is_client": False}]
txt = "Try True Silver for daily wear, Zavya for 925 pieces, Caratlane for gold, and Gargi for hallmarked silver."
assert [m["name"] for m in AV.find_mentions(txt, brands)] == ["truesilver.co.in", "zavya.co", "CaratLane", "Gargi"]
assert [m["name"] for m in AV.find_mentions("TrueSilver and true-silver and ZAVYA", brands)] == ["truesilver.co.in", "zavya.co"]
assert AV.find_mentions("Zavyani is a different word; pngexclusives too", brands) == []
assert AV.same_brand("Zavya", "zavya.co") and AV.same_brand("True Silver", "truesilver.co.in") and not AV.same_brand("Giva", "Gargi")
assert AV.display_name("zavya.co", "zavya.co") == "Zavya" and AV.display_name("CaratLane", "caratlane.com") == "CaratLane"
print("matching: brands saved as web addresses are found as Zavya, True Silver, TrueSilver, ZAVYA; longer words are not")

# ---------------------------------------------------------------- free re-read of saved answers
A = "55555555-0000-0000-0000-00000000000a"
with connect() as conn:
    conn.execute("INSERT INTO clients (client_id, name, domain) VALUES (%s,'Gargi','gargi.shop')", [A])
    conn.execute("INSERT INTO competitors (client_id, name, domain, tracked) VALUES (%s,'zavya.co','zavya.co',true), (%s,'truesilver.co.in','truesilver.co.in',true)", [A, A])
    pid = conn.execute("INSERT INTO prompts (client_id, text, approved, region) VALUES (%s,'best silver brands',true,'IN') RETURNING prompt_id", [A]).fetchone()[0]
    rid = conn.execute("""INSERT INTO runs (client_id, prompt_id, engine, region, sample_no, cycle, status, run_at)
                          VALUES (%s,%s,'chatgpt','IN',1,current_date,'ok',now()) RETURNING run_id""", [A, pid]).fetchone()[0]
    aid = conn.execute("INSERT INTO answers (client_id, run_id, full_text, parsed_at) VALUES (%s,%s,%s,now()) RETURNING answer_id",
                       [A, rid, "Top picks: Zavya, then True Silver. Gargi also makes hallmarked silver."]).fetchone()[0]
    # as an older version saved it: the competitors were missed and kept only as untracked names with the judge's verdicts
    for b, s in (("Zavya", "positive"), ("True Silver", "neutral")):
        conn.execute("INSERT INTO mentions (client_id, answer_id, brand, is_client, rank_in_answer, sentiment, recommended) VALUES (%s,%s,%s,false,NULL,%s,true)", [A, aid, b, s])
    conn.execute("INSERT INTO mentions (client_id, answer_id, brand, is_client, rank_in_answer, sentiment, recommended) VALUES (%s,%s,'Gargi',true,1,'positive',true)", [A, aid])
    assert AV.rescore_due(conn, A)
    t0 = conn.execute("SELECT now()").fetchone()[0]
    worker.ensure_fresh(conn, A)                                          # opening the brand re-reads small histories at once
    assert not AV.rescore_due(conn, A)
    # an AI refresh queued in the last 2 hours does not stop other steps from starting
    conn.execute("DELETE FROM score_versions WHERE client_id=%s", [A])
    conn.execute("UPDATE answers SET full_text=full_text WHERE client_id=%s", [A])
    note, _ = worker.run_task(conn, {"task_id": None, "pull_id": None, "client_id": A, "step": "rescore", "args": {}, "seq": 0, "attempts": 1}, 9e9)
    assert note == "1 answers re-read", note
    got = conn.execute("SELECT brand, is_client, rank_in_answer, sentiment FROM mentions WHERE answer_id=%s ORDER BY rank_in_answer NULLS LAST", [aid]).fetchall()
    assert got == [("zavya.co", False, 1, "positive"), ("truesilver.co.in", False, 2, "neutral"), ("Gargi", True, 3, "positive")], got
    sc = dict(conn.execute("SELECT brand, visibility_score FROM scores_daily WHERE client_id=%s AND engine='chatgpt'", [A]).fetchall())
    assert float(sc["zavya.co"]) > float(sc["truesilver.co.in"]) > 0 and float(sc[""]) > 0, sc
    assert not AV.rescore_due(conn, A)
print("re-read: saved answers counted again with the current rules (verdicts kept), scores recomputed, nothing asked again")

# ---------------------------------------------------------------- the dashboard
import app as webapp
cl = webapp.app.test_client()
with cl.session_transaction() as s: s["staff_email"] = "seo@hovers.in"; s["csrf"] = "tok"
page = cl.get(f"/c/{A}").get_data(as_text=True)
links = re.findall(r'<a class="eng[^"]*" href="([^"]+)"', page)
assert len(links) == 9 and all("e=" in l for l in links), links
assert re.search(r'<a class="eng on" href="[^"]*e=chatgpt', page) and "Zavya" in page and ">zavya.co<" not in page
assert "no answers from" not in page                                    # ChatGPT has answers
gem = cl.get(f"/c/{A}?e=gemini").get_data(as_text=True)
assert re.search(r'<a class="eng off on" href="[^"]*e=gemini', gem) and "no answers from Gemini yet" in gem and "GEMINI_API_KEY" in gem
comp = cl.get(f"/c/{A}?e=gemini&tab=competitors").get_data(as_text=True)
assert "e=gemini" in re.search(r'href="([^"]*tab=competitors[^"]*)"', comp).group(1)  # the choice carries across tabs
# a client sees the explanation without the team note
with connect() as conn: pass
v = webapp.app.test_client()
with v.session_transaction() as s: s["viewer_email"] = "c@gargi.shop"; s["viewer_clients"] = [A]; s["csrf"] = "tok"
vg = v.get(f"/c/{A}?e=gemini").get_data(as_text=True)
assert "no answers from Gemini yet" in vg and "GEMINI_API_KEY" not in vg
# changing competitors re-reads at once
r = cl.post(f"/c/{A}/competitors", data={"csrf": "tok", "add_name": "CaratLane", "add_domain": "caratlane.com"})
with connect() as conn:
    assert conn.execute("SELECT version FROM score_versions WHERE client_id=%s", [A]).fetchone()[0] == AV.SCORING_VERSION
print("dashboard: every engine button is a link, the chosen one is marked and kept across tabs; an empty engine says so "
      "(the team sees what it needs); competitors show as brand names; adding one re-reads the answers")
# ---------------------------------------------------------------- open models through NVIDIA
import open_models as OM
class NR:
    def __init__(s, status, body): s.status_code, s._b, s.text, s.headers = status, body, json.dumps(body), {}
    def json(s): return s._b
CATALOG = {"data": [{"id": i} for i in ("google/gemma-2-9b-it", "google/gemma-3-27b-it", "google/gemma-3n-e4b-it", "nvidia/embed-qa-4",
                                        "google/codegemma-7b", "microsoft/phi-3-mini-128k-instruct", "microsoft/phi-4-mini-instruct",
                                        "microsoft/phi-4-multimodal-instruct", "deepseek-ai/deepseek-r1", "deepseek-ai/deepseek-v4-flash",
                                        "deepseek-ai/deepseek-coder-6.7b-instruct")]}
class Nv:
    def __init__(s): s.calls = []
    def get(s, url, headers=None, timeout=None):
        assert url == OM.BASE + "/models" and headers["Authorization"] == "Bearer nvapi-test"; return NR(200, CATALOG)
    def post(s, url, headers=None, json=None, timeout=None):
        assert url == OM.BASE + "/chat/completions"; s.calls.append(json)
        if "Reply with JSON only" in json["messages"][0]["content"]:
            return NR(200, {"choices": [{"message": {"content": 'Sure: {"brands":[{"name":"Zavya","sentiment":"positive","recommended":true},'
                                                                 '{"name":"Tanishq","sentiment":"neutral","recommended":false}]}'}}]})
        return NR(200, {"model": json["model"], "id": "x", "usage": {"total_tokens": 300}, "choices": [{"message": {
            "content": "<think>shopper in India...</think>For silver, Zavya and True Silver are popular; Tanishq also sells silver."}}]})
os.environ["NVIDIA_GOOGLE_KEY"] = "nvapi-test"
OM._models.clear()
nv = Nv()
assert OM.pick_model(nv, "nvapi-test", "gemma") == "google/gemma-3-27b-it"
assert OM.pick_model(nv, "nvapi-test", "phi") == "microsoft/phi-4-mini-instruct"
assert OM.pick_model(nv, "nvapi-test", "deepseek") == "deepseek-ai/deepseek-v4-flash"
with connect() as conn:
    tally, _ = OM.sync_client(conn, A, "gemma", session=nv)
    assert tally == {"ok": 1}, tally
    ask = nv.calls[0]
    assert ask["model"] == "google/gemma-3-27b-it" and ask["messages"][0]["role"] == "system" and "India" in ask["messages"][0]["content"]
    row = conn.execute("""SELECT a.full_text, a.engine_version, a.raw_json FROM answers a JOIN runs r USING (run_id) WHERE r.engine='gemma'""").fetchone()
    assert row[0].startswith("For silver, Zavya") and "<think>" not in row[0] and row[1] == "google/gemma-3-27b-it" and row[2]["via"] == "nvidia"
    ms = conn.execute("""SELECT brand, rank_in_answer, sentiment FROM mentions m JOIN answers a USING (answer_id) JOIN runs r USING (run_id)
                         WHERE r.engine='gemma' ORDER BY rank_in_answer NULLS LAST""").fetchall()
    assert ms[0] == ("zavya.co", 1, "positive") and ms[1][:2] == ("truesilver.co.in", 2) and ms[-1][0] == "Tanishq" and ms[-1][1] is None, ms
    assert conn.execute("SELECT 1 FROM scores_daily WHERE client_id=%s AND engine='gemma'", [A]).fetchone()
    assert not conn.execute("SELECT 1 FROM citations c JOIN answers a USING (answer_id) JOIN runs r USING (run_id) WHERE r.engine='gemma'").fetchone()
    conn.execute("DELETE FROM pull_tasks"); conn.execute("DELETE FROM pull_queue")
    due = worker.ai_due(conn, A)
    assert "gemma" not in due and "phi" in due and "deepseek" in due and "claude" not in due, due   # gemma answered today
    # a ChatGPT refresh queued a moment ago does not stop the open models from starting when the brand is opened
    worker.enqueue(conn, A, "auto", ["--client-id", A, "--only", "ai"], "t")
    t2 = conn.execute("SELECT now()").fetchone()[0]
    worker.ensure_fresh(conn, A)
    new = {r[0] for r in conn.execute("""SELECT t.step FROM pull_tasks t JOIN pull_queue q USING (pull_id)
                                        WHERE t.client_id=%s AND q.requested_at >= %s""", [A, t2])}
    assert {"phi", "deepseek"} <= new and "ai" not in new, new
    assert worker.ensure_fresh(conn, A) is None                            # and nothing is queued twice
print("open models: newest instruction model per family picked from NVIDIA's list, the shopper's country given, a reasoning "
      "block dropped, brands read like ChatGPT (no citations: no web search), and they run by themselves once a day")

page = cl.get(f"/c/{A}/ai").get_data(as_text=True)
import html as _h; heads = [_h.unescape(h) for h in re.findall(r"<th class=\"e\"[^>]*title=\"([^\"]+)\"", page)]
assert "Gemma (Google's open model)" in heads and "Phi (Microsoft's open model)" in heads and not any("Gemini" == h for h in heads), heads
assert re.search(r'<a class="ck r" href="/c/%s/ai/answer/' % A, page)               # cells open that engine's answer
assert "Not connected yet: AI Overviews, Gemini, Perplexity, Copilot" in page
assert "zavya.co, truesilver.co.in" in page or "Zavya" in page
print("prompts table: a column per connected engine (Gemini, Perplexity, Copilot listed as not connected), each cell opens that answer")
print("ALL MATCHING TESTS PASSED")
