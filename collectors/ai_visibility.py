#!/usr/bin/env python3
"""AI visibility runner (ChatGPT through the OpenAI API, web search on).

For every approved prompt it asks the engine, stores the answer, then records:
  mentions   which tracked brands the answer names, in what order, with what sentiment
  citations  which links the answer cites, and which it only read while searching
Then it writes the Orbit AI Visibility Score for the client and each tracked competitor (scores_daily).

Orbit AI Visibility Score (OVS), 0-100, per brand per engine per region per day. Full method: docs, "Orbit AI Visibility Score".
  Evidence: the newest answer to every active prompt (per sample) from the last 14 days, up to the day scored.
  Per answer, for a brand:  v = named * (0.50 D + 0.20 E + 0.15 S) + 0.15 C
      D  prominence, DCG decay on the brand's place among the brands named: 1 / log2(place + 1)  (1st 1.0, 2nd .63, 3rd .50)
      E  endorsement: the answer recommends it (1) or only names it (0)
      S  sentiment: positive 1, neutral .5, negative 0
      C  source authority: the answer cites the brand's own site (engines without web search: dropped, the rest rescaled)
  Per prompt weight w: buying stage (awareness .8, consideration 1, decision 1.25) x 0.25 when the prompt itself names the brand.
  Score = 100 * sum(w v) / (sum(w) + K), K = 2: two answers' worth of 'not yet seen' so thin evidence can't read as dominance.
  Share of voice: the brand's share of all prominence (w * D) earned by tracked brands.
Bands: 0-14 Not shortlisted, 15-34 Emerging, 35-59 Competitive, 60+ Leader.

The API answer approximates what ChatGPT shows users; it is not a scrape of chatgpt.com.
Usage: python ai_visibility.py --client-id UUID [--samples 1] [--date 2026-11-12]
When the brand has connected its own ChatGPT plan (chatgpt_plan.py), the plan answers and the API key is the backup.
Env: OPENAI_API_KEY_2, OPENAI_MODEL (default below), AI_SENTIMENT=0 to skip the sentiment call
"""
import argparse, datetime as dt, json, os, re, sys
from urllib.parse import urlparse
import requests
from common import connect
import chatgpt_plan

API = "https://api.openai.com/v1/responses"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-astra")
WEIGHTS = {"prominence": 0.50, "endorsement": 0.20, "sentiment": 0.15, "authority": 0.15}
STAGE = {"awareness": 0.8, "consideration": 1.0, "decision": 1.25}
BRANDED = 0.25            # a prompt that names the brand itself proves little about the brand's visibility
PRIOR_K = 2.0             # evidence prior, in answers
WINDOW_DAYS = 14          # newest answer per prompt within this many days
CITING = {"chatgpt", "claude", "perplexity", "ai_overviews", "gemini", "bing_copilot", "grok", "google_ai_mode"}
BANDS = [(60, "Leader"), (35, "Competitive"), (15, "Emerging"), (0, "Not shortlisted")]
SENT = {"positive": 1.0, "neutral": 0.5, "negative": 0.0}

def band(score):
    return next(name for floor, name in BANDS if (score or 0) >= floor)

def host(url):
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h

def same_site(h, domain):
    d = (domain or "").lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    return bool(d) and (h == d or h.endswith("." + d))

# ------------------------------------------------------------------ talking to the engine
def ask(session, key, prompt, country="IN"):
    body = {"model": MODEL, "input": prompt, "include": ["web_search_call.action.sources"],
            "tools": [{"type": "web_search", "user_location": {"type": "approximate", "country": country}}]}
    j = chatgpt_plan.post(session, key, body, 180)             # key: the API key, or the brand's ChatGPT plan (falls back to the key)
    if not parse_response(j)[0] and isinstance(key, chatgpt_plan.PlanRoute) and key.via == "plan" and key.api_key:
        chatgpt_plan.mark(key.conn, key.client_id, None, "The ChatGPT plan answered with no text " + chatgpt_plan.shape(j) + "; the API key answered.")
        key.via = "api"
        j = chatgpt_plan.api_post(session, key.api_key, body, 180)
    return j

def parse_response(j):
    """-> answer text, cited [(url, title)], consulted [url]"""
    text, cited, consulted = [], [], []
    for item in j.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text"):
                    text.append(part.get("text", ""))
                    for a in part.get("annotations", []):
                        if a.get("type") == "url_citation" and a.get("url"): cited.append((a["url"], a.get("title")))
        elif item.get("type") == "web_search_call":
            for s in ((item.get("action") or {}).get("sources") or []):
                if s.get("url"): consulted.append(s["url"])
    seen, uniq = set(), []
    for u, t in cited:
        k = u.split("#")[0].rstrip("/")
        if k not in seen: seen.add(k); uniq.append((u, t))
    return "\n".join(text).strip(), uniq, [u for u in dict.fromkeys(consulted) if u.split("#")[0].rstrip("/") not in seen]

SENTIMENT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["brands"], "properties": {"brands": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "sentiment", "recommended"],
    "properties": {"name": {"type": "string"}, "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                   "recommended": {"type": "boolean"}}}}}}

def judge_brands(session, key, answer):
    """Second, cheap call without web search: every brand or store the answer names, its sentiment, recommended or not."""
    body = {"model": MODEL, "input": [
        {"role": "system", "content": "List every brand, store or marketplace named in the answer, in order of first appearance. "
                                      "Sentiment is how the answer presents that brand. Do not add brands that are not named."},
        {"role": "user", "content": answer}],
        "text": {"format": {"type": "json_schema", "name": "brands", "schema": SENTIMENT_SCHEMA, "strict": True}}}
    text, _, _ = parse_response(chatgpt_plan.post(session, key, body, 120))
    return json.loads(text).get("brands", [])

# ------------------------------------------------------------------ reading the answer
SCORING_VERSION = 3            # bump when brand detection changes: saved answers are re-read for free (rescore)

def stem(text):
    """'zavya.co' -> 'zavya', 'www.truesilver.co.in' -> 'truesilver', 'CaratLane' -> 'caratlane' (letters and digits only)."""
    t = (text or "").lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
    if "." in t: t = t.split(".")[0]
    return re.sub(r"[^a-z0-9]", "", t)

def brand_patterns(name, aliases, domain):
    """Ways an answer can name the brand: its name and aliases as written, its web address, and the brand word inside a
    name or address written loosely: 'zavya.co' also matches 'Zavya', 'truesilver.co.in' also 'True Silver' or 'TrueSilver'."""
    terms = {name, *(aliases or [])}
    if domain: terms.add(domain.lower().replace("www.", "").split("/")[0])
    terms = sorted({t.strip() for t in terms if t and len(t.strip()) >= 3}, key=len, reverse=True)
    pats = [re.compile(r"(?<![\w])" + re.escape(t).replace(r"\ ", r"[\s-]?") + r"(?![\w])", re.I) for t in terms]
    for st in sorted({stem(t) for t in [name, domain, *(aliases or [])] if t}, key=len, reverse=True):
        if len(st) >= 4 and not st.isdigit():
            pats.append(re.compile(r"(?<![\w])" + r"[\s.-]?".join(re.escape(ch) for ch in st) + r"(?![\w])", re.I))
    return pats

def display_name(name, domain):
    """A competitor saved as a web address is shown by its brand word: 'zavya.co' -> 'Zavya'."""
    if name and "." in name and " " not in name and stem(name):
        return stem(name).capitalize()
    return name

def find_mentions(answer, brands):
    """brands = [{name, aliases, domain, is_client}] -> [{name, is_client, rank}] in order of first appearance."""
    spans = [[(m.start(), m.end()) for p in brand_patterns(b["name"], b["aliases"], b["domain"]) for m in p.finditer(answer)] for b in brands]
    hits = []
    for i, b in enumerate(brands):
        # a match inside a longer match of another brand is that brand's: "PNG" in "PNG Brothers" is not Gargi
        own = [s for s in spans[i] if not any(o[0] <= s[0] and s[1] <= o[1] and o[1] - o[0] > s[1] - s[0]
                                             for j, other in enumerate(spans) if j != i for o in other)]
        if own: hits.append((min(s[0] for s in own), b))
    hits.sort(key=lambda x: x[0])
    return [{"name": b["name"], "is_client": b["is_client"], "rank": i + 1} for i, (_, b) in enumerate(hits)]

SOURCE_TYPES = [("reddit", ("reddit.com",)), ("youtube", ("youtube.com", "youtu.be")), ("wikipedia", ("wikipedia.org",)),
                ("review_site", ("trustpilot.com", "mouthshut.com", "quora.com", "justdial.com")),
                ("news", ("timesofindia", "economictimes", "hindustantimes", "ndtv", "livemint", "vogue", "elle", "femina", "news"))]

def source_type(h, client_domain, competitor_domains):
    if same_site(h, client_domain): return "own_site"
    if any(same_site(h, d) for d in competitor_domains): return "competitor"
    for t, keys in SOURCE_TYPES:
        if any(k in h for k in keys): return t
    return "other"

# ------------------------------------------------------------------ the run
def tracked_brands(conn, client_id):
    name, domain, aliases = conn.execute("SELECT name, domain, aliases FROM clients WHERE client_id=%s", [client_id]).fetchone()
    br = conn.execute("SELECT brand_spellings FROM brand_rules WHERE client_id=%s", [client_id]).fetchone()
    out = [{"name": name, "aliases": list(aliases or []) + list((br or [[]])[0] or []), "domain": domain, "is_client": True}]
    for n, d, a in conn.execute("SELECT name, domain, aliases FROM competitors WHERE client_id=%s AND tracked", [client_id]):
        out.append({"name": n, "aliases": a or [], "domain": d, "is_client": False})
    return out

class OpenAIEngine:
    """ChatGPT through the OpenAI API (or the brand's own ChatGPT plan). Other engines (claude_answers.py) give the same five calls."""
    key, default_model, prompt_seconds = "chatgpt", MODEL, 0
    @staticmethod
    def ask(session, key, text, region): return ask(session, key, text, region)
    @staticmethod
    def parse(j): return parse_response(j)
    @staticmethod
    def judge(session, key, answer): return judge_brands(session, key, answer)
    @staticmethod
    def meta(j, key, judge_meta=None): return {"usage": j.get("usage"), "id": j.get("id"), "via": getattr(key, "via", None) or "api"}
    @staticmethod
    def guard(conn, client_id): return None            # a reason to stop before the next prompt (spend caps), or None

def run_prompt(conn, session, key, client_id, prompt_id, text, region, cycle, sample_no, brands, sentiment=True, eng=OpenAIEngine):
    row = conn.execute("""INSERT INTO runs (client_id, prompt_id, engine, region, sample_no, cycle, status)
                          VALUES (%s,%s,%s,%s,%s,%s,'error') ON CONFLICT (prompt_id, engine, region, cycle, sample_no) DO NOTHING
                          RETURNING run_id""", [client_id, prompt_id, eng.key, region, sample_no, cycle]).fetchone()
    if not row:                                                    # already asked this cycle: retry only if it failed
        row = conn.execute("""SELECT run_id FROM runs WHERE prompt_id=%s AND engine=%s AND region=%s AND cycle=%s AND sample_no=%s
                              AND status='error'""", [prompt_id, eng.key, region, cycle, sample_no]).fetchone()
        if not row: return "skipped"
        conn.execute("DELETE FROM answers WHERE run_id=%s", [row[0]])
    run_id = row[0]
    try:
        j = eng.ask(session, key, text, region)
        meta = eng.meta(j, key)
        answer, cited, consulted = eng.parse(j)
        judged = eng.judge(session, key, answer) if sentiment and answer else []
        if isinstance(judged, tuple): judged, jmeta = judged; meta = eng.meta(j, key, jmeta)
    except Exception as e:
        conn.execute("UPDATE runs SET status='error', error=%s, run_at=now() WHERE run_id=%s", [str(e)[:500], run_id]); return "error"
    client = brands[0]; comp_domains = [b["domain"] for b in brands[1:] if b["domain"]]
    with conn.transaction():
        answer_id = conn.execute("""INSERT INTO answers (client_id, run_id, full_text, engine_version, raw_json, parsed_at)
                                    VALUES (%s,%s,%s,%s,%s,now()) RETURNING answer_id""",
                                 [client_id, run_id, answer, j.get("model", eng.default_model), json.dumps(meta)]).fetchone()[0]
        write_mentions(conn, client_id, answer_id, answer, brands, judged)
        for urls, is_cited in (([u for u, _ in cited], True), (consulted, False)):
            titles = dict(cited)
            for u in urls:
                h = host(u)
                conn.execute("""INSERT INTO citations (client_id, answer_id, url, domain, source_type, title, cited, is_client)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                             [client_id, answer_id, u[:2000], h, source_type(h, client["domain"], comp_domains), titles.get(u), is_cited,
                              same_site(h, client["domain"])])
        conn.execute("UPDATE runs SET status='ok', error=NULL, run_at=now() WHERE run_id=%s", [run_id])
    return "ok"

def same_brand(a, b):
    """Whether two ways of writing a brand are the same brand: 'Zavya' and 'zavya.co', 'True Silver' and 'truesilver.co.in'."""
    x, y = (a or "").lower(), (b or "").lower()
    if x == y or (x and y and (x in y or y in x)): return True
    sx, sy = stem(a), stem(b)
    return len(sx) >= 4 and len(sy) >= 4 and (sx == sy or sx in sy or sy in sx)

def write_mentions(conn, client_id, answer_id, answer, brands, judged):
    """The tracked brands an answer names (place, sentiment, recommended), then every other brand it names (no place)."""
    found = find_mentions(answer, brands)
    for m in found:
        v = next((j for j in judged if same_brand(j["name"], m["name"])
                  or any(same_brand(j["name"], b["domain"]) for b in brands if b["name"] == m["name"] and b["domain"])), None)
        conn.execute("""INSERT INTO mentions (client_id, answer_id, brand, is_client, rank_in_answer, sentiment, recommended)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)""", [client_id, answer_id, m["name"], m["is_client"], m["rank"],
                                                           (v or {}).get("sentiment"), (v or {}).get("recommended")])
    for v in judged:                                          # brands we do not track yet: kept for "who else gets named"
        if not any(same_brand(v["name"], m["name"]) or
                   any(same_brand(v["name"], b["domain"]) for b in brands if b["name"] == m["name"] and b["domain"]) for m in found):
            conn.execute("""INSERT INTO mentions (client_id, answer_id, brand, is_client, rank_in_answer, sentiment, recommended)
                            VALUES (%s,%s,%s,false,NULL,%s,%s)""", [client_id, answer_id, v["name"][:200], v["sentiment"], v["recommended"]])

def rescore(conn, client_id):
    """Re-read every saved answer with the current brands and detection rules, then recompute the scores. Free: nothing is
    asked again. The judge's verdicts (sentiment, recommended) kept on the old mentions are reused."""
    brands = tracked_brands(conn, client_id)
    n = 0
    for aid, text in conn.execute("SELECT answer_id, full_text FROM answers WHERE client_id=%s", [client_id]).fetchall():
        judged = [{"name": b, "sentiment": s or "neutral", "recommended": bool(r)} for b, s, r in conn.execute(
            "SELECT brand, sentiment, recommended FROM mentions WHERE answer_id=%s AND sentiment IS NOT NULL ORDER BY rank_in_answer NULLS LAST", [aid])]
        with conn.transaction():
            conn.execute("DELETE FROM mentions WHERE answer_id=%s", [aid])
            write_mentions(conn, client_id, aid, text or "", brands, judged)
        n += 1
    for engine, region, cycle in conn.execute("""SELECT DISTINCT engine::text, region, cycle FROM runs WHERE client_id=%s AND status='ok'""",
                                              [client_id]).fetchall():
        compute_scores(conn, client_id, cycle, region, engine)
    conn.execute("""INSERT INTO score_versions (client_id, version, rescored_at) VALUES (%s,%s,now())
                    ON CONFLICT (client_id) DO UPDATE SET version=EXCLUDED.version, rescored_at=now()""", [client_id, SCORING_VERSION])
    return n

def rescore_due(conn, client_id):
    r = conn.execute("SELECT version FROM score_versions WHERE client_id=%s", [client_id]).fetchone()
    if r and r[0] >= SCORING_VERSION: return False
    return conn.execute("SELECT EXISTS (SELECT 1 FROM answers WHERE client_id=%s)", [client_id]).fetchone()[0]

def position_score(rank):
    """Prominence: DCG-style decay on the place among the brands named (1st 1.0, 2nd 0.63, 3rd 0.50, 5th 0.39, 10th 0.29)."""
    import math
    return 1 / math.log2(rank + 1) if rank else 0.0

def answer_value(a, citing=True):
    """One answer's value for one brand, 0-1. a = {mentioned, rank, recommended, sentiment, cited}."""
    W = WEIGHTS
    named = (W["prominence"] * position_score(a["rank"]) + W["endorsement"] * (1.0 if a.get("recommended") else 0.0)
             + W["sentiment"] * SENT.get(a.get("sentiment") or "neutral", 0.5)) if a["mentioned"] else 0.0
    if not citing: return named / (1 - W["authority"])
    return named + W["authority"] * (1.0 if a.get("cited") else 0.0)

def score_brand(answers, citing=True, k=PRIOR_K):
    """answers = [{mentioned, rank, recommended, sentiment, cited, weight}] for one brand -> components and the score."""
    n = len(answers)
    if not n: return None
    w = [a.get("weight", 1.0) for a in answers]
    vals = [answer_value(a, citing) for a in answers]
    sw = sum(w)
    named = [a for a in answers if a["mentioned"]]
    ranks = [a["rank"] for a in named if a["rank"]]
    raw = sum(x * y for x, y in zip(w, vals)) / sw if sw else 0.0
    score = 100 * sum(x * y for x, y in zip(w, vals)) / (sw + k) if sw + k else 0.0
    return {"samples": n, "mention_rate": len(named) / n, "position": sum(position_score(r) for r in ranks) / len(ranks) if ranks else 0.0,
            "citation_rate": sum(1 for a in answers if a.get("cited")) / n,
            "endorsement": sum(1 for a in named if a.get("recommended")) / len(named) if named else 0.0,
            "sentiment": sum(SENT.get(a.get("sentiment") or "neutral", 0.5) for a in named) / len(named) if named else None,
            "avg_position": sum(ranks) / len(ranks) if ranks else None, "raw": round(100 * raw, 2), "score": round(score, 2),
            "prominence": sum(x * position_score(a["rank"]) for x, a in zip(w, answers) if a["mentioned"])}

def evidence(conn, client_id, cycle, region, engine):
    """The newest ok answer per (prompt, sample) in the window ending on `cycle`: [(answer_id, prompt text, funnel stage)]."""
    return conn.execute("""SELECT DISTINCT ON (r.prompt_id, r.sample_no) a.answer_id, p.text, p.funnel_stage::text
                           FROM runs r JOIN answers a USING (run_id) JOIN prompts p USING (prompt_id)
                           WHERE r.client_id=%s AND r.region=%s AND r.engine::text=%s AND r.status='ok' AND p.active
                             AND r.cycle <= %s AND r.cycle > %s::date - %s
                           ORDER BY r.prompt_id, r.sample_no, r.cycle DESC, r.run_at DESC""",
                        [client_id, region, engine, cycle, cycle, WINDOW_DAYS]).fetchall()

def compute_scores(conn, client_id, cycle, region="IN", engine="chatgpt"):
    brands = tracked_brands(conn, client_id)
    ev = evidence(conn, client_id, cycle, region, engine)
    ids = [r[0] for r in ev]
    if not ids: return {}
    ment = {}
    for aid, brand, rank, sent, rec in conn.execute("""SELECT answer_id, brand, rank_in_answer, sentiment, recommended FROM mentions
                                                       WHERE answer_id = ANY(%s) AND rank_in_answer IS NOT NULL""", [ids]):
        ment[(aid, brand)] = (rank, sent, rec)
    cites = conn.execute("SELECT answer_id, domain FROM citations WHERE answer_id = ANY(%s) AND cited", [ids]).fetchall()
    citing = engine in CITING
    stage_w = {aid: STAGE.get(stage, 1.0) for aid, _, stage in ev}
    out = {}
    for b in brands:
        pats = brand_patterns(b["name"], b["aliases"], b["domain"])
        ans = []
        for aid, text, _ in ev:
            rank, sent, rec = ment.get((aid, b["name"]), (None, None, None))
            branded = any(p.search(text or "") for p in pats)
            ans.append({"mentioned": rank is not None, "rank": rank, "sentiment": sent, "recommended": rec,
                        "cited": any(a == aid and same_site(d, b["domain"]) for a, d in cites),
                        "weight": stage_w[aid] * (BRANDED if branded else 1.0), "sov_weight": stage_w[aid]})
        s = score_brand(ans, citing)
        s["sov_mass"] = sum(a["sov_weight"] * position_score(a["rank"]) for a in ans if a["mentioned"])
        out[b["name"]] = (b, s)
    total = sum(s["sov_mass"] for _, s in out.values())
    res = {}
    for name, (b, s) in out.items():
        sov = s["sov_mass"] / total if total else 0.0
        s["sov"] = sov
        key = "" if b["is_client"] else b["name"]
        conn.execute("""INSERT INTO scores_daily (client_id, engine, region, date, brand, samples, mention_rate, citation_rate, share_of_voice,
                          avg_position, sentiment, visibility_score) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (client_id, engine, region, date, brand) DO UPDATE SET samples=EXCLUDED.samples, mention_rate=EXCLUDED.mention_rate,
                          citation_rate=EXCLUDED.citation_rate, share_of_voice=EXCLUDED.share_of_voice, avg_position=EXCLUDED.avg_position,
                          sentiment=EXCLUDED.sentiment, visibility_score=EXCLUDED.visibility_score""",
                     [client_id, engine, region, cycle, key, s["samples"], s["mention_rate"], s["citation_rate"], sov,
                      s["avg_position"], s["sentiment"], s["score"]])
        res[name] = s
    print(f"[ovs] {client_id} {engine} {region} {cycle} answers={len(ids)} " +
          " | ".join(f"{n}: {v['score']} (named {round(100 * v['mention_rate'])}%, sov {round(100 * v['sov'])}%)" for n, v in res.items()), flush=True)
    return res

def sync_client(conn, client_id, key, samples=1, cycle=None, session=None, sentiment=True, deadline=None, eng=OpenAIEngine):
    """deadline (time.time()): stop starting new prompts after it; tally['more'] = 1 tells the caller to run again
    (prompts already asked this cycle are skipped, so the next run picks up where this one stopped).
    eng: the engine (OpenAIEngine for ChatGPT; claude_answers.ClaudeEngine for Claude). tally['stopped'] = why a spend cap stopped it."""
    import time
    cycle = cycle or dt.date.today(); session = session or requests.Session()
    brands = tracked_brands(conn, client_id)
    prompts = conn.execute("SELECT prompt_id, text, region FROM prompts WHERE client_id=%s AND approved AND active ORDER BY created_at",
                           [client_id]).fetchall()
    tally = {}
    for pid, text, region in prompts:
        if deadline and time.time() + getattr(eng, "prompt_seconds", 0) > deadline:
            tally["more"] = 1; break
        why = eng.guard(conn, client_id)
        if why: tally["stopped"] = why; break
        for n in range(1, samples + 1):
            res = run_prompt(conn, session, key, client_id, pid, text, region, cycle, n, brands, sentiment, eng)
            tally[res] = tally.get(res, 0) + 1
        if res == "ok":
            compute_scores(conn, client_id, cycle, region, eng.key)       # the dashboard moves after every answer, even if a run is cut off
    regions = {r for _, _, r in prompts}
    scores = {r: compute_scores(conn, client_id, cycle, r, eng.key) for r in regions}
    return tally, scores

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True); ap.add_argument("--samples", type=int, default=1); ap.add_argument("--date")
    a = ap.parse_args()
    with connect() as conn:
        tally, scores = sync_client(conn, a.client_id, os.environ["OPENAI_API_KEY_2"], a.samples,
                                    dt.date.fromisoformat(a.date) if a.date else None, sentiment=os.environ.get("AI_SENTIMENT", "1") != "0")
        print("runs:", tally)
        for region, s in scores.items():
            for brand, v in s.items(): print(f"{region} {brand}: {v['score']} ({band(v['score'])})")
        return 1 if tally.get("error") else 0

if __name__ == "__main__":
    sys.exit(main())
